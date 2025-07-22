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

router = APIRouter(prefix="/auth", tags=["Authentication"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def deny_if_viewer(current_user: User):
    if hasattr(current_user, 'user_role') and str(current_user.user_role) == 'viewer':
        raise HTTPException(status_code=403, detail="Viewers are not allowed to upload or create resources.")


@router.post("/register", response_model=UserResponse)
async def register(user: UserCreate):
    """Register a new user"""
    users_collection = await get_users_collection()
    
    # Check if user already exists
    existing_user = await users_collection.find_one({"email": user.email})
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Create new user
    hashed_password = get_password_hash(user.password)
    user_in_db = UserInDB(
        **user.dict(exclude={"password"}),
        hashed_password=hashed_password
    )
    
    result = await users_collection.insert_one(user_in_db.dict(by_alias=True))
    
    if result.inserted_id:
        # Fetch the created user
        created_user = await users_collection.find_one({"_id": result.inserted_id})
        created_user["_id"] = str(created_user["_id"])
        return UserResponse(**created_user)
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create user"
        )


@router.post("/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    """Login user and return tokens"""
    user = await authenticate_user(form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Create tokens
    access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
    access_token = create_access_token(
        data={"sub": user["email"], "user_id": str(user["_id"])},
        expires_delta=access_token_expires
    )
    refresh_token = create_refresh_token()
    
    # Save refresh token
    await save_refresh_token(str(user["_id"]), refresh_token)
    
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }


@router.post("/refresh", response_model=Token)
async def refresh_token(refresh_token: str = Form(...)):
    """Refresh access token using refresh token"""
    user_id = await verify_refresh_token(refresh_token)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token"
        )
    
    # Get user info
    users_collection = await get_users_collection()
    user = await users_collection.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found"
        )
    
    # Create new tokens
    access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
    access_token = create_access_token(
        data={"sub": user["email"], "user_id": str(user["_id"])},
        expires_delta=access_token_expires
    )
    new_refresh_token = create_refresh_token()
    
    # Replace old refresh token
    await save_refresh_token(str(user["_id"]), new_refresh_token)
    
    return {
        "access_token": access_token,
        "refresh_token": new_refresh_token,
        "token_type": "bearer"
    }


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    refresh_token: str = Form(...),
    current_user: User = Depends(get_current_user)
):
    """Logout user by revoking refresh token"""
    await revoke_refresh_token(refresh_token)
    return LogoutResponse(message="Successfully logged out")


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(current_user: User = Depends(get_current_user)):
    """Get current user information"""
    return current_user


@router.patch("/me", response_model=UserResponse)
async def update_current_user(
    user_update: UserUpdate,
    current_user: User = Depends(get_current_user)
):
    """Update current user's details. Cannot update user_role here."""
    deny_if_viewer(current_user)
    users_collection = await get_users_collection()
    update_data = user_update.dict(exclude_unset=True)
    # Remove user_role if present
    update_data.pop("user_role", None)
    await users_collection.update_one({"_id": ObjectId(current_user.id)}, {"$set": update_data})
    user_doc = await users_collection.find_one({"_id": ObjectId(current_user.id)})
    user_doc["_id"] = str(user_doc["_id"])
    return UserResponse(**user_doc)


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
    await users_collection.update_one({"_id": ObjectId(user_id)}, {"$set": {"user_role": user_role}})
    user_doc = await users_collection.find_one({"_id": ObjectId(user_id)})
    if not user_doc:
        raise HTTPException(status_code=404, detail="User not found")
    user_doc["_id"] = str(user_doc["_id"])
    return UserResponse(**user_doc)


@router.post("/profile-pic", response_model=UserResponse)
async def upload_profile_pic(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user)
):
    """Upload or update the user's profile picture (webp, 360p, base64)"""
    deny_if_viewer(current_user)
    users_collection = await get_users_collection()
    # Read file content
    content = await file.read()
    # Convert and resize to webp, 360p
    image_stream = BytesIO(content)
    processed = thumbnail_service.resize_image(image_stream, 360, 360, output_format='WEBP')
    if not processed:
        raise HTTPException(status_code=400, detail="Invalid image or failed to process.")
    # Encode as base64
    b64 = base64.b64encode(processed.getvalue()).decode('utf-8')
    b64_str = f"data:image/webp;base64,{b64}"
    # Update user in DB
    await users_collection.update_one({"_id": ObjectId(current_user.id)}, {"$set": {"profile_pic": b64_str}})
    user_doc = await users_collection.find_one({"_id": ObjectId(current_user.id)})
    user_doc["_id"] = str(user_doc["_id"])
    return UserResponse(**user_doc)


@router.delete("/profile-pic", response_model=UserResponse)
async def delete_profile_pic(current_user: User = Depends(get_current_user)):
    """Delete the user's profile picture"""
    users_collection = await get_users_collection()
    await users_collection.update_one({"_id": ObjectId(current_user.id)}, {"$set": {"profile_pic": None}})
    user_doc = await users_collection.find_one({"_id": ObjectId(current_user.id)})
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
    query = {"is_active": True, "_id": {"$ne": ObjectId(current_user.id)}}  # Exclude current user
    
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