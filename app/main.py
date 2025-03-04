import logging
import os
import time
import uuid
from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks, Query
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import cloudinary
import cloudinary.uploader
import cloudinary.api
from pymongo.errors import DuplicateKeyError
from bson.objectid import ObjectId

from .core import remove_background
from .utils import cleanup_temp_files
from .settings import settings
from .database import db
from .models import ImageMetadata, Folder

# Set up logging
logger = logging.getLogger("bg_removal_api")

# Initialize FastAPI app
app = FastAPI(
    title="Background Removal API",
    description="API for managing vendor images with folders and optional background removal using Cloudinary",
    version="1.0.0",
)

# Configure Cloudinary
cloudinary.config(
    cloud_name=settings.CLOUDINARY_NAME,
    api_key=settings.CLOUDINARY_API_KEY,
    api_secret=settings.CLOUDINARY_API_SECRET
)

app.mount("/static", StaticFiles(directory=settings.STATIC_DIR), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {
        "service": "Background Removal API",
        "status": "operational",
        "endpoints": [
            {"path": "/", "method": "GET", "description": "Service information"},
            {"path": "/health", "method": "GET", "description": "Health check"},
            {"path": "/remove-background", "method": "POST",
                "description": "Upload and optionally remove background from vendor image"},
            {"path": "/vendor-images/{vendor_id}", "method": "GET",
                "description": "Get all images for a vendor"},
            {"path": "/vendor-images/{vendor_id}/{folder_id}", "method": "GET",
                "description": "Get images for a vendor in a specific folder"},
            {"path": "/vendor-folders/{vendor_id}", "method": "GET",
                "description": "Get all folders for a vendor"},
            {"path": "/vendor-folders/{vendor_id}", "method": "POST",
                "description": "Create a folder for a vendor"},
            {"path": "/folders/{folder_id}", "method": "DELETE",
                "description": "Delete a folder and its images"},
            {"path": "/images/{image_id}", "method": "DELETE",
                "description": "Delete a specific image by ID"},
        ]
    }


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


@app.post("/remove-background")
async def remove_background_endpoint(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    vendor_id: str = Query(...,
                           description="Vendor ID to associate with the image"),
    remove_bg: bool = Query(
        False, description="Whether to remove the background"),
    folder_id: str = Query(
        None, description="Folder ID to store the image in (optional)"),
):
    try:
        start_time = time.time()
        image_data = await file.read()
        image_size = len(image_data) / 1024
        print("🚀 ~ image_size:", image_size)

        image_hash = str(uuid.uuid4())
        temp_path = os.path.join(settings.TEMP_DIR, f"input_{image_hash}.png")

        # Process the image if background removal is requested
        result = image_data if not remove_bg else await remove_background(image_data, image_hash)
        print("🚀 ~ result:", result)
# Save the input image temporarily
        with open(temp_path, "wb") as f:
            f.write(result)

        # Upload to Cloudinary, explicitly setting format to PNG and preserving transparency
        upload_result = cloudinary.uploader.upload(
            temp_path,
            public_id=f"vendor_{vendor_id}_{image_hash}",
            overwrite=True,
            resource_type="image",
            format="png",
            quality="auto:best",
            fetch_format="png",
            transformation=[{"flags": "preserve_transparency"}]
        )

        # Generate the Cloudinary URL and public_id
        image_url = upload_result["secure_url"]
        public_id = upload_result["public_id"]

        # Clean up temporary file
        background_tasks.add_task(cleanup_temp_files, temp_path)

        # Validate folder_id if provided
        if folder_id:
            folder = db.folders_collection.find_one(
                {"_id": ObjectId(folder_id), "vendor_id": vendor_id})
            if not folder:
                raise HTTPException(
                    status_code=404, detail="Folder not found or unauthorized")

        # Store metadata in MongoDB
        metadata = ImageMetadata(
            url=image_url,
            filename=file.filename,
            vendor_id=vendor_id,
            processed=remove_bg,
            public_id=public_id,
            created_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            # Generate a new ObjectId as string for JSON response
            _id=str(ObjectId()),
            # Convert to string for consistency
            folder_id=str(ObjectId(folder_id)) if folder_id else None
        )
        try:
            result = db.images_collection.insert_one(metadata.dict())
            image_id = str(result.inserted_id)  # Get MongoDB _id as image_id
        except DuplicateKeyError as e:
            logger.error(
                f"Failed to store metadata for {file.filename}: {str(e)}")
            raise HTTPException(
                status_code=500, detail="Failed to store image metadata")

        processing_time = time.time() - start_time
        logger.info(
            f"Processed image for vendor {vendor_id}: {file.filename}, "
            f"Size: {image_size:.2f}KB, "
            f"Time: {processing_time:.2f}s, "
            f"Background Removed: {remove_bg}, "
            f"Folder: {folder_id or 'None'}"
        )

        return JSONResponse(
            content={"url": image_url, "vendor_id": vendor_id,
                     "processed": remove_bg, "image_id": image_id, "folder_id": folder_id},
            status_code=200,
            media_type="application/json"
        )
    except Exception as e:
        logger.error(
            f"Error processing {file.filename} for vendor {vendor_id}: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to process image: {str(e)}"}
        )


@app.get("/vendor-images/{vendor_id}")
async def get_vendor_images(vendor_id: str):
    try:
        vendor_images = list(
            db.images_collection.find({"vendor_id": vendor_id}))
        # Convert ObjectId to string for JSON serialization
        serialized_images = [
            {
                **image,
                "_id": str(image["_id"]) if "_id" in image else None,
                "folder_id": str(image["folder_id"]) if "folder_id" in image and image["folder_id"] else None,
            }
            for image in vendor_images
        ]
        return JSONResponse(
            content={"images": serialized_images},
            status_code=200,
            media_type="application/json"
        )
    except Exception as e:
        logger.error(f"Error fetching images for vendor {vendor_id}: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to fetch images: {str(e)}"}
        )


@app.get("/vendor-images/{vendor_id}/{folder_id}")
async def get_folder_images(vendor_id: str, folder_id: str):
    try:
        # Convert folder_id to ObjectId for MongoDB query
        oid = ObjectId(folder_id)
        folder = db.folders_collection.find_one(
            {"_id": oid, "vendor_id": vendor_id})
        if not folder:
            raise HTTPException(
                status_code=404, detail="Folder not found or unauthorized")

        folder_images = list(db.images_collection.find(
            {"vendor_id": vendor_id, "folder_id": str(oid)}))
        # Convert ObjectId to string for JSON serialization
        serialized_images = [
            {
                **image,
                "_id": str(image["_id"]) if "_id" in image else None,
            }
            for image in folder_images
        ]
        return JSONResponse(
            content={"images": serialized_images,
                     "folder_name": folder["folder_name"]},
            status_code=200,
            media_type="application/json"
        )
    except Exception as e:
        logger.error(
            f"Error fetching images for folder {folder_id} of vendor {vendor_id}: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to fetch folder images: {str(e)}"}
        )


@app.get("/vendor-folders/{vendor_id}")
async def get_vendor_folders(vendor_id: str):
    try:
        folders = list(db.folders_collection.find({"vendor_id": vendor_id}))
        # Convert ObjectId to string for JSON serialization
        serialized_folders = [
            {
                **folder,
                "_id": str(folder["_id"]) if "_id" in folder else None,
            }
            for folder in folders
        ]
        return JSONResponse(
            content={"folders": serialized_folders},
            status_code=200,
            media_type="application/json"
        )
    except Exception as e:
        logger.error(
            f"Error fetching folders for vendor {vendor_id}: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to fetch folders: {str(e)}"}
        )


@app.post("/vendor-folders/{vendor_id}")
async def create_vendor_folder(vendor_id: str, folder: Folder):
    try:
        folder_data = folder.dict(exclude={"_id"})
        folder_data["vendor_id"] = vendor_id
        folder_data["created_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        result = db.folders_collection.insert_one(folder_data)
        folder_id = str(result.inserted_id)
        return JSONResponse(
            content={"folder_id": folder_id,
                     "folder_name": folder.folder_name, "vendor_id": vendor_id},
            status_code=201,
            media_type="application/json"
        )
    except Exception as e:
        logger.error(f"Error creating folder for vendor {vendor_id}: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to create folder: {str(e)}"}
        )


@app.delete("/folders/{folder_id}")
async def delete_folder(folder_id: str):
    try:
        # Convert folder_id to ObjectId for MongoDB query
        oid = ObjectId(folder_id)
        folder = db.folders_collection.find_one({"_id": oid})
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")

        vendor_id = folder["vendor_id"]

        # Delete all images in the folder from Cloudinary and MongoDB
        folder_images = list(db.images_collection.find({"folder_id": oid}))
        for image in folder_images:
            cloudinary.uploader.destroy(
                image["public_id"],
                resource_type="image"
            )
            db.images_collection.delete_one({"_id": image["_id"]})

        # Delete the folder from MongoDB
        result = db.folders_collection.delete_one({"_id": oid})
        if result.deleted_count == 0:
            raise HTTPException(
                status_code=404, detail="Folder not found in database")

        logger.info(
            f"Deleted folder {folder_id} and its images for vendor {vendor_id}")
        return JSONResponse(
            content={"message": "Folder and its images deleted successfully"},
            status_code=200,
            media_type="application/json"
        )
    except Exception as e:
        logger.error(f"Error deleting folder {folder_id}: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to delete folder: {str(e)}"}
        )


@app.delete("/images/{image_id}")
async def delete_image(image_id: str):
    try:
        # Convert string image_id to ObjectId for MongoDB query
        try:
            oid = ObjectId(image_id)
        except:
            raise HTTPException(
                status_code=400, detail="Invalid image ID format")

        # Find the image metadata in MongoDB
        image = db.images_collection.find_one({"_id": oid})
        if not image:
            raise HTTPException(status_code=404, detail="Image not found")

        # Delete the image from Cloudinary using the public_id
        cloudinary.uploader.destroy(
            image["public_id"],
            resource_type="image"
        )

        # Remove the metadata from MongoDB
        result = db.images_collection.delete_one({"_id": oid})
        if result.deleted_count == 0:
            raise HTTPException(
                status_code=404, detail="Image not found in database")

        logger.info(
            f"Deleted image with ID {image_id} for vendor {image['vendor_id']} from Cloudinary and MongoDB")
        return JSONResponse(
            content={"message": "Image deleted successfully"},
            status_code=200,
            media_type="application/json"
        )
    except Exception as e:
        logger.error(f"Error deleting image {image_id}: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to delete image: {str(e)}"}
        )

if not os.path.exists(settings.TEMP_DIR):
    os.makedirs(settings.TEMP_DIR)
