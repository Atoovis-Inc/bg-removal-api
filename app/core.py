import os
import time
import uuid
import logging
from io import BytesIO
from typing import Optional
from functools import lru_cache
import asyncio

from .settings import settings

logger = logging.getLogger("bg_removal_api")

# Semaphore to limit concurrent processing
processing_semaphore = asyncio.Semaphore(settings.MAX_WORKERS)

@lru_cache(maxsize=settings.CACHE_SIZE)
def get_cached_image(image_hash: str) -> Optional[bytes]:
    cache_path = os.path.join(settings.TEMP_DIR, f"{image_hash}.png")
    if os.path.exists(cache_path):
        with open(cache_path, "rb") as f:
            return f.read()
    return None

def save_to_cache(image_hash: str, image_data: bytes) -> None:
    if settings.KEEP_TEMP_FILES:
        cache_path = os.path.join(settings.TEMP_DIR, f"{image_hash}.png")
        with open(cache_path, "wb") as f:
            f.write(image_data)

async def remove_background(image_data: bytes, image_hash: str = None) -> bytes:
    # Defer imports until the function is called
    from PIL import Image
    import rembg

    async with processing_semaphore:
        try:
            if not image_hash:
                image_hash = str(uuid.uuid4())

            cached = get_cached_image(image_hash)
            if cached:
                logger.info(f"Cache hit for image {image_hash}")
                return cached

            start_time = time.time()
            input_image = Image.open(BytesIO(image_data))

            # Remove background using rembg
            output_image = rembg.remove(
                input_image,
                alpha_matting=True,
                alpha_matting_foreground_threshold=240,
                alpha_matting_background_threshold=10,
            )

            # Ensure the output image is in PNG format (RGBA mode if it has transparency)
            if output_image.mode != "RGBA":
                output_image = output_image.convert("RGBA")

            # Save to BytesIO as PNG explicitly
            output_buffer = BytesIO()
            output_image.save(
                output_buffer,
                format="PNG",  # Explicitly specify PNG format
                quality=settings.OUTPUT_QUALITY,
                optimize=True
            )
            output_data = output_buffer.getvalue()

            save_to_cache(image_hash, output_data)

            processing_time = time.time() - start_time
            logger.info(
                f"Processed image {image_hash} in {processing_time:.2f}s")

            return output_data
        except Exception as e:
            logger.error(f"Error processing image: {str(e)}")
            raise