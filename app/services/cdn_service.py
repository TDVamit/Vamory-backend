import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict
from app.config import settings
from app.services.s3 import s3_service
from app.database import get_cdn_upload_status_collection
from app.models.cdn_models import CdnUploadStatus, CdnUploadStatusCreate, CdnUploadStatusUpdate
import logging
import asyncio
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend
import base64
import json
import aiofiles

logger = logging.getLogger(__name__)


# Global variable to cache the private key
_private_key_cache = None
_cache_lock = asyncio.Lock()


async def _load_private_key():
    """Load and cache the private key asynchronously."""
    global _private_key_cache
    
    async with _cache_lock:
        if _private_key_cache is not None:
            return _private_key_cache
        
        cdn_private_key_path = os.getenv('CDN_PRIVATE_KEY_PATH')
        if not cdn_private_key_path:
            raise ValueError("CDN_PRIVATE_KEY_PATH environment variable not set")
        
        # Replace backslashes with forward slashes for cross-platform compatibility
        cdn_private_key_path = cdn_private_key_path.replace('\\', '/')
        
        # Normalize path to handle both Windows and Linux paths
        cdn_private_key_path = os.path.normpath(cdn_private_key_path)
        
        # If path is relative, make it absolute from the current working directory
        if not os.path.isabs(cdn_private_key_path):
            cdn_private_key_path = os.path.abspath(cdn_private_key_path)
        
        # Check if file exists
        if not os.path.exists(cdn_private_key_path):
            raise FileNotFoundError(f"Private key file not found at: {cdn_private_key_path}")
        
        # Read the private key file asynchronously
        async with aiofiles.open(cdn_private_key_path, 'rb') as key_file:
            key_data = await key_file.read()
        
        # Load the private key (this is CPU-bound, so run in executor)
        loop = asyncio.get_event_loop()
        _private_key_cache = await loop.run_in_executor(
            None,
            lambda: serialization.load_pem_private_key(
                key_data,
                password=None,
                backend=default_backend()
            )
        )
        
        return _private_key_cache


async def generate_cloudfront_signed_url(
    s3_key: str,
    expiration_hours: int = 1
) -> str:
    """
    Generate a signed CloudFront URL for private content.
    
    Args:
        s3_key: The S3 object key (path to file)
        expiration_hours: URL expiration time in hours (default: 1)
    
    Returns:
        Signed CloudFront URL as a string
    
    Environment Variables Required:
        CDN_KEY_GROUP_ID: CloudFront Key Pair ID (Access Key ID from CloudFront)
        CDN_PRIVATE_KEY_PATH: Path to the private key PEM file
        CDN_DOMAIN_NAME: CloudFront distribution domain name
    """
    # Get environment variables
    cdn_key_pair_id = os.getenv('CDN_KEY_GROUP_ID')  # This should be the Key Pair ID, not Key Group ID
    cdn_domain_name = os.getenv('CDN_DOMAIN_NAME')
    
    if not cdn_key_pair_id:
        raise ValueError("CDN_KEY_GROUP_ID environment variable not set (should contain the CloudFront Key Pair ID)")
    if not cdn_domain_name:
        raise ValueError("CDN_DOMAIN_NAME environment variable not set")
    
    # Load private key (cached after first load)
    private_key = await _load_private_key()
    
    # Construct the full URL
    resource_url = f"https://{cdn_domain_name}/{s3_key}"
    
    # Calculate expiration timestamp
    expiration_time = datetime.now(timezone.utc) + timedelta(hours=expiration_hours)
    expiration_timestamp = int(expiration_time.timestamp())
    
    # Create the policy statement
    policy = {
        "Statement": [
            {
                "Resource": resource_url,
                "Condition": {
                    "DateLessThan": {
                        "AWS:EpochTime": expiration_timestamp
                    }
                }
            }
        ]
    }
    
    # Convert policy to JSON string (no spaces)
    policy_json = json.dumps(policy, separators=(',', ':'))
    
    # Sign the policy with SHA-1 (CloudFront requirement)
    loop = asyncio.get_event_loop()
    signature = await loop.run_in_executor(
        None,
        lambda: private_key.sign(
            policy_json.encode('utf-8'),
            padding.PKCS1v15(),
            hashes.SHA1()  # CloudFront uses SHA-1, not SHA-256
        )
    )
    
    # Base64 encode and make URL-safe (CloudFront specific encoding)
    encoded_signature = base64.b64encode(signature).decode('utf-8')
    encoded_signature = encoded_signature.replace('+', '-').replace('=', '_').replace('/', '~')
    
    encoded_policy = base64.b64encode(policy_json.encode('utf-8')).decode('utf-8')
    encoded_policy = encoded_policy.replace('+', '-').replace('=', '_').replace('/', '~')
    
    # Construct the signed URL with proper parameter order
    signed_url = (
        f"{resource_url}"
        f"?Policy={encoded_policy}"
        f"&Signature={encoded_signature}"
        f"&Key-Pair-Id={cdn_key_pair_id}"
    )
    
    return signed_url


async def generate_cloudfront_signed_cookies(
    expiration_hours: int = 1,
    resource_path: str = "*"
) -> Dict[str, str]:
    """
    Generate CloudFront signed cookies for accessing private content.
    
    Args:
        expiration_hours: Cookie expiration time in hours (default: 1)
        resource_path: Path pattern to allow access to (default: "*" for all resources)
    
    Returns:
        Dictionary with cookie names and values:
        {
            "CloudFront-Policy": "<encoded_policy>",
            "CloudFront-Signature": "<signature>",
            "CloudFront-Key-Pair-Id": "<key_pair_id>"
        }
    
    Environment Variables Required:
        CDN_KEY_GROUP_ID: CloudFront Key Pair ID
        CDN_PRIVATE_KEY_PATH: Path to the private key PEM file
        CDN_DOMAIN_NAME: CloudFront distribution domain name
    """
    # Get environment variables
    cdn_key_pair_id = os.getenv('CDN_KEY_GROUP_ID')
    cdn_domain_name = os.getenv('CDN_DOMAIN_NAME')
    
    if not cdn_key_pair_id:
        raise ValueError("CDN_KEY_GROUP_ID environment variable not set")
    if not cdn_domain_name:
        raise ValueError("CDN_DOMAIN_NAME environment variable not set")
    
    # Load private key (cached after first load)
    private_key = await _load_private_key()
    
    # Construct the resource URL for the policy
    # Using wildcard to allow access to all resources under the domain
    resource_url = f"https://d1fh10einhx93s.cloudfront.net/{resource_path}"
    
    # Calculate expiration timestamp
    expiration_time = datetime.now(timezone.utc) + timedelta(hours=expiration_hours)
    expiration_timestamp = int(expiration_time.timestamp())
    
    # Create the policy statement (canned policy for cookies)
    policy = {
        "Statement": [
            {
                "Resource": resource_url,
                "Condition": {
                    "DateLessThan": {
                        "AWS:EpochTime": expiration_timestamp
                    }
                }
            }
        ]
    }
    
    # Convert policy to JSON string (no spaces)
    policy_json = json.dumps(policy, separators=(',', ':'))
    
    # Sign the policy with SHA-1 (CloudFront requirement)
    loop = asyncio.get_event_loop()
    signature = await loop.run_in_executor(
        None,
        lambda: private_key.sign(
            policy_json.encode('utf-8'),
            padding.PKCS1v15(),
            hashes.SHA1()
        )
    )
    
    # Base64 encode and make URL-safe (CloudFront specific encoding)
    encoded_signature = base64.b64encode(signature).decode('utf-8')
    encoded_signature = encoded_signature.replace('+', '-').replace('=', '_').replace('/', '~')
    
    encoded_policy = base64.b64encode(policy_json.encode('utf-8')).decode('utf-8')
    encoded_policy = encoded_policy.replace('+', '-').replace('=', '_').replace('/', '~')
    
    # Return the signed cookies
    return {
        "CloudFront-Policy": encoded_policy,
        "CloudFront-Signature": encoded_signature,
        "CloudFront-Key-Pair-Id": cdn_key_pair_id
    }


class CdnService:
    """Service for handling CDN operations"""
    
    def __init__(self):
        self.cdn_domain = settings.CDN_DOMAIN_NAME
        self.key_group_id = settings.CDN_KEY_GROUP_ID
        self.private_key_path = settings.CDN_PRIVATE_KEY_PATH
    
    async def generate_cdn_url(self, s3_key: str, expiration_hours: int = 1) -> str:
        """Generate signed CloudFront CDN URL for a given S3 key"""
        return await generate_cloudfront_signed_url(s3_key, expiration_hours)
    
    async def generate_signed_cookies(self, expiration_hours: int = 1, resource_path: str = "*") -> Dict[str, str]:
        """Generate signed CloudFront cookies"""
        return await generate_cloudfront_signed_cookies(expiration_hours, resource_path)
    
    async def create_upload_status(self, s3_key: str) -> str:
        """Create a new CDN upload status record"""
        try:
            collection = await get_cdn_upload_status_collection()
            if collection is None:
                logger.error("Failed to get CDN upload status collection")
                raise Exception("Database connection failed")
            
            # Generate unique ID
            upload_id = str(uuid.uuid4())
            
            # Create status record
            status_data = CdnUploadStatusCreate(s3_key=s3_key)
            status_doc = {
                "_id": upload_id,
                **status_data.dict(),
                "created_at": datetime.now(timezone.utc),
                "status": "pending"
            }
            
            result = await collection.insert_one(status_doc)
            if result.inserted_id:
                logger.info(f"Created CDN upload status: {upload_id}")
                return upload_id
            else:
                raise Exception("Failed to create CDN upload status")
                
        except Exception as e:
            logger.exception(f"Error creating CDN upload status: {e}")
            raise Exception(f"Failed to create CDN upload status: {str(e)}")
    
    async def update_upload_status(
        self, 
        upload_id: str, 
        status: str, 
        cdn_url: Optional[str] = None, 
        error: Optional[str] = None
    ) -> bool:
        """Update CDN upload status"""
        try:
            collection = await get_cdn_upload_status_collection()
            if collection is None:
                logger.error("Failed to get CDN upload status collection")
                return False
            
            update_data = {
                "status": status,
                "updated_at": datetime.now(timezone.utc)
            }
            
            if cdn_url:
                update_data["cdn_url"] = cdn_url
                update_data["uploaded_at"] = datetime.now(timezone.utc)
            
            if error:
                update_data["error"] = error
            
            result = await collection.update_one(
                {"_id": upload_id},
                {"$set": update_data}
            )
            
            if result.modified_count > 0:
                logger.info(f"Updated CDN upload status: {upload_id} -> {status}")
                return True
            else:
                logger.warning(f"No CDN upload status record found for ID: {upload_id}")
                return False
                
        except Exception as e:
            logger.exception(f"Error updating CDN upload status: {e}")
            return False
    
    async def get_upload_status(self, upload_id: str) -> Optional[CdnUploadStatus]:
        """Get CDN upload status by ID"""
        try:
            collection = await get_cdn_upload_status_collection()
            if collection is None:
                return None
            
            doc = await collection.find_one({"_id": upload_id})
            if doc:
                return CdnUploadStatus(**doc)
            return None
            
        except Exception as e:
            logger.exception(f"Error getting CDN upload status: {e}")
            return None
    
    async def get_upload_statuses_paginated(
        self, 
        page: int = 1, 
        per_page: int = 20,
        status_filter: Optional[str] = None
    ) -> tuple[list, dict]:
        """Get paginated CDN upload statuses"""
        try:
            collection = await get_cdn_upload_status_collection()
            if collection is None:
                return [], {"total": 0, "page": page, "per_page": per_page, "total_pages": 0}
            
            # Build query
            query = {}
            if status_filter:
                query["status"] = status_filter
            
            # Get total count
            total_count = await collection.count_documents(query)
            
            # Calculate pagination
            skip = (page - 1) * per_page
            total_pages = (total_count + per_page - 1) // per_page
            
            # Get documents
            cursor = collection.find(query).sort("created_at", -1).skip(skip).limit(per_page)
            docs = await cursor.to_list(None)
            
            # Convert to models
            statuses = [CdnUploadStatus(**doc) for doc in docs]
            
            # Pagination metadata
            meta = {
                "total": total_count,
                "page": page,
                "per_page": per_page,
                "total_pages": total_pages
            }
            
            return statuses, meta
            
        except Exception as e:
            logger.exception(f"Error getting paginated CDN upload statuses: {e}")
            return [], {"total": 0, "page": page, "per_page": per_page, "total_pages": 0}
    
    async def get_cdn_urls_paginated(
        self, 
        page: int = 1, 
        per_page: int = 20,
        expiration_hours: int = 24
    ) -> tuple[list, dict]:
        """Get paginated CDN URLs (only uploaded files) with unsigned URLs (auth via cookies)"""
        try:
            collection = await get_cdn_upload_status_collection()
            if collection is None:
                return [], {"total": 0, "page": page, "per_page": per_page, "total_pages": 0}
            
            # Query for uploaded files only (no need to check for cdn_url existence)
            query = {"status": {"$in": ["uploaded", "COMPLETE", "completed","started"]}}
            
            # Get total count
            total_count = await collection.count_documents(query)
            
            # Calculate pagination
            skip = (page - 1) * per_page
            total_pages = (total_count + per_page - 1) // per_page
            
            # Get documents
            cursor = collection.find(query).sort("uploaded_at", -1).skip(skip).limit(per_page)
            docs = await cursor.to_list(None)
            
            # Convert to response format with unsigned URLs (cookies will handle auth)
            cdn_urls = []
            for doc in docs:
                # Generate unsigned URLs - cookies will provide authentication
                status = doc["status"]
                m3u8_url = None
                if status in ["COMPLETE", "completed"]:
                    m3u8_url_key = doc["s3_key"].split("/")[-1]
                    m3u8_url_key = m3u8_url_key.split(".")[0]
                    m3u8_url_key = m3u8_url_key + ".m3u8"
                    m3u8_url_key = "HLS_Converted/" + m3u8_url_key
                    m3u8_url = f"https://d1fh10einhx93s.cloudfront.net/{m3u8_url_key}"
  
                # Return unsigned URL - signed cookies will authorize access
                unsigned_url = f"https://d1fh10einhx93s.cloudfront.net/{doc['s3_key']}"
                
                cdn_urls.append({
                    "id": str(doc["_id"]),
                    "s3_key": doc["s3_key"],
                    "cdn_url": unsigned_url,
                    "m3u8_url": m3u8_url,
                    "created_at": doc["created_at"],
                    "uploaded_at": doc.get("uploaded_at"),
                    "status": doc["status"]
                })
            
            # Pagination metadata
            meta = {
                "total": total_count,
                "page": page,
                "per_page": per_page,
                "total_pages": total_pages
            }
            
            return cdn_urls, meta
            
        except Exception as e:
            logger.exception(f"Error getting paginated CDN URLs: {e}")
            return [], {"total": 0, "page": page, "per_page": per_page, "total_pages": 0}


# Create a global instance
cdn_service = CdnService()
