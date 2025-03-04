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

# Set up logging with DEBUG level for detailed output
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("bg_removal_api")

# Initialize FastAPI app
app = FastAPI(
    title="Background Removal API",
    description="API for managing vendor images with folders and optional background removal using Cloudinary",
    version="1.0.0",
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/static", StaticFiles(directory=settings.STATIC_DIR), name="static")

# Lazy initialization for Cloudinary and MongoDB
cloudinary_initialized = False
mongodb_initialized = False

def init_cloudinary():
    global cloudinary_initialized
    if not cloudinary_initialized:
        try:
            cloudinary.config(
                cloud_name=settings.CLOUDINARY_NAME,
                api_key=settings.CLOUDINARY_API_KEY,
                api_secret=settings.CLOUDINARY_API_SECRET
            )
            cloudinary_initialized = True
            logger.debug("Cloudinary initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Cloudinary: {str(e)}")
            raise

def init_mongodb():
    global mongodb_initialized
    if not mongodb_initialized:
        try:
            # Minimal operation to verify connection
            db.images_collection.count_documents({'_id': {'$exists': True}}, limit=1)
            mongodb_initialized = True
            logger.debug("MongoDB connection verified")
        except Exception as e:
            logger.error(f"Failed to connect to MongoDB: {str(e)}")
            raise

# Ensure the temp directory exists without blocking startup
if not os.path.exists(settings.TEMP_DIR):
    try:
        os.makedirs(settings.TEMP_DIR)
        logger.debug(f"Created temp directory: {settings.TEMP_DIR}")
    except Exception as e:
        logger.error(f"Failed to create temp directory {settings.TEMP_DIR}: {str(e)}")

# Minimal startup event to log readiness
@app.on_event("startup")
async def startup_event():
    start_time = time.time()
    logger.debug("Application startup started")
    # Defer all external service initialization to first request
    logger.debug("Application startup completed")
    logger.debug(f"Startup took {time.time() - start_time:.2f} seconds")

@app.get("/")
async def root():
    logger.debug("Received request to root route")
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
    logger.debug("Received request to health route")
    return {"status": "healthy"}

@app.post("/remove-background")
async def remove_background_endpoint(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    vendor_id: str = Query(..., description="Vendor ID to associate with the image"),
    remove_bg: bool = Query(False, description="Whether to remove the background"),
    folder_id: str = Query(None, description="Folder ID to store the image in (optional)"),
):
    logger.debug(f"Received request to /remove-background for vendor {vendor_id}, remove_bg={remove_bg}, folder_id={folder_id}")
    try:
        start_time = time.time()
        image_data = await file.read()
        image_size = len(image_data) / 1024
        logger.debug(f"Image size: {image_size}KB")

        image_hash = str(uuid.uuid4())
        temp_path = os.path.join(settings.TEMP_DIR, f"input_{image_hash}.png")

        # Process the image if background removal is requested
        result = image_data if not remove_bg else await remove_background(image_data, image_hash)
        logger.debug(f"Image processing result: {result}")

        # Save the input image temporarily
        with open(temp_path, "wb") as f:
            f.write(result)
        logger.debug(f"Saved temporary image to {temp_path}")

        # Initialize Cloudinary and MongoDB if not already initialized
        init_cloudinary()
        init_mongodb()

        # Upload to Cloudinary
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
        logger.debug(f"Uploaded image to Cloudinary: {upload_result}")

        image_url = upload_result["secure_url"]
        public_id = upload_result["public_id"]

        # Clean up temporary file
        background_tasks.add_task(cleanup_temp_files, temp_path)
        logger.debug(f"Scheduled cleanup for {temp_path}")

        # Validate folder_id if provided
        if folder_id:
            try:
                folder = db.folders_collection.find_one(
                    {"_id": ObjectId(folder_id), "vendor_id": vendor_id})
                if not folder:
                    raise HTTPException(
                        status_code=404, detail="Folder not found or unauthorized")
                logger.debug(f"Validated folder_id {folder_id}")
            except Exception as e:
                logger.error(f"Error validating folder_id {folder_id}: {str(e)}")
                raise HTTPException(status_code=400, detail=f"Invalid folder_id: {str(e)}")

        # Store metadata in MongoDB
        metadata = ImageMetadata(
            url=image_url,
            filename=file.filename,
            vendor_id=vendor_id,
            processed=remove_bg,
            public_id=public_id,
            created_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            _id=str(ObjectId()),
            folder_id=str(ObjectId(folder_id)) if folder_id else None
        )
        try:
            result = db.images_collection.insert_one(metadata.dict())
            image_id = str(result.inserted_id)
            logger.debug(f"Stored metadata in MongoDB with image_id {image_id}")
        except DuplicateKeyError as e:
            logger.error(f"Failed to store metadata for {file.filename}: {str(e)}")
            raise HTTPException(status_code=500, detail="Failed to store image metadata")

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
        logger.error(f"Error processing {file.filename} for vendor {vendor_id}: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to process image: {str(e)}"}
        )

@app.get("/vendor-images/{vendor_id}")
async def get_vendor_images(vendor_id: str):
    logger.debug(f"Received request to /vendor-images/{vendor_id}")
    try:
        init_mongodb()
        vendor_images = list(db.images_collection.find({"vendor_id": vendor_id}))
        serialized_images = [
            {
                **image,
                "_id": str(image["_id"]) if "_id" in image else None,
                "folder_id": str(image["folder_id"]) if "folder_id" in image and image["folder_id"] else None,
            }
            for image in vendor_images
        ]
        logger.debug(f"Found {len(serialized_images)} images for vendor {vendor_id}")
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
    logger.debug(f"Received request to /vendor-images/{vendor_id}/{folder_id}")
    try:
        init_mongodb()
        oid = ObjectId(folder_id)
        folder = db.folders_collection.find_one(
            {"_id": oid, "vendor_id": vendor_id})
        if not folder:
            raise HTTPException(
                status_code=404, detail="Folder not found or unauthorized")

        folder_images = list(db.images_collection.find(
            {"vendor_id": vendor_id, "folder_id": str(oid)}))
        serialized_images = [
            {
                **image,
                "_id": str(image["_id"]) if "_id" in image else None,
            }
            for image in folder_images
        ]
        logger.debug(f"Found {len(serialized_images)} images in folder {folder_id} for vendor {vendor_id}")
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
    logger.debug(f"Received request to /vendor-folders/{vendor_id}")
    try:
        init_mongodb()
        folders = list(db.folders_collection.find({"vendor_id": vendor_id}))
        serialized_folders = [
            {
                **folder,
                "_id": str(folder["_id"]) if "_id" in folder else None,
            }
            for folder in folders
        ]
        logger.debug(f"Found {len(serialized_folders)} folders for vendor {vendor_id}")
        return JSONResponse(
            content={"folders": serialized_folders},
            status_code=200,
            media_type="application/json"
        )
    except Exception as e:
        logger.error(f"Error fetching folders for vendor {vendor_id}: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to fetch folders: {str(e)}"}
        )

@app.post("/vendor-folders/{vendor_id}")
async def create_vendor_folder(vendor_id: str, folder: Folder):
    logger.debug(f"Received request to create folder for vendor {vendor_id}")
    try:
        init_mongodb()
        folder_data = folder.dict(exclude={"_id"})
        folder_data["vendor_id"] = vendor_id
        folder_data["created_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        result = db.folders_collection.insert_one(folder_data)
        folder_id = str(result.inserted_id)
        logger.debug(f"Created folder with ID {folder_id} for vendor {vendor_id}")
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
    logger.debug(f"Received request to delete folder {folder_id}")
    try:
        init_mongodb()
        oid = ObjectId(folder_id)
        folder = db.folders_collection.find_one({"_id": oid})
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")

        vendor_id = folder["vendor_id"]

        # Delete all images in the folder from Cloudinary and MongoDB
        folder_images = list(db.images_collection.find({"folder_id": oid}))
        for image in folder_images:
            init_cloudinary()
            cloudinary.uploader.destroy(
                image["public_id"],
                resource_type="image"
            )
            db.images_collection.delete_one({"_id": image["_id"]})
        logger.debug(f"Deleted {len(folder_images)} images from folder {folder_id}")

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
    logger.debug(f"Received request to delete image {image_id}")
    try:
        init_mongodb()
        try:
            oid = ObjectId(image_id)
        except:
            raise HTTPException(
                status_code=400, detail="Invalid image ID format")

        image = db.images_collection.find_one({"_id": oid})
        if not image:
            raise HTTPException(status_code=404, detail="Image not found")

        init_cloudinary()
        cloudinary.uploader.destroy(
            image["public_id"],
            resource_type="image"
        )
        logger.debug(f"Deleted image {image_id} from Cloudinary")

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