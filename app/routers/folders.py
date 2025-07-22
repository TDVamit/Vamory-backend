from fastapi import APIRouter, Depends, HTTPException, status, Query, Form
from typing import List, Optional, Dict
from bson import ObjectId
from datetime import datetime, timezone
from app.models.folder import (
    FolderCreate, Folder, FolderUpdate, FolderInDB, 
    ShareFolderRequest, FolderAccess, FolderAccessCreate, 
    FolderAccessInDB, AccessLevel, FolderWithAccess, StorageType, StorageTypeChangeRequest,
    StorageTypeChangeResponse, DeleteFolderResponse, ShareFolderResponse, 
    RevokeFolderAccessResponse, FolderStatsResponse, FolderStatsFileType,
    PaginatedFoldersResponse, FolderStatus, ConversionMode
)
from app.models.user import User, UserRole
from app.models.file import FileType
from app.dependencies import get_current_user, folder_read_access, folder_write_access, folder_admin_access, verify_folder_access
from app.database import (
    get_folders_collection, get_folder_access_collection, 
    get_users_collection, get_files_collection
)
from app.services.s3 import s3_service
from app.services.folder import folder_service
from app.utils import (
    format_file_size, 
    calculate_pagination_metadata, 
    calculate_skip_from_page,
    apply_dynamic_folder_status,
    apply_dynamic_folder_status_batch,
    convert_objectid
)
from app.services.storage_conversion import storage_conversion_service
import logging
from app.routers.auth import deny_if_viewer
from uuid import uuid4
from pydantic import BaseModel
from app.models.common import PaginationMetadata

class PublicSubfoldersResponse(BaseModel):
    data: List[Folder]

router = APIRouter(prefix="/folders", tags=["Folders"])
logger = logging.getLogger(__name__)


async def get_folder_shared_users(folder_ids: List[str]) -> Dict[str, List[str]]:
    """Get email IDs of users each folder is shared with"""
    if not folder_ids:
        return {}
    
    folder_access_collection = await get_folder_access_collection()
    users_collection = await get_users_collection()
    
    # Get all access records for these folders
    access_records = await folder_access_collection.find({
        "folder_id": {"$in": folder_ids}
    }).to_list(None)
    
    if not access_records:
        return {folder_id: [] for folder_id in folder_ids}
    
    # Get unique user IDs
    user_ids = list(set(record["user_id"] for record in access_records))
    
    # Batch lookup users
    users_cursor = users_collection.find({"_id": {"$in": [ObjectId(uid) for uid in user_ids]}})
    users = await users_cursor.to_list(None)
    user_emails_map = {str(user["_id"]): user["email"] for user in users}
    
    # Group by folder_id
    result = {folder_id: [] for folder_id in folder_ids}
    for record in access_records:
        folder_id = record["folder_id"]
        user_id = record["user_id"]
        email = user_emails_map.get(user_id, "unknown@example.com")
        if email not in result[folder_id]:  # Avoid duplicates
            result[folder_id].append(email)
    
    return result


async def get_folder_thumbnail(folder_id: str) -> Optional[str]:
    """Get thumbnail URL of the first file in the folder that has a thumbnail"""
    files_collection = await get_files_collection()
    
    # Find the first file in the folder that has a thumbnail, prioritizing images
    first_file_with_thumbnail = await files_collection.find_one({
        "folder_id": folder_id,
        "thumbnail_s3_key": {"$exists": True, "$ne": None, "$ne": ""},
        "file_type": FileType.IMAGE.value
    }, sort=[("created_at", 1)])  # Sort by creation date, oldest first
    
    # If no image with thumbnail found, try any file type with thumbnail
    if not first_file_with_thumbnail:
        first_file_with_thumbnail = await files_collection.find_one({
            "folder_id": folder_id,
            "thumbnail_s3_key": {"$exists": True, "$ne": None, "$ne": ""}
        }, sort=[("created_at", 1)])
    
    if first_file_with_thumbnail and first_file_with_thumbnail.get("thumbnail_s3_key"):
        # Generate fresh presigned URL for the thumbnail
        thumbnail_url = await s3_service.generate_presigned_url(
            first_file_with_thumbnail["thumbnail_s3_key"], 
            3600  # 1 hour expiry
        )
        return thumbnail_url
    
    return None


@router.post("/", response_model=Folder)
async def create_folder(
    folder: FolderCreate,
    current_user: User = Depends(get_current_user)
):
    deny_if_viewer(current_user)
    """Create a new folder with specified storage type"""
    folders_collection = await get_folders_collection()
    
    # Validate parent folder if provided
    if folder.parent_folder_id:
        parent_folder = await folders_collection.find_one({"_id": ObjectId(folder.parent_folder_id)})
        if not parent_folder:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Parent folder not found"
            )
        
        # Check if user has write access to parent folder
        await verify_folder_access(folder.parent_folder_id, current_user.id, AccessLevel.WRITE)
        
        # Inherit storage type from parent if not explicitly set
        if folder.storage_type is None:
            folder.storage_type = StorageType(parent_folder.get("storage_type", StorageType.GLACIER_IR))
    
    # Set default storage type if not provided and no parent to inherit from
    if folder.storage_type is None:
        folder.storage_type = StorageType.GLACIER_IR
    
    # Set initial status based on storage type
    initial_status = FolderStatus.INACTIVE if folder.storage_type == StorageType.DEEP_ARCHIVE else FolderStatus.ACTIVE
    
    # Create folder in database
    folder_in_db = FolderInDB(
        **folder.dict(),
        owner_id=current_user.id,
        status=initial_status
    )
    
    result = await folders_collection.insert_one(folder_in_db.dict(by_alias=True))
    
    if result.inserted_id:
        # Update parent folder's subfolder count if applicable
        if folder.parent_folder_id:
            await folders_collection.update_one(
                {"_id": ObjectId(folder.parent_folder_id)},
                {"$inc": {"subfolder_count": 1}}
            )
        
        created_folder = await folders_collection.find_one({"_id": result.inserted_id})
        created_folder["_id"] = str(created_folder["_id"])
        return Folder(**convert_objectid(created_folder))
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create folder"
        )


@router.get("/", response_model=PaginatedFoldersResponse)
async def get_folders(
    search: str = Query("", description="Search by folder name"),
    storage_type: Optional[StorageType] = Query(None, description="Filter by storage type"),
    parent_folder_id: Optional[str] = Query(None, description="Filter by parent folder ID"),
    include_size: bool = Query(False, description="Include total size calculation (may be slower)"),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    per_page: int = Query(20, ge=1, le=100, description="Number of items per page"),
    sort_by: str = Query("created_at", description="Sort by field"),
    sort_order: str = Query("desc", regex="^(asc|desc)$", description="Sort order"),
    current_user: User = Depends(get_current_user)
):
    """Get folders with search, filtering, and pagination - includes owned and shared folders"""
    try:
        folders_collection = await get_folders_collection()
        folder_access_collection = await get_folder_access_collection()
        
        # Build query for folders (owned OR shared)
        if parent_folder_id:
            # First verify user has access to the parent folder
            await verify_folder_access(parent_folder_id, current_user.id, AccessLevel.READ)
            
            # When filtering by parent, show ALL direct children of that folder
            # (regardless of ownership, since user has access to parent)
            folder_query = {"parent_folder_id": parent_folder_id}
            
            # Still need to get shared access records for access level calculation
            shared_access_cursor = folder_access_collection.find({"user_id": current_user.id})
            shared_access_records = await shared_access_cursor.to_list(None)
            shared_folder_ids = [record["folder_id"] for record in shared_access_records]
        else:
            # Get folder IDs that are shared with the user
            shared_access_cursor = folder_access_collection.find({"user_id": current_user.id})
            shared_access_records = await shared_access_cursor.to_list(None)
            shared_folder_ids = [record["folder_id"] for record in shared_access_records]
            
            # For root level, show owned OR shared folders
            folder_query = {
                "$or": [
                    {"owner_id": current_user.id},  # Owned folders
                    {"_id": {"$in": [ObjectId(fid) for fid in shared_folder_ids]}}  # Shared folders
                ]
            }
        
        # Add search
        if search:
            search_constraint = {"name": {"$regex": search, "$options": "i"}}
            folder_query = {"$and": [folder_query, search_constraint]}
        
        # Add storage type filter
        if storage_type:
            storage_constraint = {"storage_type": storage_type.value}
            if "$and" in folder_query:
                folder_query["$and"].append(storage_constraint)
            else:
                folder_query = {"$and": [folder_query, storage_constraint]}
        
        # Get total count
        total_count = await folders_collection.count_documents(folder_query)
        
        # Calculate pagination
        skip = calculate_skip_from_page(page, per_page)
        
        # Build sort
        sort_direction = 1 if sort_order == "asc" else -1
        sort_spec = [(sort_by, sort_direction)]
        
        # Get folders
        cursor = folders_collection.find(folder_query).sort(sort_spec).skip(skip).limit(per_page)
        folders = await cursor.to_list(None)
        
        # Apply dynamic status calculation
        folders = await apply_dynamic_folder_status_batch(folders)
        
        # Get folder IDs for shared users lookup
        folder_ids = [str(folder["_id"]) for folder in folders]
        shared_users_map = await get_folder_shared_users(folder_ids)
        
        # Get all unique owner IDs for shared folders to batch lookup users
        shared_folder_owner_ids = []
        for folder in folders:
            if folder["owner_id"] != current_user.id:
                shared_folder_owner_ids.append(ObjectId(folder["owner_id"]))
        
        # Batch lookup users for shared folders
        users_collection = await get_users_collection()
        owners_map = {}
        if shared_folder_owner_ids:
            owners_cursor = users_collection.find({"_id": {"$in": list(set(shared_folder_owner_ids))}})
            owners = await owners_cursor.to_list(None)
            owners_map = {str(owner["_id"]): owner for owner in owners}
        
        # Convert to response models with access level information
        folder_responses = []
        for folder in folders:
            # Convert ObjectId to string
            folder_id = str(folder["_id"])
            folder["_id"] = folder_id
            
            # Determine access level for this user
            if folder["owner_id"] == current_user.id:
                access_level = AccessLevel.ADMIN  # Owner has admin access
                shared_by_name = None  # Not applicable for owned folders
            else:
                # For shared folders, find the access level
                access_record = next((r for r in shared_access_records if r["folder_id"] == folder_id), None)
                
                if access_record:
                    # Direct sharing access
                    access_level = AccessLevel(access_record["access_level"])
                else:
                    # Fallback for root shared folders
                    access_level = AccessLevel.READ
                
                # Get the owner's full name for shared folders from the batched lookup
                owner = owners_map.get(folder["owner_id"])
                shared_by_name = owner.get("full_name", owner.get("email", "Unknown")) if owner else "Unknown"
            
            # Calculate size if requested
            if include_size:
                folder["total_size"] = await folder_service.calculate_folder_size(folder_id)
            
            # Get thumbnail
            folder["thumbnail_url"] = await get_folder_thumbnail(folder_id)
            
            # Create response with access level and shared by info
            folder_response = FolderWithAccess(**folder, access_level=access_level)
            
            # Add shared_by_name to the response if it's a shared folder
            if shared_by_name:
                folder_response.shared_by_name = shared_by_name
            
            # Add shared_with email IDs (only show if user is the owner)
            if folder["owner_id"] == current_user.id:
                folder_response.shared_with = shared_users_map.get(folder_id, [])
            
            folder_responses.append(folder_response)
        
        # Create pagination metadata
        meta = calculate_pagination_metadata(total_count, page, per_page)
        
        return PaginatedFoldersResponse(data=folder_responses, meta=meta)
        
    except Exception as e:
        logger.error(f"Error getting folders: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve folders")


@router.get("/root", response_model=PaginatedFoldersResponse)
async def get_root_folders(
    search: str = Query("", description="Search by folder name"),
    storage_type: Optional[StorageType] = Query(None, description="Filter by storage type"),
    only_true_roots: bool = Query(True, description="Only folders with no parent (true roots)"),
    include_size: bool = Query(False, description="Include total size calculation (may be slower)"),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    per_page: int = Query(20, ge=1, le=100, description="Number of items per page"),
    sort_by: str = Query("created_at", description="Sort by field"),
    sort_order: str = Query("desc", regex="^(asc|desc)$", description="Sort order"),
    current_user: User = Depends(get_current_user)
):
    """Get root folders with dynamic status calculation - includes owned and shared folders"""
    try:
        folders_collection = await get_folders_collection()
        folder_access_collection = await get_folder_access_collection()
        
        # Get folder IDs that are shared with the user
        shared_access_cursor = folder_access_collection.find({"user_id": current_user.id})
        shared_access_records = await shared_access_cursor.to_list(None)
        shared_folder_ids = [record["folder_id"] for record in shared_access_records]
        
        # Build query for root folders (owned OR shared)
        if current_user.user_role == UserRole.super_admin:
            folder_query = {
                "$or": [
                    {"parent_folder_id": None},
                    {"_id": {"$in": [ObjectId(fid) for fid in shared_folder_ids]}}  # Shared folders
                ]
            }
        else:
            folder_query = {
                "$or": [
                    {"owner_id": current_user.id},  # Owned folders
                    {"_id": {"$in": [ObjectId(fid) for fid in shared_folder_ids]}}  # Shared folders
                ]
            }
        
        if only_true_roots:
            # Add parent folder constraint
            root_constraint = {
                "$or": [
                    {"parent_folder_id": {"$exists": False}},
                    {"parent_folder_id": None},
                    {"parent_folder_id": ""}
                ]
            }
            folder_query = {"$and": [folder_query, root_constraint]}
        
        # Add search
        if search:
            search_constraint = {"name": {"$regex": search, "$options": "i"}}
            if "$and" in folder_query:
                folder_query["$and"].append(search_constraint)
            else:
                folder_query = {"$and": [folder_query, search_constraint]}
        
        # Add storage type filter
        if storage_type:
            storage_constraint = {"storage_type": storage_type.value}
            if "$and" in folder_query:
                folder_query["$and"].append(storage_constraint)
            else:
                folder_query = {"$and": [folder_query, storage_constraint]}
        
        # Get total count
        total_count = await folders_collection.count_documents(folder_query)
        
        # Calculate pagination
        skip = calculate_skip_from_page(page, per_page)
        
        # Build sort
        sort_direction = 1 if sort_order == "asc" else -1
        sort_spec = [(sort_by, sort_direction)]
        
        # Get folders
        cursor = folders_collection.find(folder_query).sort(sort_spec).skip(skip).limit(per_page)
        folders = await cursor.to_list(None)
        
        # Apply dynamic status calculation
        folders = await apply_dynamic_folder_status_batch(folders)
        
        # Get folder IDs for shared users lookup
        folder_ids = [str(folder["_id"]) for folder in folders]
        shared_users_map = await get_folder_shared_users(folder_ids)
        
        # Get all unique owner IDs for shared folders to batch lookup users
        shared_folder_owner_ids = []
        for folder in folders:
            if folder["owner_id"] != current_user.id:
                shared_folder_owner_ids.append(ObjectId(folder["owner_id"]))
        
        # Batch lookup users for shared folders
        users_collection = await get_users_collection()
        owners_map = {}
        if shared_folder_owner_ids:
            owners_cursor = users_collection.find({"_id": {"$in": list(set(shared_folder_owner_ids))}})
            owners = await owners_cursor.to_list(None)
            owners_map = {str(owner["_id"]): owner for owner in owners}
        
        # Convert to response models with access level information
        folder_responses = []
        for folder in folders:
            # Convert ObjectId to string
            folder_id = str(folder["_id"])
            folder["_id"] = folder_id
            
            # Determine access level for this user
            if folder["owner_id"] == current_user.id:
                access_level = AccessLevel.ADMIN  # Owner has admin access
                shared_by_name = None  # Not applicable for owned folders
            else:
                # For shared folders, find the access level
                access_record = next((r for r in shared_access_records if r["folder_id"] == folder_id), None)
                
                if access_record:
                    # Direct sharing access
                    access_level = AccessLevel(access_record["access_level"])
                else:
                    # Fallback for root shared folders
                    access_level = AccessLevel.READ
                
                # Get the owner's full name for shared folders from the batched lookup
                owner = owners_map.get(folder["owner_id"])
                shared_by_name = owner.get("full_name", owner.get("email", "Unknown")) if owner else "Unknown"
            
            # Calculate size if requested
            if include_size:
                folder["total_size"] = await folder_service.calculate_folder_size(folder_id)
            
            # Get thumbnail
            folder["thumbnail_url"] = await get_folder_thumbnail(folder_id)
            
            # Create response with access level and shared by info
            folder_response = FolderWithAccess(**folder, access_level=access_level)
            
            # Add shared_by_name to the response if it's a shared folder
            if shared_by_name:
                folder_response.shared_by_name = shared_by_name
            
            # Add shared_with email IDs (only show if user is the owner)
            if folder["owner_id"] == current_user.id:
                folder_response.shared_with = shared_users_map.get(folder_id, [])
            
            folder_responses.append(folder_response)
        
        # Create pagination metadata
        meta = calculate_pagination_metadata(total_count, page, per_page)
        
        return PaginatedFoldersResponse(data=folder_responses, meta=meta)
        
    except Exception as e:
        logger.error(f"Error getting root folders: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve root folders")


@router.get("/{folder_id}", response_model=FolderWithAccess)
async def get_folder(
    folder_id: str,
    user_id: str = Depends(folder_read_access),
    current_user: User = Depends(get_current_user)
):
    # If super_admin, skip access validation
    if current_user.user_role == UserRole.super_admin:
        user_id = None
    """Get a specific folder with dynamic status calculation"""
    try:
        folders_collection = await get_folders_collection()
        
        # Get folder
        folder = await folders_collection.find_one({"_id": ObjectId(folder_id)})
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")
        
        # Convert ObjectId to string
        folder["_id"] = str(folder["_id"])
        
        # Apply dynamic status calculation
        folder = await apply_dynamic_folder_status(folder)
        
        # Calculate total size
        folder["total_size"] = await folder_service.calculate_folder_size(folder_id)
        
        # Get thumbnail
        folder["thumbnail_url"] = await get_folder_thumbnail(folder_id)
        
        # Get shared users if current user is the owner
        shared_users_map = await get_folder_shared_users([folder_id])
        
        # Create response
        folder_response = FolderWithAccess(**folder)
        
        # Add shared_with email IDs (only show if user is the owner)
        if folder["owner_id"] == user_id:
            folder_response.shared_with = shared_users_map.get(folder_id, [])
        
        return FolderWithAccess(**convert_objectid(folder_response.dict()))
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting folder {folder_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve folder")


@router.put("/{folder_id}", response_model=Folder)
async def update_folder(
    folder_id: str,
    folder_update: FolderUpdate,
    user_id: str = Depends(folder_write_access),
    current_user: User = Depends(get_current_user)
):
    deny_if_viewer(current_user)
    """Update folder name only"""
    folders_collection = await get_folders_collection()
    
    # Validate that at least one field is provided
    update_data = {}
    if folder_update.name is not None:
        update_data["name"] = folder_update.name
    
    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update"
        )
    
    # Add updated timestamp
    update_data["updated_at"] = datetime.now(timezone.utc)
    
    # Update folder
    result = await folders_collection.update_one(
        {"_id": ObjectId(folder_id)},
        {"$set": update_data}
    )
    
    if result.matched_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Folder not found"
        )
    
    # Get updated folder
    updated_folder = await folders_collection.find_one({"_id": ObjectId(folder_id)})
    updated_folder["_id"] = str(updated_folder["_id"])
    return Folder(**convert_objectid(updated_folder))


@router.post("/{folder_id}/change-storage-type", response_model=StorageTypeChangeResponse)
async def change_folder_storage_type(
    folder_id: str,
    new_storage_type: StorageType = Form(..., description="New storage type"),
    apply_to_children: bool = Form(False, description="Apply to all subfolders and files"),
    retrieval_days: Optional[int] = Form(None, ge=1, le=365, description="Days to retrieve from Deep Archive (1-365, None = permanent)"),
    retrieval_mode: ConversionMode = Form(ConversionMode.BULK, description="Retrieval mode for Deep Archive"),
    user_id: str = Depends(folder_admin_access),
    current_user: User = Depends(get_current_user)
):
    deny_if_viewer(current_user)
    """Change storage type with time-based conversion tracking"""
    try:
        folders_collection = await get_folders_collection()
        files_collection = await get_files_collection()
        
        # Get the folder
        folder = await folders_collection.find_one({"_id": ObjectId(folder_id)})
        
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")
        
        # Apply dynamic status to get current effective storage type
        folder = await apply_dynamic_folder_status(folder)
        current_storage = StorageType(folder.get("effective_storage_type", folder.get("storage_type")))
        
        # Auto-correct parameters for Deep Archive conversions
        if new_storage_type == StorageType.DEEP_ARCHIVE:
            apply_to_children = True  # Force include children
            retrieval_days = None     # Not applicable for TO Deep Archive
            retrieval_mode = ConversionMode.BULK  # Force bulk mode
        
        # Validate retrieval_days for FROM Deep Archive
        if current_storage == StorageType.DEEP_ARCHIVE and new_storage_type != StorageType.DEEP_ARCHIVE:
            if retrieval_days is not None and retrieval_days < 1:
                raise HTTPException(status_code=400, detail="retrieval_days must be at least 1 or None for permanent")
        
        # Check if conversion is needed
        if current_storage == new_storage_type:
            raise HTTPException(status_code=400, detail=f"Folder is already {new_storage_type.value}")
        
        # Count affected items
        if apply_to_children:
            all_folder_ids = await storage_conversion_service._get_all_child_folders(folder_id)
        else:
            all_folder_ids = [folder_id]
        
        file_count = await files_collection.count_documents({
            "folder_id": {"$in": all_folder_ids}
        })
        
        # Determine conversion type and execute
        if current_storage == StorageType.DEEP_ARCHIVE:
            # FROM Deep Archive - delayed retrieval
            job_id, retrieval_start, retrieval_ready, retrieval_expires = await storage_conversion_service.start_deep_archive_retrieval(
                folder_id=folder_id,
                target_storage=new_storage_type,
                apply_to_children=apply_to_children,
                retrieval_days=retrieval_days,
                retrieval_mode=retrieval_mode
            )
            
            return StorageTypeChangeResponse(
                message=f"Started Deep Archive retrieval ({retrieval_mode.value} mode)",
                folder_id=folder_id,
                folders_updated=len(all_folder_ids),
                files_updated=file_count,
                thumbnails_deleted=0,
                new_storage_type=new_storage_type.value,
                new_status=FolderStatus.CONVERTING,
                conversion_job_id=job_id,
                estimated_completion_time=retrieval_ready,
                is_immediate=False,
                retrieval_days=retrieval_days,
                retrieval_mode=retrieval_mode,
                bulk_mode_savings=storage_conversion_service.get_cost_savings_info(retrieval_mode, file_count)
            )
        
        else:
            # TO Deep Archive or between Standard/Glacier - immediate
            job_id, folders_updated, files_updated = await storage_conversion_service.start_immediate_conversion(
                folder_id=folder_id,
                storage_from=current_storage,
                storage_to=new_storage_type,
                apply_to_children=apply_to_children
            )
            
            new_status = FolderStatus.INACTIVE if new_storage_type == StorageType.DEEP_ARCHIVE else FolderStatus.ACTIVE
            thumbnails_deleted = 0  # Thumbnails are no longer deleted, just hidden from API
            
            return StorageTypeChangeResponse(
                message=f"Storage type changed to {new_storage_type.value}",
                folder_id=folder_id,
                folders_updated=folders_updated,
                files_updated=files_updated,
                thumbnails_deleted=thumbnails_deleted,
                new_storage_type=new_storage_type.value,
                new_status=new_status,
                conversion_job_id=job_id,
                estimated_completion_time=None,
                is_immediate=True,
                retrieval_days=None,
                retrieval_mode=None,
                bulk_mode_savings=None
            )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error changing storage type for folder {folder_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to change storage type")


@router.get("/{folder_id}/conversion-status")
async def get_conversion_status(
    folder_id: str,
    user_id: str = Depends(folder_read_access)
):
    """Get real-time conversion status based on timing"""
    try:
        folders_collection = await get_folders_collection()
        
        # Get folder
        folder = await folders_collection.find_one({"_id": ObjectId(folder_id)})
        
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")
        
        # Apply dynamic status calculation
        folder = await apply_dynamic_folder_status(folder)
        
        # Return status information
        return convert_objectid({
            "folder_id": folder_id,
            "current_status": folder.get("status"),
            "storage_type": folder.get("storage_type"),
            "effective_storage_type": folder.get("effective_storage_type"),
            "conversion_job_id": folder.get("conversion_job_id"),
            "conversion_started_at": folder.get("conversion_started_at"),
            "conversion_estimated_completion": folder.get("conversion_estimated_completion"),
            "conversion_from_storage": folder.get("conversion_from_storage"),
            "conversion_to_storage": folder.get("conversion_to_storage"),
            "retrieval_status": folder.get("retrieval_status"),
            "deep_archive_retrieval_start": folder.get("deep_archive_retrieval_start"),
            "deep_archive_retrieval_ready": folder.get("deep_archive_retrieval_ready"),
            "deep_archive_retrieval_expires": folder.get("deep_archive_retrieval_expires"),
            "retrieval_days": folder.get("retrieval_days"),
            "retrieval_mode": folder.get("retrieval_mode")
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting conversion status for folder {folder_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get conversion status")


@router.delete("/{folder_id}", response_model=DeleteFolderResponse)
async def delete_folder(
    folder_id: str,
    user_id: str = Depends(folder_admin_access)
):
    """Delete a folder and all its contents"""
    folders_collection = await get_folders_collection()
    files_collection = await get_files_collection()
    folder_access_collection = await get_folder_access_collection()
    
    async def get_all_subfolders(parent_id: str) -> List[str]:
        """Recursively get all subfolder IDs"""
        all_folders = [parent_id]
        
        async def get_children(folder_id: str):
            children = await folders_collection.find({"parent_folder_id": folder_id}).to_list(None)
            for child in children:
                child_id = str(child["_id"])
                all_folders.append(child_id)
                await get_children(child_id)  # Recursive call
        
        await get_children(parent_id)
        return all_folders
    
    try:
        # Get all folder IDs to delete (including subfolders)
        folder_ids_to_delete = await get_all_subfolders(folder_id)
        
        # Get all files in these folders
        files_to_delete = await files_collection.find({
            "folder_id": {"$in": folder_ids_to_delete}
        }).to_list(None)
        
        # Delete files from S3 (only if not referenced elsewhere)
        s3_keys_to_delete = set()
        for file_doc in files_to_delete:
            # Main file S3 key
            s3_key = file_doc.get("s3_key")
            if s3_key:
                count = await files_collection.count_documents({"s3_key": s3_key})
                if count == 1:
                    s3_keys_to_delete.add(s3_key)
            # Thumbnail S3 key
            thumbnail_s3_key = file_doc.get("thumbnail_s3_key")
            if thumbnail_s3_key:
                count = await files_collection.count_documents({"thumbnail_s3_key": thumbnail_s3_key})
                if count == 1:
                    s3_keys_to_delete.add(thumbnail_s3_key)
        
        if s3_keys_to_delete:
            delete_result = await s3_service.delete_files_batch(list(s3_keys_to_delete))
            logger.info(f"Deleted {delete_result.get('deleted', 0)} files from S3 for folder {folder_id}")
        
        # Delete files from database
        files_delete_result = await files_collection.delete_many({
            "folder_id": {"$in": folder_ids_to_delete}
        })
        
        # Delete folder access records
        await folder_access_collection.delete_many({
            "folder_id": {"$in": folder_ids_to_delete}
        })
        
        # Delete folders from database (convert to ObjectId)
        folder_object_ids = [ObjectId(fid) for fid in folder_ids_to_delete]
        folders_delete_result = await folders_collection.delete_many({
            "_id": {"$in": folder_object_ids}
        })
        
        # Update parent folder's subfolder count if this folder had a parent
        parent_folder = await folders_collection.find_one({"_id": ObjectId(folder_id)})
        if parent_folder and parent_folder.get("parent_folder_id"):
            await folders_collection.update_one(
                {"_id": ObjectId(parent_folder["parent_folder_id"])},
                {"$inc": {"subfolder_count": -1}}
            )
        
        return DeleteFolderResponse(
            message=f"Successfully deleted folder and {files_delete_result.deleted_count} files"
        )
        
    except Exception as e:
        logger.error(f"Error deleting folder {folder_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete folder"
        )


@router.post("/{folder_id}/share", response_model=ShareFolderResponse)
async def share_folder(
    folder_id: str,
    share_request: ShareFolderRequest,
    current_user: User = Depends(get_current_user)
):
    deny_if_viewer(current_user)
    """Share folder with another user (owner only)"""
    folders_collection = await get_folders_collection()
    users_collection = await get_users_collection()
    folder_access_collection = await get_folder_access_collection()
    
    # Verify folder exists and user is the owner
    folder = await folders_collection.find_one({
        "_id": ObjectId(folder_id),
        "owner_id": current_user.id
    })
    
    if not folder:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Folder not found or you are not the owner"
        )
    
    # Find the user to share with
    user_to_share_with = await users_collection.find_one({
        "email": share_request.user_email,
        "is_active": True
    })
    
    if not user_to_share_with:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found or inactive"
        )
    
    # Check if folder is already shared with this user
    existing_access = await folder_access_collection.find_one({
        "folder_id": folder_id,
        "user_id": str(user_to_share_with["_id"])
    })
    
    if existing_access:
        # Update existing access level
        await folder_access_collection.update_one(
            {"_id": existing_access["_id"]},
            {"$set": {
                "access_level": share_request.access_level.value,
                "granted_at": datetime.now(timezone.utc)
            }}
        )
        message = f"Updated access level to {share_request.access_level.value}"
    else:
        # Create new folder access
        folder_access = FolderAccessInDB(
            folder_id=folder_id,
            user_id=str(user_to_share_with["_id"]),
            access_level=share_request.access_level,
            granted_by=current_user.id
        )
        
        await folder_access_collection.insert_one(folder_access.dict(by_alias=True))
        
        # Update folder's is_shared status
        await folders_collection.update_one(
            {"_id": ObjectId(folder_id)},
            {"$set": {"is_shared": True}}
        )
        
        message = f"Folder shared with {share_request.access_level.value} access"
    
    return ShareFolderResponse(
        message=message,
        user_email=share_request.user_email,
        access_level=share_request.access_level.value
    )


@router.delete("/{folder_id}/share/{user_email}", response_model=RevokeFolderAccessResponse)
async def revoke_folder_access(
    folder_id: str,
    user_email: str,
    current_user: User = Depends(get_current_user)
):
    """Revoke folder access from a user (owner only)"""
    folders_collection = await get_folders_collection()
    users_collection = await get_users_collection()
    folder_access_collection = await get_folder_access_collection()
    
    # Verify folder exists and user is the owner
    folder = await folders_collection.find_one({
        "_id": ObjectId(folder_id),
        "owner_id": current_user.id
    })
    
    if not folder:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Folder not found or you are not the owner"
        )
    
    # Find the user
    user = await users_collection.find_one({"email": user_email})
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Remove folder access
    delete_result = await folder_access_collection.delete_one({
        "folder_id": folder_id,
        "user_id": str(user["_id"])
    })
    
    if delete_result.deleted_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Access record not found"
        )
    
    # Check if folder still has any shared access
    remaining_access = await folder_access_collection.count_documents({
        "folder_id": folder_id
    })
    
    # Update folder's is_shared status if no more shared access
    if remaining_access == 0:
        await folders_collection.update_one(
            {"_id": ObjectId(folder_id)},
            {"$set": {"is_shared": False}}
        )
    
    return RevokeFolderAccessResponse(
        message="Folder access revoked successfully",
        user_email=user_email
    )


@router.get("/{folder_id}/stats", response_model=FolderStatsResponse)
async def get_folder_statistics(
    folder_id: str,
    user_id: str = Depends(folder_read_access)
):
    """Get detailed folder statistics including file type breakdown"""
    folders_collection = await get_folders_collection()
    files_collection = await get_files_collection()
    
    # Get folder info
    folder = await folders_collection.find_one({"_id": ObjectId(folder_id)})
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")
    
    # Get all files in folder and subfolders
    all_folder_ids = await storage_conversion_service._get_all_child_folders(folder_id)
    
    # Aggregate file statistics
    pipeline = [
        {"$match": {"folder_id": {"$in": all_folder_ids}}},
        {"$group": {
            "_id": "$file_type",
            "count": {"$sum": 1},
            "total_size": {"$sum": "$file_size"},
            "avg_size": {"$avg": "$file_size"}
        }}
    ]
    
    file_stats = await files_collection.aggregate(pipeline).to_list(None)
    
    # Calculate totals
    total_files = sum(stat["count"] for stat in file_stats)
    total_size = sum(stat["total_size"] for stat in file_stats)
    
    # Build file type breakdown
    file_types = {}
    for stat in file_stats:
        file_type = stat["_id"].lower() if stat["_id"] else "unknown"
        file_types[file_type] = FolderStatsFileType(
            count=stat["count"],
            total_size_bytes=int(stat["total_size"]),
            total_size_formatted=format_file_size(stat["total_size"]),
            average_size_bytes=int(stat["avg_size"]),
            average_size_formatted=format_file_size(stat["avg_size"])
        )
    
    return convert_objectid(FolderStatsResponse(
        folder_id=folder_id,
        folder_name=folder["name"],
        total_size_bytes=total_size,
        total_size_formatted=format_file_size(total_size),
        file_count=total_files,
        subfolder_count=len(all_folder_ids) - 1,  # Subtract 1 to exclude the folder itself
        file_types=file_types
    ))


@router.get("/{folder_id}/shared-with")
async def get_folder_shared_with(
    folder_id: str,
    current_user: User = Depends(get_current_user)
):
    """Get list of users this folder is shared with (owner only)"""
    folders_collection = await get_folders_collection()
    users_collection = await get_users_collection()
    folder_access_collection = await get_folder_access_collection()
    
    # Verify folder exists and user is the owner
    folder = await folders_collection.find_one({
        "_id": ObjectId(folder_id),
        "owner_id": current_user.id
    })
    
    if not folder:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Folder not found or you are not the owner"
        )
    
    # Get all access records for this folder
    access_records = await folder_access_collection.find({
        "folder_id": folder_id
    }).to_list(None)
    
    # Get user details for each access record
    shared_with = []
    for access_record in access_records:
        user = await users_collection.find_one({"_id": ObjectId(access_record["user_id"])})
        if user:
            shared_with.append({
                "user_id": str(user["_id"]),
                "user_email": user["email"],
                "user_name": user.get("name", ""),
                "access_level": access_record["access_level"],
                "granted_at": access_record["granted_at"],
                "granted_by": access_record["granted_by"]
            })
    
    return {
        "folder_id": folder_id,
        "folder_name": folder["name"],
        "is_shared": folder.get("is_shared", False),
        "shared_with": shared_with,
        "total_shared_users": len(shared_with)
    }


@router.get("/shared-with-me", response_model=PaginatedFoldersResponse)
async def get_folders_shared_with_me(
    search: str = Query("", description="Search by folder name"),
    storage_type: Optional[StorageType] = Query(None, description="Filter by storage type"),
    access_level: Optional[AccessLevel] = Query(None, description="Filter by access level"),
    include_size: bool = Query(False, description="Include total size calculation (may be slower)"),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    per_page: int = Query(20, ge=1, le=100, description="Number of items per page"),
    sort_by: str = Query("created_at", description="Sort by field"),
    sort_order: str = Query("desc", regex="^(asc|desc)$", description="Sort order"),
    current_user: User = Depends(get_current_user)
):
    """Get only folders that have been shared with the current user (not owned folders)"""
    try:
        folders_collection = await get_folders_collection()
        folder_access_collection = await get_folder_access_collection()
        
        # Get folder access records for this user
        access_query = {"user_id": current_user.id}
        if access_level:
            access_query["access_level"] = access_level.value
        
        shared_access_cursor = folder_access_collection.find(access_query)
        shared_access_records = await shared_access_cursor.to_list(None)
        shared_folder_ids = [record["folder_id"] for record in shared_access_records]
        
        if not shared_folder_ids:
            # No shared folders
            return PaginatedFoldersResponse(
                data=[],
                meta=calculate_pagination_metadata(0, page, per_page)
            )
        
        # Build query for shared folders only
        folder_query = {"_id": {"$in": [ObjectId(fid) for fid in shared_folder_ids]}}
        
        # Add search
        if search:
            folder_query["name"] = {"$regex": search, "$options": "i"}
        
        # Add storage type filter
        if storage_type:
            folder_query["storage_type"] = storage_type.value
        
        # Get total count
        total_count = await folders_collection.count_documents(folder_query)
        
        # Calculate pagination
        skip = calculate_skip_from_page(page, per_page)
        
        # Build sort
        sort_direction = 1 if sort_order == "asc" else -1
        sort_spec = [(sort_by, sort_direction)]
        
        # Get folders
        cursor = folders_collection.find(folder_query).sort(sort_spec).skip(skip).limit(per_page)
        folders = await cursor.to_list(None)
        
        # Apply dynamic status calculation
        folders = await apply_dynamic_folder_status_batch(folders)
        
        # Get folder IDs for shared users lookup
        folder_ids = [str(folder["_id"]) for folder in folders]
        shared_users_map = await get_folder_shared_users(folder_ids)
        
        # Get all unique owner IDs for shared folders to batch lookup users
        shared_folder_owner_ids = []
        for folder in folders:
            if folder["owner_id"] != current_user.id:
                shared_folder_owner_ids.append(ObjectId(folder["owner_id"]))
        
        # Batch lookup users for shared folders
        users_collection = await get_users_collection()
        owners_map = {}
        if shared_folder_owner_ids:
            owners_cursor = users_collection.find({"_id": {"$in": list(set(shared_folder_owner_ids))}})
            owners = await owners_cursor.to_list(None)
            owners_map = {str(owner["_id"]): owner for owner in owners}
        
        # Convert to response models with access level information
        folder_responses = []
        for folder in folders:
            # Convert ObjectId to string
            folder_id = str(folder["_id"])
            folder["_id"] = folder_id
            
            # Determine access level for this user
            if folder["owner_id"] == current_user.id:
                access_level = AccessLevel.ADMIN  # Owner has admin access
                shared_by_name = None  # Not applicable for owned folders
            else:
                # For shared folders, find the access level
                access_record = next((r for r in shared_access_records if r["folder_id"] == folder_id), None)
                
                if access_record:
                    # Direct sharing access
                    access_level = AccessLevel(access_record["access_level"])
                else:
                    # Fallback for root shared folders
                    access_level = AccessLevel.READ
                
                # Get the owner's full name for shared folders from the batched lookup
                owner = owners_map.get(folder["owner_id"])
                shared_by_name = owner.get("full_name", owner.get("email", "Unknown")) if owner else "Unknown"
            
            # Calculate size if requested
            if include_size:
                folder["total_size"] = await folder_service.calculate_folder_size(folder_id)
            
            # Get thumbnail
            folder["thumbnail_url"] = await get_folder_thumbnail(folder_id)
            
            # Create response with access level and shared by info
            folder_response = FolderWithAccess(**folder, access_level=access_level)
            
            # Add shared_by_name to the response if it's a shared folder
            if shared_by_name:
                folder_response.shared_by_name = shared_by_name
            
            # Add shared_with email IDs (only show if user is the owner)
            if folder["owner_id"] == current_user.id:
                folder_response.shared_with = shared_users_map.get(folder_id, [])
            
            folder_responses.append(folder_response)
        
        # Create pagination metadata
        meta = calculate_pagination_metadata(total_count, page, per_page)
        
        return PaginatedFoldersResponse(data=folder_responses, meta=meta)
        
    except Exception as e:
        logger.error(f"Error getting shared folders: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve shared folders") 


@router.post("/{folder_id}/check-conversion")
async def check_folder_conversion_status(
    folder_id: str,
    user_id: str = Depends(folder_admin_access),
    current_user: User = Depends(get_current_user)
):
    """Check if all files in the folder and all subfolders are converted to the folder's target storage type. If so, update folder status to ACTIVE."""
    try:
        folders_collection = await get_folders_collection()
        folder = await folders_collection.find_one({"_id": ObjectId(folder_id)})
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")
        target_storage_str = folder.get("conversion_to_storage")
        if not target_storage_str:
            raise HTTPException(status_code=400, detail="Folder does not have a target conversion storage type (conversion_to_storage)")
        from app.models.folder import StorageType
        target_storage = StorageType(target_storage_str)
        result = await storage_conversion_service.check_and_update_folder_conversion_status(
            folder_id=folder_id,
            target_storage=target_storage,
            apply_to_children=True
        )
        if not result.get("success"):
            # Add estimated ready time if available
            estimated_ready = folder.get("deep_archive_retrieval_ready")
            if estimated_ready:
                result["estimated_ready_time"] = estimated_ready
        return convert_objectid(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking conversion status for folder {folder_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to check conversion status") 


@router.post("/{folder_id}/make-public")
async def make_folder_public(
    folder_id: str,
    current_user: User = Depends(get_current_user)
):
    """Make a folder public and generate a public token (owner/admin only, recursive for subfolders and files)"""
    folders_collection = await get_folders_collection()
    files_collection = await get_files_collection()
    folder = await folders_collection.find_one({"_id": ObjectId(folder_id)})
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")
    if folder["owner_id"] != current_user.id and current_user.user_role != UserRole.super_admin:
        raise HTTPException(status_code=403, detail="Not authorized to make this folder public")
    

    parent_folder_id = folder.get("parent_folder_id")
    public_token = str(uuid4())
    if parent_folder_id:
        parent_folder = await folders_collection.find_one({"_id": ObjectId(parent_folder_id)})
        if parent_folder and parent_folder.get("public_token"):
            public_token = parent_folder.get("public_token")
    # Find all descendant folders (including the main one)
    all_folder_ids = [str(folder["_id"])]
    queue = [str(folder["_id"])]
    while queue:
        current_id = queue.pop()
        children = await folders_collection.find({"parent_folder_id": current_id}).to_list(None)
        for child in children:
            child_id = str(child["_id"])
            all_folder_ids.append(child_id)
            queue.append(child_id)
    # Update all folders
    await folders_collection.update_many(
        {"_id": {"$in": [ObjectId(fid) for fid in all_folder_ids]}},
        {"$set": {"is_public": True, "public_token": public_token}}
    )
    # Update all files in these folders
    await files_collection.update_many(
        {"folder_id": {"$in": all_folder_ids}},
        {"$set": {"public_token": public_token}}
    )
    return {"message": "Folder and all subfolders/files are now public", "public_token": public_token}

@router.post("/{folder_id}/make-private")
async def make_folder_private(
    folder_id: str,
    current_user: User = Depends(get_current_user)
):
    """Make a folder private and remove its public token (owner/admin only, recursive for subfolders and files)"""
    folders_collection = await get_folders_collection()
    files_collection = await get_files_collection()
    folder = await folders_collection.find_one({"_id": ObjectId(folder_id)})
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")
    if folder["owner_id"] != current_user.id and current_user.user_role != UserRole.super_admin:
        raise HTTPException(status_code=403, detail="Not authorized to make this folder private")
    # Check parent folder for public_token
    parent_folder_id = folder.get("parent_folder_id")
    if parent_folder_id:
        parent_folder = await folders_collection.find_one({"_id": ObjectId(parent_folder_id)})
        if parent_folder and parent_folder.get("public_token"):
            return {"success": False, "reason": "Parent folder is public. Cannot make private."}
    # Find all descendant folders (including the main one)
    all_folder_ids = [str(folder["_id"])]
    queue = [str(folder["_id"])]
    while queue:
        current_id = queue.pop()
        children = await folders_collection.find({"parent_folder_id": current_id}).to_list(None)
        for child in children:
            child_id = str(child["_id"])
            all_folder_ids.append(child_id)
            queue.append(child_id)
    # Update all folders
    await folders_collection.update_many(
        {"_id": {"$in": [ObjectId(fid) for fid in all_folder_ids]}},
        {"$set": {"is_public": False, "public_token": None}}
    )
    # Update all files in these folders
    await files_collection.update_many(
        {"folder_id": {"$in": all_folder_ids}},
        {"$set": {"public_token": None}}
    )
    return {"message": "Folder and all subfolders/files are now private"}

public_router = APIRouter(prefix="/public/folders", tags=["Public Folders"])

@public_router.get("/{public_token}", response_model=Folder)
async def get_public_folder(public_token: str):
    """Get public folder info by token (no auth required)"""
    folders_collection = await get_folders_collection()
    folder = await folders_collection.find_one({"public_token": public_token, "is_public": True})
    if not folder:
        raise HTTPException(status_code=404, detail="Public folder not found or not public")
    folder["_id"] = str(folder["_id"])
    return Folder(**folder)

@public_router.get("/{public_token}/path/{folder_path:path}", response_model=Folder)
async def get_public_folder_by_path(public_token: str, folder_path: str):
    """Get a public folder by path (e.g., /Photos/2024/Vacation)"""
    folders_collection = await get_folders_collection()
    # Find root folder by token
    root_folder = await folders_collection.find_one({"public_token": public_token, "is_public": True})
    if not root_folder:
        raise HTTPException(status_code=404, detail="Public folder not found or not public")
    current_folder = root_folder
    if folder_path:
        parts = [p for p in folder_path.split("/") if p]
        for part in parts:
            next_folder = await folders_collection.find_one({
                "parent_folder_id": str(current_folder["_id"]),
                "name": part,
                "is_public": True,
                "public_token": public_token
            })
            if not next_folder:
                raise HTTPException(status_code=404, detail="Folder path not found or not public")
            current_folder = next_folder
    current_folder["_id"] = str(current_folder["_id"])
    return Folder(**current_folder)

class PaginatedPublicSubfoldersResponse(BaseModel):
    data: List[Folder]
    meta: PaginationMetadata

@public_router.get("/", response_model=PaginatedPublicSubfoldersResponse)
async def get_public_subfolders(
    token: str,
    folder_id: str,
    page: int = 1,
    per_page: int = 20
):
    """Get paginated list of subfolders for a public folder by token and folder_id"""
    folders_collection = await get_folders_collection()
    # Validate parent folder is public and token matches
    parent_folder = await folders_collection.find_one({"_id": ObjectId(folder_id), "is_public": True, "public_token": token})
    if not parent_folder:
        raise HTTPException(status_code=404, detail="Public folder not found or not public")
    # Count total subfolders
    total_count = await folders_collection.count_documents({"parent_folder_id": folder_id, "is_public": True, "public_token": token})
    # Pagination
    skip = (page - 1) * per_page
    cursor = folders_collection.find({"parent_folder_id": folder_id, "is_public": True, "public_token": token}).skip(skip).limit(per_page)
    subfolders = await cursor.to_list(None)
    for subfolder in subfolders:
        subfolder["_id"] = str(subfolder["_id"])
    meta = calculate_pagination_metadata(total_count, page, per_page)
    return PaginatedPublicSubfoldersResponse(data=[Folder(**subfolder) for subfolder in subfolders], meta=meta)

# Register the public router
router.include_router(public_router) 