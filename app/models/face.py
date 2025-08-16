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
    faces: List[Dict[str, Any]]
    total_count: int


class PaginatedFacesResponse(BaseModel):
    """Paginated response for faces"""
    data: List[Face] = Field(..., description="List of faces")
    meta: PaginationMetadata = Field(..., description="Pagination metadata")