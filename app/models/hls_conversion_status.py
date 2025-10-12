from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
from bson import ObjectId


class HlsConversionStatus(BaseModel):
    """HLS conversion status model"""
    id: Optional[str] = Field(default=None, description="Unique identifier")
    job_id: str = Field(..., description="The job ID for the conversion")
    status: str = Field(..., description="Current status of the conversion")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="When the record was created")
    updated_at: datetime = Field(default_factory=datetime.utcnow, description="When the record was last updated")
    error: Optional[str] = Field(default=None, description="Error message if conversion failed")

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {
            ObjectId: str,
            datetime: lambda v: v.isoformat()
        }


class HlsConversionStatusCreate(BaseModel):
    """Model for creating a new HLS conversion status"""
    job_id: str = Field(..., description="The job ID for the conversion")
    status: str = Field(..., description="Current status of the conversion")
    error: Optional[str] = Field(default=None, description="Error message if conversion failed")


class HlsConversionStatusUpdate(BaseModel):
    """Model for updating an existing HLS conversion status"""
    status: Optional[str] = Field(default=None, description="Current status of the conversion")
    error: Optional[str] = Field(default=None, description="Error message if conversion failed")
    updated_at: datetime = Field(default_factory=datetime.utcnow, description="When the record was last updated")


class HlsConversionStatusInDB(HlsConversionStatus):
    """Model for HLS conversion status stored in database"""
    pass
