from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field
from bson import ObjectId


class CdnUploadStatus(BaseModel):
    """CDN upload status model"""
    id: Optional[str] = Field(default=None, description="Unique identifier")
    s3_key: str = Field(..., description="S3 key of the uploaded file")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="When the record was created")
    uploaded_at: Optional[datetime] = Field(default=None, description="When the file was uploaded to CDN")
    cdn_url: Optional[str] = Field(default=None, description="CDN URL for the file")
    status: str = Field(default="pending", description="Upload status: pending, uploaded, failed")
    error: Optional[str] = Field(default=None, description="Error message if upload failed")

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {
            ObjectId: str,
            datetime: lambda v: v.isoformat()
        }


class CdnUploadStatusCreate(BaseModel):
    """Model for creating a new CDN upload status"""
    s3_key: str = Field(..., description="S3 key of the uploaded file")


class CdnUploadStatusUpdate(BaseModel):
    """Model for updating an existing CDN upload status"""
    cdn_url: Optional[str] = Field(default=None, description="CDN URL for the file")
    status: Optional[str] = Field(default=None, description="Upload status: pending, uploaded, failed")
    uploaded_at: Optional[datetime] = Field(default=None, description="When the file was uploaded to CDN")
    error: Optional[str] = Field(default=None, description="Error message if upload failed")


class CdnUploadStatusInDB(CdnUploadStatus):
    """Model for CDN upload status stored in database"""
    pass


class CdnUrlResponse(BaseModel):
    """Response model for CDN URL"""
    id: str = Field(..., description="Unique identifier")
    s3_key: str = Field(..., description="S3 key of the file")
    cdn_url: str = Field(..., description="CDN URL for the file")
    m3u8_url: Optional[str] = Field(default=None, description="M3U8 URL for the file")
    created_at: datetime = Field(..., description="When the record was created")
    uploaded_at: Optional[datetime] = Field(default=None, description="When the file was uploaded to CDN")
    status: str = Field(..., description="Upload status")


class PaginatedCdnUrlsResponse(BaseModel):
    """Paginated response for CDN URLs"""
    data: List[CdnUrlResponse] = Field(..., description="List of CDN URLs")
    meta: dict = Field(..., description="Pagination metadata")


class PaginatedHlsStatusesResponse(BaseModel):
    """Paginated response for HLS statuses"""
    data: List[CdnUploadStatus] = Field(..., description="List of HLS statuses")
    meta: dict = Field(..., description="Pagination metadata")


class CdnUploadResponse(BaseModel):
    """Response model for CDN upload"""
    upload_id: str = Field(..., description="Unique identifier for the upload")
    s3_key: str = Field(..., description="S3 key of the uploaded file")
    status: str = Field(..., description="Upload status")
    message: str = Field(..., description="Response message")
