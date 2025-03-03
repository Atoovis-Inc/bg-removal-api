from pydantic import BaseModel
from typing import Optional


class Folder(BaseModel):
    folder_name: str
    vendor_id: str
    created_at: Optional[str] = None
    _id: Optional[str] = None  # MongoDB ID

    class Config:
        allow_population_by_field_name = True
        arbitrary_types_allowed = True


class ImageMetadata(BaseModel):
    url: str  # Cloudinary URL
    filename: str
    vendor_id: str
    processed: bool
    public_id: str  # Cloudinary public ID
    created_at: str
    _id: str  # MongoDB ID for deletion
    folder_id: Optional[str] = None  # Link to folder (optional)

    class Config:
        allow_population_by_field_name = True
        arbitrary_types_allowed = True
