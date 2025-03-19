from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Union
from datetime import datetime


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


class ImageDimensions(BaseModel):
    width: int
    height: int


class DateRangeFilter(BaseModel):
    start: Optional[datetime] = None
    end: Optional[datetime] = None


class ImageFilters(BaseModel):
    categories: List[str] = []
    tags: List[str] = []
    formats: List[str] = []
    dateRange: Optional[DateRangeFilter] = None


class ResizeOptions(BaseModel):
    width: Optional[int] = None
    height: Optional[int] = None
    maintainAspectRatio: Optional[bool] = True


class ImageProcessingOptions(BaseModel):
    resize: Optional[ResizeOptions] = None
    format: Optional[str] = None
    quality: Optional[int] = Field(None, ge=1, le=100)
    compress: Optional[bool] = None


class ImageMetadata(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    tags: List[str] = []
    category: Optional[str] = None
    uploadedAt: datetime
    size: int  # in bytes
    dimensions: ImageDimensions
    format: str
    url: str
    thumbnailUrl: str
    isPublic: bool = False
    vendor_id: str  # Added field to maintain compatibility with existing code
    processed: bool = False  # Added field to maintain compatibility with existing code
    public_id: str  # Added field to maintain compatibility with existing code
    # Added field to maintain compatibility with existing code
    folder_id: Optional[str] = None

    class Config:
        allow_population_by_field_name = True


class ImageSearchResult(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    tags: List[str] = []
    category: Optional[str] = None
    thumbnailUrl: str
    url: str
    dimensions: ImageDimensions
    format: str


class PaginatedImageMetadata(BaseModel):
    images: List[ImageMetadata]
    total: int


class PaginatedImageSearchResults(BaseModel):
    results: List[ImageSearchResult]
    total: int


class FilterOptions(BaseModel):
    categories: List[str] = []
    tags: List[str] = []
    formats: List[str] = []
