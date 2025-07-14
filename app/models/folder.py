from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict
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


class AccessLevel(str, Enum):
    READ = "read"
    WRITE = "write"
    ADMIN = "admin"


class StorageType(str, Enum):
    STANDARD_IA = "STANDARD_IA"    # S3 Standard – Infrequent Access (IA)
    GLACIER_IR = "GLACIER_IR"      # S3 Glacier Instant Retrieval
    DEEP_ARCHIVE = "DEEP_ARCHIVE"  # S3 Glacier Deep Archive


class FolderStatus(str, Enum):
    ACTIVE = "active"              # Folder is accessible (Standard IA, Glacier IR)
    INACTIVE = "inactive"          # Folder is in Deep Archive (not immediately accessible)
    CONVERTING = "converting"      # Folder is being converted between storage types


class ConversionMode(str, Enum):
    STANDARD = "Standard"          # 1-12 hours, higher cost
    BULK = "Bulk"                 # 5-12 hours, lower cost


class PaginatedFoldersResponse(BaseModel):
    """Paginated response for folders"""
    data: List['FolderWithAccess'] = Field(..., description="List of folders")
    meta: PaginationMetadata = Field(..., description="Pagination metadata")


class PaginatedResponse(BaseModel):
    """Generic paginated response"""
    data: List = Field(..., description="List of items")
    meta: PaginationMetadata = Field(..., description="Pagination metadata")


class FolderBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    parent_folder_id: Optional[str] = None
    storage_type: StorageType = StorageType.GLACIER_IR
    status: FolderStatus = FolderStatus.ACTIVE


class FolderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    parent_folder_id: Optional[str] = None
    storage_type: Optional[StorageType] = None  # Optional to distinguish explicit vs default


class FolderInDB(FolderBase):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    owner_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    is_shared: bool = False
    file_count: int = 0
    subfolder_count: int = 0
    
    # Conversion tracking fields
    conversion_job_id: Optional[str] = None
    conversion_started_at: Optional[datetime] = None
    conversion_estimated_completion: Optional[datetime] = None
    conversion_from_storage: Optional[StorageType] = None
    conversion_to_storage: Optional[StorageType] = None
    
    # Deep Archive specific fields
    retrieval_days: Optional[int] = None  # How many days to retrieve from Deep Archive (1-365, None = forever)
    retrieval_mode: Optional[ConversionMode] = None
    auto_return_to_standard: bool = False  # If True, automatically move to Standard after retrieval_days
    
    # New time-based conversion fields
    deep_archive_retrieval_start: Optional[datetime] = None      # When retrieval started
    deep_archive_retrieval_ready: Optional[datetime] = None      # When files will be ready
    deep_archive_retrieval_expires: Optional[datetime] = None    # When retrieval expires (back to Deep Archive)
    deep_archive_original_storage: Optional[StorageType] = None  # Original storage before Deep Archive

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}


class Folder(FolderBase):
    id: str = Field(..., alias="_id")
    owner_id: str
    created_at: datetime
    updated_at: datetime
    is_shared: bool = False
    file_count: int = 0
    subfolder_count: int = 0
    total_size: Optional[int] = Field(None, description="Total size in bytes including all files and subfolders")
    thumbnail_url: Optional[str] = Field(None, description="Thumbnail URL of the first file in the folder, if available")
    
    # Conversion tracking fields
    conversion_job_id: Optional[str] = None
    conversion_started_at: Optional[datetime] = None
    conversion_estimated_completion: Optional[datetime] = None
    conversion_from_storage: Optional[StorageType] = None
    conversion_to_storage: Optional[StorageType] = None
    
    # Deep Archive specific fields
    retrieval_days: Optional[int] = None
    retrieval_mode: Optional[ConversionMode] = None
    auto_return_to_standard: bool = False
    
    # New time-based conversion fields
    deep_archive_retrieval_start: Optional[datetime] = None      # When retrieval started
    deep_archive_retrieval_ready: Optional[datetime] = None      # When files will be ready
    deep_archive_retrieval_expires: Optional[datetime] = None    # When retrieval expires (back to Deep Archive)
    deep_archive_original_storage: Optional[StorageType] = None  # Original storage before Deep Archive

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}


class FolderUpdate(BaseModel):
    name: Optional[str] = None


class FolderAccessBase(BaseModel):
    folder_id: str
    user_id: str
    access_level: AccessLevel = AccessLevel.READ


class FolderAccessCreate(FolderAccessBase):
    pass


class FolderAccessInDB(FolderAccessBase):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    granted_by: str
    granted_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}


class FolderAccess(FolderAccessBase):
    id: str = Field(..., alias="_id")
    granted_by: str
    granted_at: datetime

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}


class FolderWithAccess(Folder):
    access_level: Optional[AccessLevel] = None
    shared_with: List[str] = Field(default_factory=list, description="List of email IDs of users this folder is shared with (only visible to folder owner)")
    shared_by_name: Optional[str] = Field(None, description="Full name of the person who shared this folder (only for shared folders)")


class ShareFolderRequest(BaseModel):
    user_email: str
    access_level: AccessLevel = AccessLevel.READ


class StorageTypeChangeRequest(BaseModel):
    folder_id: str
    new_storage_type: StorageType
    apply_to_children: bool = False
    
    # Deep Archive conversion settings
    retrieval_days: Optional[int] = Field(None, ge=1, le=365, description="Days to retrieve from Deep Archive (1-365). Use None for permanent retrieval.")
    retrieval_mode: ConversionMode = ConversionMode.BULK
    
    @validator('apply_to_children')
    def validate_deep_archive_children(cls, v, values):
        """When converting to Deep Archive, apply_to_children must be True"""
        if values.get('new_storage_type') == StorageType.DEEP_ARCHIVE and not v:
            raise ValueError("apply_to_children must be True when converting to Deep Archive")
        return v
    
    @validator('retrieval_days')
    def validate_retrieval_days(cls, v, values):
        """retrieval_days only applies when converting FROM Deep Archive"""
        new_storage = values.get('new_storage_type')
        if v is not None and new_storage == StorageType.DEEP_ARCHIVE:
            raise ValueError("retrieval_days only applies when converting FROM Deep Archive, not TO Deep Archive")
        return v


class StorageTypeChangeResponse(BaseModel):
    message: str
    folder_id: str
    folders_updated: int
    files_updated: int
    thumbnails_deleted: int
    new_storage_type: str
    new_status: FolderStatus
    
    # Conversion tracking
    conversion_job_id: Optional[str] = None
    estimated_completion_time: Optional[datetime] = None
    is_immediate: bool = True
    
    # Deep Archive specific
    retrieval_days: Optional[int] = None
    retrieval_mode: Optional[ConversionMode] = None
    bulk_mode_savings: Optional[str] = None  # Cost savings info
    
    storage_type: Optional[str] = None  # For backwards compatibility


class DeleteFolderResponse(BaseModel):
    message: str


class ShareFolderResponse(BaseModel):
    message: str
    user_email: str
    access_level: str


class RevokeFolderAccessResponse(BaseModel):
    message: str
    user_email: str


class FolderStatsFileType(BaseModel):
    count: int
    total_size_bytes: int
    total_size_formatted: str
    average_size_bytes: int
    average_size_formatted: str


class FolderStatsResponse(BaseModel):
    folder_id: str
    folder_name: str
    total_size_bytes: int
    total_size_formatted: str
    file_count: int
    subfolder_count: int
    file_types: Dict[str, FolderStatsFileType] 