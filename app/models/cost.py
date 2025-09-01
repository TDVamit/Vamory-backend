"""
Cost models for storage pricing tiers.
"""
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, timezone
from bson import ObjectId
from enum import Enum


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


class StorageTypeEnum(str, Enum):
    STANDARD = "standard"
    ARCHIVE = "archive"


class CostTier(BaseModel):
    """Individual cost tier (e.g., 0-10GB at $0.035/GB)"""
    min_gb: float = Field(..., description="Minimum GB for this tier (inclusive)")
    max_gb: Optional[float] = Field(None, description="Maximum GB for this tier (exclusive), None means unlimited")
    cost_per_gb: float = Field(..., description="Cost per GB in USD")


class StorageCosts(BaseModel):
    """Storage costs for a specific storage type"""
    storage_type: StorageTypeEnum
    tiers: List[CostTier] = Field(..., description="Cost tiers for this storage type")
    
    class Config:
        use_enum_values = True


class RetrievalCosts(BaseModel):
    """Costs for data retrieval operations"""
    cost_per_gb: float = Field(..., description="Cost per GB for retrieval in USD")


class CostStructure(BaseModel):
    """Complete cost structure"""
    standard_storage: StorageCosts
    archive_storage: StorageCosts
    retrieval: RetrievalCosts
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_by: Optional[str] = Field(None, description="User ID who last updated the costs")


class CostStructureInDB(CostStructure):
    """Cost structure as stored in database"""
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}


class CostStructureResponse(CostStructure):
    """Cost structure response model"""
    id: str = Field(..., alias="_id")
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}


class CostStructureUpdate(BaseModel):
    """Model for updating cost structure"""
    standard_storage: Optional[StorageCosts] = None
    archive_storage: Optional[StorageCosts] = None
    retrieval: Optional[RetrievalCosts] = None


class CostCalculationRequest(BaseModel):
    """Request model for calculating storage costs"""
    standard_storage_gb: float = Field(0, ge=0, description="Amount of standard storage in GB")
    archive_storage_gb: float = Field(0, ge=0, description="Amount of archive storage in GB")
    retrieval_gb: float = Field(0, ge=0, description="Amount of data retrieved in GB")


class CostCalculationResponse(BaseModel):
    """Response model for cost calculations"""
    standard_storage_cost: float = Field(..., description="Cost for standard storage in USD")
    archive_storage_cost: float = Field(..., description="Cost for archive storage in USD")
    retrieval_cost: float = Field(..., description="Cost for data retrieval in USD")
    total_cost: float = Field(..., description="Total cost in USD")
    breakdown: dict = Field(..., description="Detailed cost breakdown by tiers")
