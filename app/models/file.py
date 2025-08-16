from pydantic import BaseModel, Field, validator
from typing import Optional, Dict, Any, List
from datetime import datetime
from bson import ObjectId
from enum import Enum
from app.models.common import PaginationMetadata


class PyObjectId(ObjectId):
    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v):
        if not ObjectId.is_valid(v):
            raise ValueError("Invalid objectid")
        return ObjectId(v)

    @classmethod
    def __get_pydantic_json_schema__(cls, field_schema):
        field_schema.update(type="string")


class FileType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    DOCUMENT = "document"
    OTHER = "other"


class StorageType(str, Enum):
    STANDARD = "STANDARD"          # S3 Standard (for thumbnails)
    STANDARD_IA = "STANDARD_IA"    # S3 Standard – Infrequent Access (IA)
    GLACIER_IR = "GLACIER_IR"      # S3 Glacier Instant Retrieval
    DEEP_ARCHIVE = "DEEP_ARCHIVE"  # S3 Glacier Deep Archive


class ArchivalStatus(str, Enum):
    ACTIVE = "active"
    DEEP_ARCHIVE = "deep_archive"
    GLACIER = "glacier"


class FileBase(BaseModel):
    filename: str = Field(..., min_length=1, max_length=255)
    original_filename: str
    file_type: FileType
    content_type: str
    file_size: int
    folder_id: str


class FileCreate(FileBase):
    pass


class FileInDB(FileBase):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    owner_id: str
    s3_key: str
    s3_url: str
    thumbnail_s3_key: Optional[str] = None
    thumbnail_s3_url: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    storage_type: StorageType = StorageType.GLACIER_IR
    archival_status: ArchivalStatus = ArchivalStatus.ACTIVE
    archived_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    file_hash: Optional[str] = None
    public_token: Optional[str] = None
    face_references: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    image_description: Optional[str] = None

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}


class File(FileBase):
    id: str = Field(..., alias="_id")
    owner_id: str
    s3_key: str
    s3_url: str
    thumbnail_s3_key: Optional[str] = None
    thumbnail_s3_url: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    storage_type: StorageType = StorageType.GLACIER_IR
    archival_status: ArchivalStatus = ArchivalStatus.ACTIVE
    archived_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    file_hash: Optional[str] = None
    public_token: Optional[str] = None
    face_references: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    image_description: Optional[str] = None

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}


class FileUpdate(BaseModel):
    filename: Optional[str] = None
    folder_id: Optional[str] = None


class FileUploadResponse(BaseModel):
    file_id: str
    filename: str
    file_size: int
    file_type: FileType
    s3_url: str
    thumbnail_url: Optional[str] = None
    upload_status: str = "completed"
    storage_type: StorageType
    unknown_faces : Optional[int] = None


class FileMetadata(BaseModel):
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[float] = None
    format: Optional[str] = None
    color_mode: Optional[str] = None
    has_transparency: Optional[bool] = None
    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None
    bitrate: Optional[int] = None
    frame_rate: Optional[float] = None


class ArchiveRequest(BaseModel):
    folder_id: str
    target_storage_type: StorageType = StorageType.DEEP_ARCHIVE
    delete_thumbnails: Optional[bool] = None  # Auto-determined based on storage type


class ArchiveResponse(BaseModel):
    message: str
    folder_id: str
    target_storage_type: StorageType
    files_processed: int
    subfolders_processed: int
    thumbnails_deleted: int
    thumbnails_created: int
    status: str = "completed"
    already_in_target_storage: bool = False 


class FileDownloadResponse(BaseModel):
    download_url: str
    filename: str


class FileThumbnailResponse(BaseModel):
    thumbnail_url: str


class FileDeleteResponse(BaseModel):
    message: str


class PaginatedFilesResponse(BaseModel):
    """Paginated response for files"""
    data: List['File'] = Field(..., description="List of files")
    meta: PaginationMetadata = Field(..., description="Pagination metadata") 