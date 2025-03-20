import logging
import os
import time
import uuid
from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks, Query, Depends, Body, Form
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import cloudinary
import cloudinary.uploader
import cloudinary.api
from pymongo.errors import DuplicateKeyError
from bson.objectid import ObjectId
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import shutil
import json

from .core import remove_background
from .utils import cleanup_temp_files
from .settings import settings
from .database import db
from .models import (ImageMetadata, Folder, ImageMetadata,
                     ImageSearchResult,
                     PaginatedImageMetadata,
                     PaginatedImageSearchResults,
                     ImageProcessingOptions,
                     FilterOptions)

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
            db.images_collection.count_documents(
                {'_id': {'$exists': True}}, limit=1)
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
        logger.error(
            f"Failed to create temp directory {settings.TEMP_DIR}: {str(e)}")

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


@app.get("/api/images/search", response_model=PaginatedImageSearchResults)
async def search_images(
    q: str = Query("", description="Search query"),
    categories: List[str] = Query(None, description="Filter by categories"),
    tags: List[str] = Query(None, description="Filter by tags"),
    formats: List[str] = Query(None, description="Filter by formats"),
    start_date: Optional[datetime] = Query(
        None, description="Start date for filter"),
    end_date: Optional[datetime] = Query(
        None, description="End date for filter"),
    page: int = Query(1, description="Page number", ge=1),
    limit: int = Query(20, description="Results per page", ge=1, le=100),
):
    """
    Search for images based on query and filters
    """
    try:
        init_mongodb()

        # Build the query
        query = {"title": {"$regex": q, "$options": "i"}}

        # Apply filters
        if categories:
            query["category"] = {"$in": categories}
        if tags:
            query["tags"] = {"$in": tags}
        if formats:
            query["format"] = {"$in": formats}

        # Apply date range filter
        if start_date or end_date:
            date_query = {}
            if start_date:
                date_query["$gte"] = start_date
            if end_date:
                date_query["$lte"] = end_date
            if date_query:
                query["uploadedAt"] = date_query

        # Get total count
        total = db.images_collection.count_documents(query)

        # Get paginated results
        results = list(db.images_collection.find(query)
                       .skip((page - 1) * limit)
                       .limit(limit))

        # Transform results to match ImageSearchResult
        search_results = []
        for img in results:
            search_results.append(
                ImageSearchResult(
                    id=str(img["_id"]),
                    title=img.get("title", "Untitled"),
                    description=img.get("description"),
                    tags=img.get("tags", []),
                    category=img.get("category"),
                    thumbnailUrl=img.get("thumbnailUrl", img.get("url")),
                    url=img.get("url"),
                    dimensions=img.get(
                        "dimensions", {"width": 0, "height": 0}),
                    format=img.get("format", "unknown")
                )
            )

        return {
            "results": search_results,
            "total": total
        }

    except Exception as e:
        logger.error(f"Error searching images: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Failed to search images: {str(e)}")


@app.get("/api/seller/image-bucket", response_model=PaginatedImageMetadata)
async def get_bucket_images(
    vendor_id: str = Query(..., description="Vendor ID"),
    page: int = Query(1, description="Page number", ge=1),
    limit: int = Query(50, description="Results per page", ge=1, le=100),
    sortBy: str = Query("uploadedAt", description="Sort by field"),
    sortOrder: str = Query("desc", description="Sort order (asc or desc)")
):
    """
    Get all images for a vendor with pagination and sorting
    """
    try:
        init_mongodb()

        # Build the query
        query = {"vendor_id": vendor_id}

        # Get total count
        total = db.images_collection.count_documents(query)

        # Set up sorting
        sort_direction = -1 if sortOrder == "desc" else 1
        sort_field = sortBy

        # Get paginated results
        results = list(db.images_collection.find(query)
                       .sort(sort_field, sort_direction)
                       .skip((page - 1) * limit)
                       .limit(limit))

        # Transform results to match frontend expected type
        images = []
        for img in results:
            images.append(
                ImageMetadata(
                    id=str(img["_id"]),
                    title=img.get("filename", "Untitled"),
                    description=img.get("description"),
                    tags=img.get("tags", []),
                    category=img.get("category"),
                    uploadedAt=img.get("created_at", datetime.now()),
                    size=img.get("size", 0),
                    dimensions=img.get(
                        "dimensions", {"width": 0, "height": 0}),
                    format=img.get("format", "unknown"),
                    url=img.get("url"),
                    thumbnailUrl=img.get("thumbnailUrl", img.get("url")),
                    isPublic=img.get("isPublic", False),
                    vendor_id=img.get("vendor_id"),
                    processed=img.get("processed", False),
                    public_id=img.get("public_id"),
                    folder_id=img.get("folder_id")
                )
            )

        return {
            "images": images,
            "total": total
        }

    except Exception as e:
        logger.error(f"Error fetching bucket images: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch images: {str(e)}")


@app.post("/api/seller/image-bucket", response_model=ImageMetadata)
async def upload_image(
    image: UploadFile = File(...),
    metadata: str = Form(...),
    vendor_id: str = Query(..., description="Vendor ID"),
    remove_bg: bool = Query(False, description="Whether to remove background")
):
    """
    Upload a new image to the vendor's bucket
    """
    try:
        init_mongodb()
        init_cloudinary()

        # Parse metadata from JSON string
        meta_dict = json.loads(metadata)

        # Read image file
        image_data = await image.read()
        image_size = len(image_data)

        # Generate a unique hash for the image
        image_hash = str(uuid.uuid4())
        temp_path = os.path.join(settings.TEMP_DIR, f"input_{image_hash}.png")

        # Process image if background removal is requested
        if remove_bg:
            processed_data = await remove_background(image_data, image_hash)
            with open(temp_path, "wb") as f:
                f.write(processed_data)
        else:
            with open(temp_path, "wb") as f:
                f.write(image_data)

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

        # Create thumbnail
        thumbnail_result = cloudinary.uploader.upload(
            temp_path,
            public_id=f"vendor_{vendor_id}_{image_hash}_thumb",
            overwrite=True,
            resource_type="image",
            format="png",
            quality="auto:good",
            fetch_format="png",
            transformation=[
                {"width": 200, "height": 200, "crop": "fill"},
                {"flags": "preserve_transparency"}
            ]
        )

        # Clean up temp file
        if os.path.exists(temp_path) and not settings.KEEP_TEMP_FILES:
            os.remove(temp_path)

        # Create image document
        image_id = str(ObjectId())
        now = datetime.now().isoformat()

        # Get image dimensions (can be extracted from Cloudinary response)
        width = upload_result.get("width", 0)
        height = upload_result.get("height", 0)

        # Create the image metadata document
        image_doc = {
            "_id": ObjectId(image_id),
            "title": meta_dict.get("title", image.filename),
            "description": meta_dict.get("description", ""),
            "tags": meta_dict.get("tags", []),
            "category": meta_dict.get("category"),
            "uploadedAt": now,
            "size": image_size,
            "dimensions": {
                "width": width,
                "height": height
            },
            "format": "png",  # Assuming PNG format
            "url": upload_result["secure_url"],
            "thumbnailUrl": thumbnail_result["secure_url"],
            "isPublic": meta_dict.get("isPublic", False),
            "vendor_id": vendor_id,
            "processed": remove_bg,
            "public_id": upload_result["public_id"],
            "folder_id": meta_dict.get("folder_id"),
            "filename": image.filename,
            "created_at": now
        }

        # Insert into MongoDB
        db.images_collection.insert_one(image_doc)

        # Return the created image metadata
        return ImageMetadata(
            id=image_id,
            title=image_doc["title"],
            description=image_doc["description"],
            tags=image_doc["tags"],
            category=image_doc["category"],
            uploadedAt=image_doc["uploadedAt"],
            size=image_doc["size"],
            dimensions=image_doc["dimensions"],
            format=image_doc["format"],
            url=image_doc["url"],
            thumbnailUrl=image_doc["thumbnailUrl"],
            isPublic=image_doc["isPublic"],
            vendor_id=image_doc["vendor_id"],
            processed=image_doc["processed"],
            public_id=image_doc["public_id"],
            folder_id=image_doc["folder_id"]
        )
    except Exception as e:
        logger.error(f"Error uploading image: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Failed to upload image: {str(e)}")


@app.post("/api/seller/image-bucket/add", response_model=ImageMetadata)
async def add_to_bucket(
    imageId: str = Body(..., embed=True),
    vendor_id: str = Query(..., description="Vendor ID")
):
    """
    Add an existing image to the vendor's bucket
    """
    try:
        init_mongodb()

        # Find the image
        image = db.images_collection.find_one({"_id": ObjectId(imageId)})
        if not image:
            raise HTTPException(status_code=404, detail="Image not found")

        # Copy the image to the vendor's bucket
        image_copy = dict(image)
        image_copy.pop("_id")
        image_copy["vendor_id"] = vendor_id
        image_copy["_id"] = ObjectId()

        # Insert the copy
        db.images_collection.insert_one(image_copy)

        # Return the added image
        return ImageMetadata(
            id=str(image_copy["_id"]),
            title=image_copy.get("title", "Untitled"),
            description=image_copy.get("description"),
            tags=image_copy.get("tags", []),
            category=image_copy.get("category"),
            uploadedAt=image_copy.get("uploadedAt", datetime.now()),
            size=image_copy.get("size", 0),
            dimensions=image_copy.get("dimensions", {"width": 0, "height": 0}),
            format=image_copy.get("format", "unknown"),
            url=image_copy.get("url"),
            thumbnailUrl=image_copy.get("thumbnailUrl", image_copy.get("url")),
            isPublic=image_copy.get("isPublic", False),
            vendor_id=image_copy.get("vendor_id"),
            processed=image_copy.get("processed", False),
            public_id=image_copy.get("public_id"),
            folder_id=image_copy.get("folder_id")
        )
    except Exception as e:
        logger.error(f"Error adding image to bucket: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Failed to add image to bucket: {str(e)}")


@app.delete("/api/seller/image-bucket/{imageId}")
async def remove_from_bucket(
    imageId: str,
    vendor_id: str = Query(..., description="Vendor ID")
):
    """
    Remove an image from the vendor's bucket
    """
    try:
        init_mongodb()
        init_cloudinary()

        # Find the image
        image = db.images_collection.find_one(
            {"_id": ObjectId(imageId), "vendor_id": vendor_id})
        if not image:
            raise HTTPException(
                status_code=404, detail="Image not found or not owned by this vendor")

        # Delete the image from Cloudinary
        if "public_id" in image:
            cloudinary.uploader.destroy(
                image["public_id"], resource_type="image")

        # Delete the thumbnail from Cloudinary if it exists
        if "public_id" in image and not image["public_id"].endswith("_thumb"):
            cloudinary.uploader.destroy(
                f"{image['public_id']}_thumb", resource_type="image")

        # Delete from MongoDB
        db.images_collection.delete_one(
            {"_id": ObjectId(imageId), "vendor_id": vendor_id})

        return {"message": "Image removed successfully"}
    except Exception as e:
        logger.error(f"Error removing image from bucket: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Failed to remove image: {str(e)}")


@app.patch("/api/seller/image-bucket/{imageId}", response_model=ImageMetadata)
async def update_image_metadata(
    imageId: str,
    metadata: Dict[str, Any] = Body(...),
    vendor_id: str = Query(..., description="Vendor ID")
):
    """
    Update image metadata
    """
    try:
        init_mongodb()

        # Find the image
        image = db.images_collection.find_one(
            {"_id": ObjectId(imageId), "vendor_id": vendor_id})
        if not image:
            raise HTTPException(
                status_code=404, detail="Image not found or not owned by this vendor")

        # Update allowed fields only
        allowed_fields = ["title", "description",
                          "tags", "category", "isPublic", "folder_id"]
        update_data = {k: v for k, v in metadata.items()
                       if k in allowed_fields}

        # Update in MongoDB
        db.images_collection.update_one(
            {"_id": ObjectId(imageId), "vendor_id": vendor_id},
            {"$set": update_data}
        )

        # Get the updated image
        updated_image = db.images_collection.find_one(
            {"_id": ObjectId(imageId)})

        # Return the updated image metadata
        return ImageMetadata(
            id=str(updated_image["_id"]),
            title=updated_image.get("title", "Untitled"),
            description=updated_image.get("description"),
            tags=updated_image.get("tags", []),
            category=updated_image.get("category"),
            uploadedAt=updated_image.get("uploadedAt", datetime.now()),
            size=updated_image.get("size", 0),
            dimensions=updated_image.get(
                "dimensions", {"width": 0, "height": 0}),
            format=updated_image.get("format", "unknown"),
            url=updated_image.get("url"),
            thumbnailUrl=updated_image.get(
                "thumbnailUrl", updated_image.get("url")),
            isPublic=updated_image.get("isPublic", False),
            vendor_id=updated_image.get("vendor_id"),
            processed=updated_image.get("processed", False),
            public_id=updated_image.get("public_id"),
            folder_id=updated_image.get("folder_id")
        )
    except Exception as e:
        logger.error(f"Error updating image metadata: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Failed to update image metadata: {str(e)}")


@app.post("/api/images/process/{imageId}", response_model=ImageMetadata)
async def process_image(
    imageId: str,
    options: ImageProcessingOptions = Body(...),
    vendor_id: str = Query(..., description="Vendor ID")
):
    """
    Process an image (resize, convert format, etc.)
    """
    try:
        init_mongodb()
        init_cloudinary()

        # Find the image
        image = db.images_collection.find_one(
            {"_id": ObjectId(imageId), "vendor_id": vendor_id})
        if not image:
            raise HTTPException(
                status_code=404, detail="Image not found or not owned by this vendor")

        public_id = image["public_id"]

        # Build Cloudinary transformation
        transformation = []

        # Add resize transformation if requested
        if options.resize:
            resize_options = {}
            if options.resize.width:
                resize_options["width"] = options.resize.width
            if options.resize.height:
                resize_options["height"] = options.resize.height

            if options.resize.maintainAspectRatio:
                resize_options["crop"] = "scale"
            else:
                resize_options["crop"] = "fill"

            transformation.append(resize_options)

        # Add quality transformation if requested
        if options.quality:
            transformation.append({"quality": options.quality})

        # Add transparency preservation
        transformation.append({"flags": "preserve_transparency"})

        # Process the image
        format_option = options.format if options.format else "png"

        result = cloudinary.uploader.explicit(
            public_id,
            type="upload",
            resource_type="image",
            eager=[{
                "transformation": transformation,
                "format": format_option
            }]
        )

        # Get the processed image URL
        processed_url = result["eager"][0]["secure_url"]

        # Create a new image entry for the processed image
        new_image_id = str(ObjectId())
        now = datetime.now().isoformat()

        # Create a new public ID for the processed image
        new_public_id = f"{public_id}_processed_{uuid.uuid4().hex[:8]}"

        # Get dimensions from the Cloudinary result
        width = result["eager"][0].get("width", image["dimensions"]["width"])
        height = result["eager"][0].get(
            "height", image["dimensions"]["height"])

        # Create the new image metadata
        new_image = {
            "_id": ObjectId(new_image_id),
            "title": f"{image.get('title', 'Untitled')} (Processed)",
            "description": image.get("description", ""),
            "tags": image.get("tags", []),
            "category": image.get("category"),
            "uploadedAt": now,
            "size": 0,  # We don't know the size without downloading
            "dimensions": {
                "width": width,
                "height": height
            },
            "format": format_option,
            "url": processed_url,
            "thumbnailUrl": processed_url,  # Use the same URL for thumbnail
            "isPublic": image.get("isPublic", False),
            "vendor_id": vendor_id,
            "processed": True,
            "public_id": new_public_id,
            "folder_id": image.get("folder_id"),
            "filename": f"{image.get('filename', 'image')}_processed.{format_option}",
            "created_at": now
        }

        # Insert into MongoDB
        db.images_collection.insert_one(new_image)

        # Return the processed image metadata
        return ImageMetadata(
            id=new_image_id,
            title=new_image["title"],
            description=new_image["description"],
            tags=new_image["tags"],
            category=new_image["category"],
            uploadedAt=new_image["uploadedAt"],
            size=new_image["size"],
            dimensions=new_image["dimensions"],
            format=new_image["format"],
            url=new_image["url"],
            thumbnailUrl=new_image["thumbnailUrl"],
            isPublic=new_image["isPublic"],
            vendor_id=new_image["vendor_id"],
            processed=new_image["processed"],
            public_id=new_image["public_id"],
            folder_id=new_image["folder_id"]
        )
    except Exception as e:
        logger.error(f"Error processing image: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Failed to process image: {str(e)}")


@app.get("/api/seller/image-bucket/filters", response_model=FilterOptions)
async def get_filter_options(
    vendor_id: str = Query(..., description="Vendor ID")
):
    """
    Get available filter options for the vendor's bucket
    """
    try:
        init_mongodb()

        # Get unique categories
        categories = db.images_collection.distinct(
            "category", {"vendor_id": vendor_id})
        categories = [c for c in categories if c]

        # Get unique tags
        all_tags = []
        tag_cursor = db.images_collection.find(
            {"vendor_id": vendor_id, "tags": {"$exists": True}}, {"tags": 1})
        for doc in tag_cursor:
            all_tags.extend(doc.get("tags", []))
        tags = list(set(all_tags))

        # Get unique formats
        formats = db.images_collection.distinct(
            "format", {"vendor_id": vendor_id})
        formats = [f for f in formats if f]

        return FilterOptions(
            categories=categories,
            tags=tags,
            formats=formats
        )
    except Exception as e:
        logger.error(f"Error fetching filter options: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch filter options: {str(e)}")


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
    logger.debug(
        f"Received request to /remove-background for vendor {vendor_id}, remove_bg={remove_bg}, folder_id={folder_id}")
    try:
        start_time = time.time()
        image_data = await file.read()
        image_size = len(image_data)
        logger.debug(f"Image size: {image_size/1024:.2f}KB")

        image_hash = str(uuid.uuid4())
        temp_path = os.path.join(settings.TEMP_DIR, f"input_{image_hash}.png")

        # Process the image if background removal is requested
        result = image_data if not remove_bg else await remove_background(image_data, image_hash)
        logger.debug("Image processing completed")

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
        logger.debug("Uploaded image to Cloudinary")

        # Create thumbnail
        thumbnail_result = cloudinary.uploader.upload(
            temp_path,
            public_id=f"vendor_{vendor_id}_{image_hash}_thumb",
            overwrite=True,
            resource_type="image",
            format="png",
            quality="auto:good",
            fetch_format="png",
            transformation=[
                {"width": 200, "height": 200, "crop": "fill"},
                {"flags": "preserve_transparency"}
            ]
        )

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
                logger.error(
                    f"Error validating folder_id {folder_id}: {str(e)}")
                raise HTTPException(
                    status_code=400, detail=f"Invalid folder_id: {str(e)}")

        # Create image metadata following the model structure
        now = datetime.utcnow()
        image_doc = {
            "_id": ObjectId(),
            "title": file.filename,
            "description": None,
            "tags": [],
            "category": None,
            "uploadedAt": now,
            "size": image_size,
            "dimensions": {
                "width": upload_result.get("width", 0),
                "height": upload_result.get("height", 0)
            },
            "format": upload_result.get("format", "png"),
            "url": upload_result["secure_url"],
            "thumbnailUrl": thumbnail_result["secure_url"],
            "isPublic": False,
            "vendor_id": vendor_id,
            "processed": remove_bg,
            "public_id": upload_result["public_id"],
            "folder_id": folder_id,
            "filename": file.filename,
            "created_at": now.isoformat()
        }

        # Store metadata in MongoDB
        try:
            result = db.images_collection.insert_one(image_doc)
            image_id = str(result.inserted_id)
            logger.debug(
                f"Stored metadata in MongoDB with image_id {image_id}")
        except DuplicateKeyError as e:
            logger.error(
                f"Failed to store metadata for {file.filename}: {str(e)}")
            raise HTTPException(
                status_code=500, detail="Failed to store image metadata")

        processing_time = time.time() - start_time
        logger.info(
            f"Processed image for vendor {vendor_id}: {file.filename}, "
            f"Size: {image_size/1024:.2f}KB, "
            f"Time: {processing_time:.2f}s, "
            f"Background Removed: {remove_bg}, "
            f"Folder: {folder_id or 'None'}"
        )

        return JSONResponse(
            content={
                "id": image_id,
                "title": image_doc["title"],
                "description": image_doc["description"],
                "tags": image_doc["tags"],
                "category": image_doc["category"],
                "uploadedAt": image_doc["uploadedAt"].isoformat(),
                "size": image_doc["size"],
                "dimensions": image_doc["dimensions"],
                "format": image_doc["format"],
                "url": image_doc["url"],
                "thumbnailUrl": image_doc["thumbnailUrl"],
                "isPublic": image_doc["isPublic"],
                "vendor_id": image_doc["vendor_id"],
                "processed": image_doc["processed"],
                "public_id": image_doc["public_id"],
                "folder_id": image_doc["folder_id"]
            },
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


@app.get("/vendor-images/{vendor_id}", response_model=PaginatedImageMetadata)
async def get_vendor_images(
    vendor_id: str,
    page: int = Query(1, description="Page number", ge=1),
    limit: int = Query(50, description="Results per page", ge=1, le=100),
    sortBy: str = Query("uploadedAt", description="Sort by field"),
    sortOrder: str = Query("desc", description="Sort order (asc or desc)")
):
    """
    Get all images for a vendor with pagination and sorting
    """
    try:
        init_mongodb()

        # Build the query
        query = {"vendor_id": vendor_id}

        # Get total count
        total = db.images_collection.count_documents(query)

        # Set up sorting
        sort_direction = -1 if sortOrder == "desc" else 1
        sort_field = sortBy

        # Get paginated results
        results = list(db.images_collection.find(query)
                       .sort(sort_field, sort_direction)
                       .skip((page - 1) * limit)
                       .limit(limit))

        # Transform results to match frontend expected type
        images = []
        for img in results:
            # Convert datetime to ISO format string
            uploaded_at = img.get("uploadedAt")
            if isinstance(uploaded_at, datetime):
                uploaded_at = uploaded_at.isoformat()

            # Create image metadata with proper datetime handling
            image_metadata = {
                "id": str(img["_id"]),
                "title": img.get("title", "Untitled"),
                "description": img.get("description"),
                "tags": img.get("tags", []),
                "category": img.get("category"),
                "uploadedAt": uploaded_at,
                "size": img.get("size", 0),
                "dimensions": img.get("dimensions", {"width": 0, "height": 0}),
                "format": img.get("format", "unknown"),
                "url": img.get("url"),
                "thumbnailUrl": img.get("thumbnailUrl", img.get("url")),
                "isPublic": img.get("isPublic", False),
                "vendor_id": img.get("vendor_id"),
                "processed": img.get("processed", False),
                "public_id": img.get("public_id"),
                "folder_id": img.get("folder_id")
            }
            images.append(image_metadata)

        return {
            "images": images,
            "total": total
        }

    except Exception as e:
        logger.error(f"Error fetching vendor images: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch vendor images: {str(e)}"
        )


@app.get("/vendor-images/{vendor_id}/{folder_id}", response_model=PaginatedImageMetadata)
async def get_folder_images(
    vendor_id: str,
    folder_id: str,
    page: int = Query(1, description="Page number", ge=1),
    limit: int = Query(50, description="Results per page", ge=1, le=100),
    sortBy: str = Query("uploadedAt", description="Sort by field"),
    sortOrder: str = Query("desc", description="Sort order (asc or desc)")
):
    """
    Get all images for a vendor in a specific folder with pagination and sorting
    """
    try:
        init_mongodb()

        # Validate folder exists and belongs to vendor
        oid = ObjectId(folder_id)
        folder = db.folders_collection.find_one(
            {"_id": oid, "vendor_id": vendor_id})
        if not folder:
            raise HTTPException(
                status_code=404, detail="Folder not found or unauthorized")

        # Build the query
        query = {"vendor_id": vendor_id, "folder_id": str(oid)}

        # Get total count
        total = db.images_collection.count_documents(query)

        # Set up sorting
        sort_direction = -1 if sortOrder == "desc" else 1
        sort_field = sortBy

        # Get paginated results
        results = list(db.images_collection.find(query)
                       .sort(sort_field, sort_direction)
                       .skip((page - 1) * limit)
                       .limit(limit))

        # Transform results to match frontend expected type
        images = []
        for img in results:
            # Handle uploadedAt field
            uploaded_at = img.get("uploadedAt")
            if uploaded_at is None:
                # If uploadedAt is None, try to use created_at or current time
                uploaded_at = img.get("created_at")
                if uploaded_at is None:
                    uploaded_at = datetime.utcnow()
                elif isinstance(uploaded_at, str):
                    try:
                        uploaded_at = datetime.fromisoformat(
                            uploaded_at.replace('Z', '+00:00'))
                    except ValueError:
                        uploaded_at = datetime.utcnow()
            elif isinstance(uploaded_at, str):
                try:
                    uploaded_at = datetime.fromisoformat(
                        uploaded_at.replace('Z', '+00:00'))
                except ValueError:
                    uploaded_at = datetime.utcnow()

            # Create image metadata with proper datetime handling
            image_metadata = {
                "id": str(img["_id"]),
                "title": img.get("title", "Untitled"),
                "description": img.get("description"),
                "tags": img.get("tags", []),
                "category": img.get("category"),
                "uploadedAt": uploaded_at,
                "size": img.get("size", 0),
                "dimensions": img.get("dimensions", {"width": 0, "height": 0}),
                "format": img.get("format", "unknown"),
                "url": img.get("url"),
                "thumbnailUrl": img.get("thumbnailUrl", img.get("url")),
                "isPublic": img.get("isPublic", False),
                "vendor_id": img.get("vendor_id"),
                "processed": img.get("processed", False),
                "public_id": img.get("public_id"),
                "folder_id": img.get("folder_id")
            }
            images.append(image_metadata)

        return {
            "images": images,
            "total": total
        }

    except Exception as e:
        logger.error(f"Error fetching folder images: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch folder images: {str(e)}"
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
        logger.debug(
            f"Found {len(serialized_folders)} folders for vendor {vendor_id}")
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
    logger.debug(f"Received request to create folder for vendor {vendor_id}")
    try:
        init_mongodb()
        folder_data = folder.dict(exclude={"_id"})
        folder_data["vendor_id"] = vendor_id
        folder_data["created_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        result = db.folders_collection.insert_one(folder_data)
        folder_id = str(result.inserted_id)
        logger.debug(
            f"Created folder with ID {folder_id} for vendor {vendor_id}")
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
        logger.debug(
            f"Deleted {len(folder_images)} images from folder {folder_id}")

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


@app.get("/api/seller/image-bucket/stats", response_model=Dict[str, Any])
async def get_bucket_stats(
    vendor_id: str = Query(..., description="Vendor ID")
):
    """
    Get dashboard statistics for a vendor's image bucket
    """
    try:
        init_mongodb()

        # Total number of images
        total_images = db.images_collection.count_documents(
            {"vendor_id": vendor_id})

        # Processed vs unprocessed images
        processed_images = db.images_collection.count_documents(
            {"vendor_id": vendor_id, "processed": True}
        )
        unprocessed_images = total_images - processed_images

        # Total storage size (sum of all image sizes in bytes)
        pipeline = [
            {"$match": {"vendor_id": vendor_id}},
            {"$group": {"_id": None, "total_size": {"$sum": "$size"}}}
        ]
        storage_result = list(db.images_collection.aggregate(pipeline))
        total_storage = storage_result[0]["total_size"] if storage_result else 0

        # Folder distribution
        folder_pipeline = [
            {"$match": {"vendor_id": vendor_id}},
            {"$group": {"_id": "$folder_id", "count": {"$sum": 1}}}
        ]
        folder_distribution = list(
            db.images_collection.aggregate(folder_pipeline))
        folder_stats = {
            "no_folder": sum(1 for f in folder_distribution if f["_id"] is None),
            "by_folder": [
                {
                    "folder_id": str(f["_id"]) if f["_id"] else None,
                    "count": f["count"],
                    "name": db.folders_collection.find_one({"_id": ObjectId(f["_id"])})["folder_name"]
                    if f["_id"] else "No Folder"
                }
                for f in folder_distribution
            ]
        }

        # Format distribution
        format_pipeline = [
            {"$match": {"vendor_id": vendor_id}},
            {"$group": {"_id": "$format", "count": {"$sum": 1}}}
        ]
        format_distribution = {
            f["_id"]: f["count"] for f in db.images_collection.aggregate(format_pipeline) if f["_id"]
        }

        # Monthly upload trend (last 12 months)
        monthly_trend_pipeline = [
            {"$match": {"vendor_id": vendor_id}},
            {
                "$group": {
                    "_id": {
                        "year": {"$year": {"$toDate": "$created_at"}},
                        "month": {"$month": {"$toDate": "$created_at"}}
                    },
                    "count": {"$sum": 1}
                }
            },
            {"$sort": {"_id.year": -1, "_id.month": -1}},
            {"$limit": 12}
        ]
        monthly_trend = [
            {
                "year": t["_id"]["year"],
                "month": t["_id"]["month"],
                "count": t["count"]
            }
            for t in db.images_collection.aggregate(monthly_trend_pipeline)
        ]

        stats = {
            "total_images": total_images,
            "processed_images": processed_images,
            "unprocessed_images": unprocessed_images,
            "total_storage_bytes": total_storage,
            "total_storage_mb": total_storage / (1024 * 1024),  # Convert to MB
            "folder_distribution": folder_stats,
            "format_distribution": format_distribution,
            "monthly_upload_trend": monthly_trend
        }

        logger.debug(f"Generated stats for vendor {vendor_id}: {stats}")
        return stats

    except Exception as e:
        logger.error(f"Error fetching stats for vendor {vendor_id}: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch bucket stats: {str(e)}")

# Optional: Add a route for real-time stats if needed (e.g., for active uploads)


@app.get("/api/seller/image-bucket/realtime-stats", response_model=Dict[str, Any])
async def get_realtime_stats(
    vendor_id: str = Query(..., description="Vendor ID")
):
    """
    Get real-time statistics (e.g., recent uploads, processing status)
    """
    try:
        init_mongodb()

        # Recent uploads (last 24 hours)
        recent_uploads = db.images_collection.count_documents({
            "vendor_id": vendor_id,
            "created_at": {"$gte": (datetime.now() - timedelta(hours=24)).isoformat()}
        })

        # Images currently being processed (assuming processed=False for in-progress)
        processing_images = db.images_collection.count_documents({
            "vendor_id": vendor_id,
            "processed": False
        })

        stats = {
            "recent_uploads_24h": recent_uploads,
            "processing_images": processing_images,
            "timestamp": datetime.now().isoformat()
        }

        logger.debug(
            f"Generated real-time stats for vendor {vendor_id}: {stats}")
        return stats

    except Exception as e:
        logger.error(
            f"Error fetching real-time stats for vendor {vendor_id}: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch real-time stats: {str(e)}")
