from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from bson import ObjectId
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


class FileReference(BaseModel):
    """Reference to a file containing this face"""
    file_id: str
    bbox: Dict[str, float]  # Bounding box coordinates
    added_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class FaceBase(BaseModel):
    """Base face model"""
    owner_id: str
    embedding: List[float]
    name: Optional[str] = None
    file_references: List[FileReference] = Field(default_factory=list)


class FaceCreate(FaceBase):
    """Model for creating a new face"""
    pass


class FaceInDB(FaceBase):
    """Face model as stored in database"""
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}


class Face(FaceBase):
    """Face model for API responses"""
    id: str = Field(..., alias="_id")
    created_at: datetime
    updated_at: datetime

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}


class FaceUpdate(BaseModel):
    """Model for updating face information"""
    name: Optional[str] = None


class FaceNameUpdateRequest(BaseModel):
    """Request model for updating face name"""
    face_id: str
    name: str


class FaceNameUpdateResponse(BaseModel):
    """Response model for face name update"""
    message: str
    face_id: str
    name: str
    files_affected: int


class UnknownFacesResponse(BaseModel):
    """Response model for unknown faces"""
    data: List[Dict[str, Any]] = Field(..., description="List of unknown faces")
    meta: PaginationMetadata = Field(..., description="Pagination metadata")


class PaginatedFacesResponse(BaseModel):
    """Paginated response for faces"""
    data: List[Face] = Field(..., description="List of faces")
    meta: PaginationMetadata = Field(..., description="Pagination metadata")


class FaceFileReference(BaseModel):
    """File reference for face response"""
    file_id: str
    filename: str
    bbox: Dict[str, float]
    s3_url: str
    added_at: datetime


class FaceDetailResponse(BaseModel):
    """Response model for single face detail"""
    face_id: str
    name: Optional[str] = None
    file_references: List[FaceFileReference]
    created_at: datetime
    updated_at: datetime


class FaceSuggestion(BaseModel):
    """Face suggestion with thumbnail for name search"""
    face_id: str
    name: Optional[str] = None
    thumbnail_s3_url: str
    thumbnail_bbox: Dict[str, float]
    thumbnail_filename: str


class FaceNameSuggestionResponse(BaseModel):
    """Paginated response for face name suggestions"""
    data: List[FaceSuggestion] = Field(..., description="List of face suggestions")
    meta: PaginationMetadata = Field(..., description="Pagination metadata")


class FaceThumbnail(BaseModel):
    """Face with thumbnail for list responses"""
    face_id: str
    name: Optional[str] = None
    thumbnail_s3_url: Optional[str] = None
    thumbnail_bbox: Optional[Dict[str, float]] = None
    thumbnail_filename: Optional[str] = None
    total_file_references: int
    created_at: datetime
    updated_at: datetime


class PaginatedFaceThumbnailsResponse(BaseModel):
    """Paginated response for faces with thumbnails"""
    data: List[FaceThumbnail] = Field(..., description="List of faces with thumbnails")
    meta: PaginationMetadata = Field(..., description="Pagination metadata")


class FaceMergeRequest(BaseModel):
    """Request model for merging faces"""
    face_id: str = Field(..., description="Source face ID to merge from")
    target_face_id: str = Field(..., description="Target face ID to merge into")


class FaceMergeResponse(BaseModel):
    """Response model for face merge operation"""
    message: str
    target_face_id: str
    merged_face_id: str
    total_file_references_moved: int
    target_face_name: Optional[str] = None