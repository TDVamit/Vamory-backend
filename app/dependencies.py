from fastapi import Depends, HTTPException, status, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.services.auth import get_current_user_id as get_user_id_from_token
from app.database import get_users_collection, get_folders_collection, get_folder_access_collection
from app.models.folder import AccessLevel
from bson import ObjectId
from jose import jwt
import requests
import json
from app.config import settings
import aiohttp
from datetime import datetime, timedelta, timezone

security = HTTPBearer()

AUTH0_DOMAIN = settings.AUTH0_DOMAIN 
API_AUDIENCE = settings.API_AUDIENCE 
ALGORITHMS = ["RS256"]



_jwks = None



async def _get_jwks():
    global _jwks
    if _jwks is None:
        jwks_url = f"https://{AUTH0_DOMAIN}/.well-known/jwks.json"
        async with aiohttp.ClientSession() as session:
            async with session.get(jwks_url) as resp:
                _jwks = await resp.json()
    return _jwks

async def verify_jwt(token: str) -> dict:
    jwks = await _get_jwks()
    unverified_header = jwt.get_unverified_header(token)
    rsa_key = {}
    for key in jwks["keys"]:
        if key["kid"] == unverified_header.get("kid"):
            rsa_key = {
                "kty": key["kty"],
                "kid": key["kid"],
                "use": key["use"],
                "n": key["n"],
                "e": key["e"]
            }
    if not rsa_key:
        raise HTTPException(status_code=401, detail="Invalid token: appropriate key not found")

    try:
        payload = jwt.decode(
            token,
            rsa_key,
            algorithms=ALGORITHMS,
            audience=API_AUDIENCE,
            issuer=f"https://{AUTH0_DOMAIN}/"
        )
        return payload
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Token validation error: {str(exc)}")


async def verify_public_token_func(token: str) -> dict:
    
    """Verify a public token - internal function"""
    try:
        token_data = jwt.decode(token, settings.PUBLIC_SECRET_KEY, algorithms=[settings.algorithm])
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")
    
    if not token_data:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    if token_data.get("origin") != "vamory.vadaevri.com":
        raise HTTPException(status_code=401, detail="Invalid token")
    
    created_at = token_data.get("created_at")
    if not created_at:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    # Convert created_at if it's a string timestamp
    if isinstance(created_at, str):
        try:
            created_at = datetime.fromisoformat(created_at)
        except Exception:
            raise HTTPException(status_code=401, detail="Invalid token: malformed timestamp")

    # Ensure both datetimes are offset-aware (UTC)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)

    if now - created_at > timedelta(minutes=10):
        raise HTTPException(status_code=401, detail="Token expired")

    return token_data


async def verify_public_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """FastAPI dependency to verify public token from query parameter"""
    return await verify_public_token_func(credentials.credentials)


async def verify_secret_key(secret_key: str = Query(..., description="Secret key for CDN endpoints")) -> str:
    """FastAPI dependency to verify secret key for CDN endpoints"""
    if secret_key != settings.PUBLIC_SECRET_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid secret key"
        )
    return secret_key



async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> "User":
    """Get current authenticated user model"""
    from app.models.user import User  # local import to avoid circular
    token = credentials.credentials
    payload = await verify_jwt(token)
    
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Invalid token: sub not found")
    
    # Fetch user from database
    users_collection = await get_users_collection()
    user_doc = await users_collection.find_one({"_id": sub})
    if not user_doc:
        user ={
            "_id": sub,
            "email": payload.get("https://vamory.vadaevri.comemail"),
            "full_name": payload.get("https://vamory.vadaevri.comname"),
            "user_role": "user",
            "credits": 0.0,
            "storage_used_standard": 0,
            "storage_used_archived": 0,
            "storage_used_standard_deleted": 0,
            "storage_used_archived_deleted": 0,
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
        }
        await users_collection.insert_one(user)
        user_doc = user
    else:
        if user_doc.get("full_name") != payload.get("https://vamory.vadaevri.comname"):
            await users_collection.update_one({"_id": sub}, {"$set": {"full_name": payload.get("https://vamory.vadaevri.comname")}})
            user_doc = await users_collection.find_one({"_id": sub})       

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
    payload = await verify_jwt(token)
        
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Invalid token: sub not found")
    
    # Fetch user from database
    users_collection = await get_users_collection()
    user_doc = await users_collection.find_one({"_id": sub})

    if not user_doc:
        user ={
            "_id": sub,
            "email": payload.get("https://vamory.vadaevri.comemail"),
            "full_name": payload.get("https://vamory.vadaevri.comname"),
            "user_role": "user",
            "credits": 0.0,
            "storage_used_standard": 0,
            "storage_used_archived": 0,
            "storage_used_standard_deleted": 0,
            "storage_used_archived_deleted": 0,
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
        }
        await users_collection.insert_one(user)
        user_doc = user
    else:
        if user_doc.get("full_name") != payload.get("https://vamory.vadaevri.comname"):
            await users_collection.update_one({"_id": sub}, {"$set": {"full_name": payload.get("https://vamory.vadaevri.comname")}})
            user_doc = await users_collection.find_one({"_id": sub})
    
    return str(user_doc["_id"])