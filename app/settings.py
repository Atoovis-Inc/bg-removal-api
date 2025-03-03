import os
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class Settings(BaseModel):
    MONGODB_URI: str = os.getenv("MONGODB_URI")
    CLOUDINARY_API_KEY: str = os.getenv("CLOUDINARY_API_KEY")
    CLOUDINARY_API_SECRET: str = os.getenv("CLOUDINARY_API_SECRET")
    CLOUDINARY_NAME: str = os.getenv("CLOUDINARY_NAME")
    CACHE_SIZE: int = int(os.getenv("CACHE_SIZE", "1000"))
    OUTPUT_QUALITY: int = int(os.getenv("OUTPUT_QUALITY", "95"))
    MAX_WORKERS: int = int(os.getenv("MAX_WORKERS", "4"))
    TEMP_DIR: str = os.getenv("TEMP_DIR", "tmp")
    KEEP_TEMP_FILES: bool = os.getenv(
        "KEEP_TEMP_FILES", "False").lower() == "true"
    STATIC_DIR: str = os.getenv("STATIC_DIR", "static")


settings = Settings()

# Ensure directories exist
os.makedirs(settings.TEMP_DIR, exist_ok=True)
os.makedirs(settings.STATIC_DIR, exist_ok=True)
