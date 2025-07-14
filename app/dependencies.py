from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.services.auth import get_current_user_id as get_user_id_from_token
from app.database import get_users_collection, get_folders_collection, get_folder_access_collection
from app.models.folder import AccessLevel
from bson import ObjectId

security = HTTPBearer()


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> "User":
    """Get current authenticated user model"""
    from app.models.user import User  # local import to avoid circular
    token = credentials.credentials
    user_id = await get_user_id_from_token(token)

    # Fetch user from database
    users_collection = await get_users_collection()
    user_doc = await users_collection.find_one({"_id": ObjectId(user_id)})
    if not user_doc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found"
        )
    # Convert ObjectId to str
    user_doc["_id"] = str(user_doc["_id"])
    return User(**user_doc)


async def verify_folder_access(folder_id: str, user_id: str, required_access: AccessLevel = AccessLevel.READ) -> bool:
    """Verify user has required access to folder"""
    folders_collection = await get_folders_collection()
    folder_access_collection = await get_folder_access_collection()
    
    # Check if user owns the folder
    folder = await folders_collection.find_one({"_id": ObjectId(folder_id)})
    if not folder:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Folder not found"
        )
    
    # Owner has all access
    if folder["owner_id"] == user_id:
        return True
    
    # Check shared access
    access_record = await folder_access_collection.find_one({
        "folder_id": folder_id,
        "user_id": user_id
    })
    
    if not access_record:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to folder"
        )
    
    # Check access level
    user_access = AccessLevel(access_record["access_level"])
    
    # Define access hierarchy
    access_hierarchy = {
        AccessLevel.READ: 1,
        AccessLevel.WRITE: 2,
        AccessLevel.ADMIN: 3
    }
    
    if access_hierarchy[user_access] < access_hierarchy[required_access]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Insufficient access level. Required: {required_access.value}"
        )
    
    return True


class FolderAccessChecker:
    def __init__(self, required_access: AccessLevel = AccessLevel.READ):
        self.required_access = required_access
    
    async def __call__(self, folder_id: str, current_user: "User" = Depends(get_current_user)) -> str:
        from app.models.user import User  # local import to avoid circular
        await verify_folder_access(folder_id, current_user.id, self.required_access)
        return current_user.id


# Common dependency instances
folder_read_access = FolderAccessChecker(AccessLevel.READ)
folder_write_access = FolderAccessChecker(AccessLevel.WRITE)
folder_admin_access = FolderAccessChecker(AccessLevel.ADMIN)


async def get_current_user_id(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    """Get current authenticated user ID as string"""
    token = credentials.credentials
    user_id = await get_user_id_from_token(token)
    return user_id 