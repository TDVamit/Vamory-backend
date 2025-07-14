from datetime import datetime, timedelta, timezone
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import HTTPException, status
import secrets
from app.config import settings
from app.models.user import TokenData, RefreshTokenInDB
from app.database import get_users_collection, get_refresh_tokens_collection

# Alternative password hashing with argon2 (if bcrypt issues persist)
# Uncomment these lines and comment out the bcrypt imports:

# from passlib.context import CryptContext
# pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

# Current bcrypt implementation (default):
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash"""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash a password"""
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)
    return encoded_jwt


def create_refresh_token() -> str:
    """Create a random refresh token"""
    return secrets.token_urlsafe(32)


async def save_refresh_token(user_id: str, refresh_token: str) -> None:
    """Save refresh token to database"""
    refresh_tokens_collection = await get_refresh_tokens_collection()
    
    # Remove old refresh tokens for this user
    await refresh_tokens_collection.delete_many({"user_id": user_id})
    
    # Create new refresh token
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)
    refresh_token_data = RefreshTokenInDB(
        user_id=user_id,
        token=refresh_token,
        expires_at=expires_at
    )
    
    await refresh_tokens_collection.insert_one(refresh_token_data.dict(by_alias=True))


async def verify_refresh_token(refresh_token: str) -> Optional[str]:
    """Verify refresh token and return user_id if valid"""
    refresh_tokens_collection = await get_refresh_tokens_collection()
    
    token_data = await refresh_tokens_collection.find_one({"token": refresh_token})
    
    if not token_data:
        return None
    
    # Check if token is expired
    expires_at = token_data["expires_at"]
    now = datetime.now(timezone.utc)
    
    # Ensure expires_at is timezone-aware for comparison
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    
    if now > expires_at:
        # Remove expired token
        await refresh_tokens_collection.delete_one({"token": refresh_token})
        return None
    
    return token_data["user_id"]


async def revoke_refresh_token(refresh_token: str) -> None:
    """Revoke a refresh token"""
    refresh_tokens_collection = await get_refresh_tokens_collection()
    await refresh_tokens_collection.delete_one({"token": refresh_token})


def verify_token(token: str) -> Optional[TokenData]:
    """Verify JWT token and return token data"""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        email: str = payload.get("sub")
        user_id: str = payload.get("user_id")
        
        if email is None or user_id is None:
            return None
        
        return TokenData(email=email, user_id=user_id)
    except JWTError:
        return None


async def authenticate_user(email: str, password: str):
    """Authenticate user with email and password"""
    users_collection = await get_users_collection()
    user = await users_collection.find_one({"email": email})
    
    if not user:
        return False
    
    if not verify_password(password, user["hashed_password"]):
        return False
    
    if not user.get("is_active", True):
        return False
    
    return user


async def get_current_user_id(token: str) -> str:
    """Get current user ID from JWT token"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    token_data = verify_token(token)
    if token_data is None:
        raise credentials_exception
    
    users_collection = await get_users_collection()
    user = await users_collection.find_one({"email": token_data.email})
    
    if user is None:
        raise credentials_exception
    
    return str(user["_id"]) 