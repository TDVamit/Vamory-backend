from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import Optional
from bson import ObjectId
from datetime import datetime, timezone
from app.dependencies import get_current_user, verify_public_token
from app.models.user import User, UserRole
from app.models.cost import (
    CostStructure, CostStructureInDB, CostStructureResponse, 
    CostStructureUpdate, CostCalculationRequest, CostCalculationResponse,
    StorageCosts, CostTier, RetrievalCosts, StorageTypeEnum
)
from app.database import get_costs_collection
from app.services.currency_helper import convert, currencies
from app.services.cost_calculator import cost_calculator_service
from pydantic import BaseModel

router = APIRouter(prefix="/credit", tags=["Credit"])

class ExchangeRateResponse(BaseModel):
    base_currency: str
    target_currency: str
    amount: float
    converted_amount: float
    rate: float
    base_currency_info: dict
    target_currency_info: dict

@router.get("/exchange-rate", response_model=ExchangeRateResponse)
async def get_exchange_rate(
    base: str = Query(..., description="Base currency code (e.g., 'USD')"),
    amount: float = Query(..., description="Amount to convert", gt=0),
    target: str = Query(default="USD", description="Target currency code (e.g., 'EUR')"),
    verify_public_token: dict = Depends(verify_public_token)
):
    """
    Get current exchange rate and convert amount from base currency to target currency.
    Requires authentication.
    """
    # Normalize currency codes to lowercase
    base_lower = base.lower()
    target_lower = target.lower()
    
    # Validate currency codes
    if base_lower not in currencies:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid base currency code: {base}. Supported currencies: {list(currencies.keys())}"
        )
    
    if target_lower not in currencies:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid target currency code: {target}. Supported currencies: {list(currencies.keys())}"
        )
    
    try:
        # Convert the amount
        converted_amount = await convert(base, amount, target)
        
        if converted_amount is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Unable to fetch exchange rate for {base} to {target}"
            )
        
        # Calculate the rate
        rate = converted_amount / amount
        
        return ExchangeRateResponse(
            base_currency=base.upper(),
            target_currency=target.upper(),
            amount=amount,
            converted_amount=converted_amount,
            rate=rate,
            base_currency_info=currencies[base_lower],
            target_currency_info=currencies[target_lower]
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching exchange rate: {str(e)}"
        )


def require_admin(current_user: User = Depends(get_current_user)):
    """Dependency to ensure user is admin"""
    if current_user.user_role != UserRole.admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return current_user


async def get_or_create_cost_structure() -> CostStructure:
    """Get existing cost structure or create default one"""
    costs_collection = await get_costs_collection()
    
    # Try to get existing cost structure
    cost_doc = await costs_collection.find_one({})
    
    if cost_doc:
        cost_doc["_id"] = str(cost_doc["_id"])
        return CostStructureResponse(**cost_doc)
    
    # Create default cost structure based on the provided structure
    default_costs = CostStructureInDB(
        standard_storage=StorageCosts(
            storage_type=StorageTypeEnum.STANDARD,
            tiers=[
                CostTier(min_gb=0, max_gb=10, cost_per_gb=0.035),
                CostTier(min_gb=10, max_gb=30, cost_per_gb=0.033),
                CostTier(min_gb=30, max_gb=None, cost_per_gb=0.03)
            ]
        ),
        archive_storage=StorageCosts(
            storage_type=StorageTypeEnum.ARCHIVE,
            tiers=[
                CostTier(min_gb=0, max_gb=10, cost_per_gb=0.0035),
                CostTier(min_gb=10, max_gb=30, cost_per_gb=0.0033),
                CostTier(min_gb=30, max_gb=None, cost_per_gb=0.003)
            ]
        ),
        retrieval=RetrievalCosts(cost_per_gb=0.0025)
    )
    
    # Insert default costs
    result = await costs_collection.insert_one(default_costs.dict(by_alias=True))
    
    # Return the created cost structure
    cost_doc = await costs_collection.find_one({"_id": result.inserted_id})
    cost_doc["_id"] = str(cost_doc["_id"])
    return CostStructureResponse(**cost_doc)


@router.get("/costs", response_model=CostStructureResponse)
async def get_costs(verify_public_token: dict = Depends(verify_public_token)):
    """Get current storage and retrieval costs"""
    return await get_or_create_cost_structure()


@router.put("/costs", response_model=CostStructureResponse)
async def update_costs(
    cost_update: CostStructureUpdate,
    admin_user: User = Depends(require_admin)
):
    """Update storage and retrieval costs (admin only)"""
    costs_collection = await get_costs_collection()
    
    # Get current cost structure
    current_costs = await get_or_create_cost_structure()
    
    # Prepare update data
    update_data = {
        "updated_at": datetime.now(timezone.utc),
        "updated_by": admin_user.id
    }
    
    if cost_update.standard_storage is not None:
        update_data["standard_storage"] = cost_update.standard_storage.dict()
    
    if cost_update.archive_storage is not None:
        update_data["archive_storage"] = cost_update.archive_storage.dict()
    
    if cost_update.retrieval is not None:
        update_data["retrieval"] = cost_update.retrieval.dict()
    
    # Update the cost structure
    await costs_collection.update_one(
        {"_id": ObjectId(current_costs.id)},
        {"$set": update_data}
    )
    
    # Return updated cost structure
    return await get_or_create_cost_structure()


@router.post("/costs/calculate", response_model=CostCalculationResponse)
async def calculate_costs(
    calculation_request: CostCalculationRequest,
    current_user: User = Depends(get_current_user)
):
    """Calculate storage and retrieval costs based on usage"""
    # Get current cost structure
    cost_structure = await get_or_create_cost_structure()
    
    # Calculate costs
    result = cost_calculator_service.calculate_total_costs(
        standard_gb=calculation_request.standard_storage_gb,
        archive_gb=calculation_request.archive_storage_gb,
        retrieval_gb=calculation_request.retrieval_gb,
        cost_structure=cost_structure
    )
    
    return CostCalculationResponse(**result)


@router.get("/my-storage-costs", response_model=CostCalculationResponse)
async def get_my_storage_costs(current_user: User = Depends(get_current_user)):
    """Calculate costs based on current user's storage usage including deleted files"""
    # Convert bytes to GB - include both active and deleted storage
    standard_gb = (current_user.storage_used_standard + current_user.storage_used_standard_deleted) / (1024 ** 3)  # Convert bytes to GB
    archive_gb = (current_user.storage_used_archived + current_user.storage_used_archived_deleted) / (1024 ** 3)   # Convert bytes to GB
    
    # Get current cost structure
    cost_structure = await get_or_create_cost_structure()
    
    # Calculate costs (no retrieval cost for current usage)
    result = cost_calculator_service.calculate_total_costs(
        standard_gb=standard_gb,
        archive_gb=archive_gb,
        retrieval_gb=0.0,  # Current usage doesn't include retrieval
        cost_structure=cost_structure
    )
    
    return CostCalculationResponse(**result)


class StorageBreakdownResponse(BaseModel):
    """Response model for detailed storage breakdown"""
    active_storage: dict
    deleted_storage: dict
    total_storage: dict
    cost_breakdown: dict


@router.get("/my-storage-breakdown", response_model=StorageBreakdownResponse)
async def get_my_storage_breakdown(current_user: User = Depends(get_current_user)):
    """Get detailed storage breakdown including active and deleted files"""
    # Convert bytes to GB
    active_standard_gb = current_user.storage_used_standard / (1024 ** 3)
    active_archive_gb = current_user.storage_used_archived / (1024 ** 3)
    deleted_standard_gb = current_user.storage_used_standard_deleted / (1024 ** 3)
    deleted_archive_gb = current_user.storage_used_archived_deleted / (1024 ** 3)
    
    # Calculate totals
    total_standard_gb = active_standard_gb + deleted_standard_gb
    total_archive_gb = active_archive_gb + deleted_archive_gb
    
    # Get current cost structure
    cost_structure = await get_or_create_cost_structure()
    
    # Calculate costs for total storage (including deleted)
    cost_result = cost_calculator_service.calculate_total_costs(
        standard_gb=total_standard_gb,
        archive_gb=total_archive_gb,
        retrieval_gb=0.0,
        cost_structure=cost_structure
    )
    
    return StorageBreakdownResponse(
        active_storage={
            "standard_gb": round(active_standard_gb, 2),
            "archive_gb": round(active_archive_gb, 2),
            "total_gb": round(active_standard_gb + active_archive_gb, 2)
        },
        deleted_storage={
            "standard_gb": round(deleted_standard_gb, 2),
            "archive_gb": round(deleted_archive_gb, 2),
            "total_gb": round(deleted_standard_gb + deleted_archive_gb, 2)
        },
        total_storage={
            "standard_gb": round(total_standard_gb, 2),
            "archive_gb": round(total_archive_gb, 2),
            "total_gb": round(total_standard_gb + total_archive_gb, 2)
        },
        cost_breakdown=cost_result
    )
