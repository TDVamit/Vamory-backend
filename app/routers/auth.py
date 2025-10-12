from fastapi import APIRouter, Depends, HTTPException, status, Form, Query, UploadFile, File, BackgroundTasks, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from datetime import timedelta
from typing import List, Optional
from bson import ObjectId
from app.models.user import User, UserCreate, UserInDB, Token, UserResponse, LogoutResponse, PaginatedUsersResponse, UserUpdate, UserRole
from app.services.auth import (
    authenticate_user, create_access_token, create_refresh_token, 
    save_refresh_token, verify_refresh_token, revoke_refresh_token,
    get_password_hash
)
from app.dependencies import get_current_user
from app.database import get_users_collection
from app.config import settings
from app.utils import calculate_pagination_metadata, calculate_skip_from_page, convert_objectid
import base64
from app.services.thumbnail import thumbnail_service
from io import BytesIO
import aiohttp
from app.services.s3 import s3_service
import uuid
from app.models.folder import StorageType


router = APIRouter(prefix="/auth", tags=["Authentication"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def deny_if_viewer(current_user: User):
    if hasattr(current_user, 'user_role') and str(current_user.user_role) == 'viewer':
        raise HTTPException(status_code=403, detail="Viewers are not allowed to upload or create resources.")





@router.get("/me")
async def get_current_user_info(current_user: User = Depends(get_current_user)):
    """Get current user information"""
    return current_user



async def get_mgmt_api_token():
    """Obtain an Auth0 Management API token via client-credentials grant (async)."""
    token_url = f"https://{settings.AUTH0_CANONICAL_DOMAIN}/oauth/token"
    payload = {
        "grant_type": "client_credentials",
        "client_id": settings.AUTH0_CLIENT_ID,
        "client_secret": settings.AUTH0_CLIENT_SECRET,
        "audience": f"https://{settings.AUTH0_CANONICAL_DOMAIN}/api/v2/"
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(token_url, json=payload) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise Exception(f"Failed to get management API token: {text}")
            data = await resp.json()
            return data["access_token"]



@router.patch("/user/profile")
async def update_user_profile(
    name: str = Form(None),
    picture: UploadFile | None = File(None),
    user = Depends(get_current_user)
):
    auth0_id = user.id  # This is the user_id for Auth0.
    if not (name or picture):
        raise HTTPException(status_code=400, detail="No fields to update.")

    users_collection = await get_users_collection()
    update_data = {}
    db_update_data = {}
    
    # Handle name update
    if name is not None:
        update_data["name"] = name
        db_update_data["full_name"] = name
    
    # Handle picture update
    if picture is not None:
        # Validate file type
        if not picture.content_type or not picture.content_type.startswith('image/'):
            raise HTTPException(status_code=400, detail="Only image files are allowed.")
        
        # Read and process the image
        content = await picture.read()
        image_stream = BytesIO(content)
        
        # Compress to 360p and convert to WebP
        processed_image = thumbnail_service.resize_image(image_stream, 360, 360, output_format='WEBP')
        if not processed_image:
            raise HTTPException(status_code=400, detail="Failed to process image.")
        
        # Get current user data to check for existing profile pic S3 key
        current_user_data = await users_collection.find_one({"_id": user.id})
        old_profile_pic_s3_key = current_user_data.get("profile_pic_s3_key") if current_user_data else None
        
        # Delete old profile picture from S3 if it exists
        if old_profile_pic_s3_key:
            try:
                await s3_service.delete_file(old_profile_pic_s3_key)
                print(f"Deleted old profile picture from S3: {old_profile_pic_s3_key}")
            except Exception as e:
                print(f"Failed to delete old profile picture from S3: {str(e)}")
        
        # Generate S3 key for profile picture in public logo folder
        unique_id = str(uuid.uuid4())
        s3_key = f"logo/{user.id}/{unique_id}.webp"
        
        # Upload to S3 with STANDARD storage class (for fast access)
        try:
            s3_url = await s3_service.upload_file(
                file_content=processed_image,
                s3_key=s3_key,
                content_type="image/webp",
                storage_type=StorageType.STANDARD
            )
            print(f"Uploaded profile picture to S3 logo folder: {s3_key}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to upload image to S3: {str(e)}")
        
        # Generate public URL (lifetime access via bucket policy)
        public_url = f"https://vamory-s3-bucket-by-vamit.s3.{settings.aws_region}.amazonaws.com/{s3_key}"
        
        # Update Auth0 with public URL (lifetime access)
        update_data["picture"] = public_url
        
        # Save S3 key in database for future cleanup
        db_update_data["profile_pic_s3_key"] = s3_key
        db_update_data["profile_pic_url"] = public_url

    # Update database first
    if db_update_data:
        await users_collection.update_one({"_id": user.id}, {"$set": db_update_data})

    # Update Auth0 if there's data to update
    if update_data:
        token = await get_mgmt_api_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        mgmt_url = f"https://{settings.AUTH0_CANONICAL_DOMAIN}/api/v2/users/{auth0_id}"
        async with aiohttp.ClientSession() as session:
            async with session.patch(mgmt_url, headers=headers, json=update_data) as resp:
                if resp.status < 200 or resp.status >= 300:
                    text = await resp.text()
                    raise HTTPException(status_code=resp.status, detail=f"Auth0 update failed: {text}")

    return {"message": "Profile updated successfully."}


@router.patch("/users/{user_id}/role", response_model=UserResponse)
async def update_user_role(
    user_id: str,
    user_role: UserRole,
    current_user: User = Depends(get_current_user)
):
    """Admin-only: Update another user's role."""
    if current_user.user_role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Only admins can change user roles.")
    users_collection = await get_users_collection()
    await users_collection.update_one({"_id": user_id}, {"$set": {"user_role": user_role}})
    user_doc = await users_collection.find_one({"_id": user_id})
    if not user_doc:
        raise HTTPException(status_code=404, detail="User not found")
    user_doc["_id"] = str(user_doc["_id"])
    return UserResponse(**user_doc)


@router.post("/profile-pic", response_model=UserResponse)
async def upload_profile_pic(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user)
):
    """Upload or update the user's profile picture - uses S3 storage with presigned URLs"""
    deny_if_viewer(current_user)
    users_collection = await get_users_collection()
    
    # Validate file type
    if not file.content_type or not file.content_type.startswith('image/'):
        raise HTTPException(status_code=400, detail="Only image files are allowed.")
    
    # Read and process the image
    content = await file.read()
    image_stream = BytesIO(content)
    
    # Compress to 360p and convert to WebP
    processed_image = thumbnail_service.resize_image(image_stream, 360, 360, output_format='WEBP')
    if not processed_image:
        raise HTTPException(status_code=400, detail="Failed to process image.")
    
    # Get current user data to check for existing profile pic S3 key
    current_user_data = await users_collection.find_one({"_id": current_user.id})
    old_profile_pic_s3_key = current_user_data.get("profile_pic_s3_key") if current_user_data else None
    
    # Delete old profile picture from S3 if it exists
    if old_profile_pic_s3_key:
        try:
            from app.services.s3 import s3_service
            await s3_service.delete_file(old_profile_pic_s3_key)
            print(f"Deleted old profile picture from S3: {old_profile_pic_s3_key}")
        except Exception as e:
            print(f"Failed to delete old profile picture from S3: {str(e)}")
    
    unique_id = str(uuid.uuid4())
    s3_key = f"logo/{current_user.id}/{unique_id}.webp"
    
    try:
        s3_url = await s3_service.upload_file(
            file_content=processed_image,
            s3_key=s3_key,
            content_type="image/webp",
            storage_type=StorageType.STANDARD
        )
        print(f"Uploaded profile picture to S3 logo folder: {s3_key}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload image to S3: {str(e)}")
    
    public_url = f"https://vamory-s3-bucket-by-vamit.s3.{settings.aws_region}.amazonaws.com/{s3_key}"
    
    b64 = base64.b64encode(processed_image.getvalue()).decode('utf-8')
    b64_str = f"data:image/webp;base64,{b64}"
    
    await users_collection.update_one(
        {"_id": current_user.id}, 
        {"$set": {
            "profile_pic": b64_str,  
            "profile_pic_s3_key": s3_key,
            "profile_pic_url": public_url  
        }}
    )
    
    user_doc = await users_collection.find_one({"_id": current_user.id})
    user_doc["_id"] = str(user_doc["_id"])
    return UserResponse(**user_doc)


@router.delete("/profile-pic", response_model=UserResponse)
async def delete_profile_pic(current_user: User = Depends(get_current_user)):
    """Delete the user's profile picture from both database and S3"""
    users_collection = await get_users_collection()
    
    # Get current user data to check for existing profile pic S3 key
    current_user_data = await users_collection.find_one({"_id": current_user.id})
    profile_pic_s3_key = current_user_data.get("profile_pic_s3_key") if current_user_data else None
    
    # Delete profile picture from S3 if it exists
    if profile_pic_s3_key:
        try:
            from app.services.s3 import s3_service
            await s3_service.delete_file(profile_pic_s3_key)
            print(f"Deleted profile picture from S3: {profile_pic_s3_key}")
        except Exception as e:
            print(f"Failed to delete profile picture from S3: {str(e)}")
    
    # Clear all profile picture fields in database
    await users_collection.update_one(
        {"_id": current_user.id}, 
        {"$set": {
            "profile_pic": None,
            "profile_pic_s3_key": None,
            "profile_pic_url": None
        }}
    )
    
    user_doc = await users_collection.find_one({"_id": current_user.id})
    user_doc["_id"] = str(user_doc["_id"])
    return UserResponse(**user_doc)


@router.get("/users", response_model=PaginatedUsersResponse)
async def get_users(
    search: Optional[str] = Query(None, description="Search by name or email"),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    per_page: int = Query(20, ge=1, le=100, description="Number of users per page"),
    sort_by: str = Query("full_name", description="Field to sort by: full_name, email, created_at"),
    sort_order: str = Query("asc", description="Sort order: asc or desc"),
    current_user: User = Depends(get_current_user)
):
    """Get all users with search, pagination, and sorting
    
    This endpoint allows searching for users by name or email with full pagination and sorting.
    Useful for user management and finding users when sharing folders.
    """
    users_collection = await get_users_collection()
    
    # Build search query
    query = {"_id": {"$ne": current_user.id}}  
    
    if search:
        # Case-insensitive search in full_name and email
        search_pattern = {"$regex": search, "$options": "i"}
        query["$or"] = [
            {"full_name": search_pattern},
            {"email": search_pattern}
        ]
    
    # Validate and set sort parameters
    allowed_sort_fields = ["full_name", "email", "created_at", "updated_at"]
    if sort_by not in allowed_sort_fields:
        sort_by = "full_name"
    
    sort_direction = 1 if sort_order.lower() == "asc" else -1
    
    # Get total count for pagination metadata
    total_count = await users_collection.count_documents(query)
    
    # Calculate pagination metadata
    pagination_meta = calculate_pagination_metadata(total_count, page, per_page)
    
    # Calculate skip for database query
    skip = calculate_skip_from_page(page, per_page)
    
    # Get users with pagination and sorting
    cursor = users_collection.find(
        query,
        {"hashed_password": 0}  # Exclude sensitive data
    ).skip(skip).limit(per_page).sort(sort_by, sort_direction)
    
    users = await cursor.to_list(None)
    
    # Convert to response format
    result = []
    for user_doc in users:
        user_doc["_id"] = str(user_doc["_id"])
        result.append(UserResponse(**user_doc))
    
    return PaginatedUsersResponse(
        data=result,
        meta=pagination_meta
    )


@router.get("/users/search", response_model=List[UserResponse])
async def search_users(
    q: str = Query(..., min_length=2, description="Search query (minimum 2 characters)"),
    limit: int = Query(10, ge=1, le=50, description="Maximum number of results"),
    current_user: User = Depends(get_current_user)
):
    """Search users by name or email - optimized for quick lookups
    
    This is a more focused search endpoint for autocomplete/suggestion features.
    """
    users_collection = await get_users_collection()
    
    # Build search query with more flexible matching
    search_pattern = {"$regex": q, "$options": "i"}
    query = {
        "is_active": True,
        "$or": [
            {"full_name": search_pattern},
            {"email": search_pattern}
        ]
    }
    
    # Get users with limit, sorted by relevance (exact matches first)
    users = await users_collection.find(
        query,
        {"hashed_password": 0}  # Exclude sensitive data
    ).limit(limit).to_list(None)
    
    # Sort results: exact matches first, then by name
    def sort_key(user):
        name_lower = user["full_name"].lower()
        email_lower = user["email"].lower()
        query_lower = q.lower()
        
        # Exact matches first
        if name_lower == query_lower or email_lower == query_lower:
            return (0, name_lower)
        # Starts with query
        elif name_lower.startswith(query_lower) or email_lower.startswith(query_lower):
            return (1, name_lower)
        # Contains query
        else:
            return (2, name_lower)
    
    users.sort(key=sort_key)
    
    # Convert to response format
    result = []
    for user_doc in users:
        user_doc["_id"] = str(user_doc["_id"])
        result.append(UserResponse(**user_doc))
    
    return result 