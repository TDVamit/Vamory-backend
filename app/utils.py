import os
import math
from typing import Union
from app.models.common import PaginationMetadata
from app.services.storage_conversion import storage_conversion_service
from app.models.folder import FolderStatus, StorageType
from bson import ObjectId


def format_file_size(size_bytes: int) -> str:
    """
    Format file size in bytes to human-readable format.
    
    Args:
        size_bytes: Size in bytes
        
    Returns:
        Formatted string like "1.2 MB", "3.5 GB", etc.
    """
    if size_bytes == 0:
        return "0 B"
    
    size_names = ["B", "KB", "MB", "GB", "TB", "PB"]
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_names[i]}"


def get_file_size_category(size_bytes: int) -> str:
    """
    Categorize file size for analysis.
    
    Args:
        size_bytes: Size in bytes
        
    Returns:
        Category string: "tiny", "small", "medium", "large", "huge"
    """
    if size_bytes < 1024:  # < 1 KB
        return "tiny"
    elif size_bytes < 1024 * 1024:  # < 1 MB
        return "small"
    elif size_bytes < 50 * 1024 * 1024:  # < 50 MB
        return "medium"
    elif size_bytes < 1024 * 1024 * 1024:  # < 1 GB
        return "large"
    else:  # >= 1 GB
        return "huge" 


def calculate_pagination_metadata(total_count: int, page: int, per_page: int) -> PaginationMetadata:
    """Calculate pagination metadata for consistent responses"""
    page_count = (total_count + per_page - 1) // per_page if total_count > 0 else 0
    has_next = page < page_count
    has_prev = page > 1
    next_page = page + 1 if has_next else None
    prev_page = page - 1 if has_prev else None
    
    return PaginationMetadata(
        total_count=total_count,
        page_count=page_count,
        current_page=page,
        per_page=per_page,
        has_next=has_next,
        has_prev=has_prev,
        next_page=next_page,
        prev_page=prev_page
    )


def calculate_skip_from_page(page: int, per_page: int) -> int:
    """Calculate skip value from page and per_page parameters"""
    if page < 1:
        page = 1
    return (page - 1) * per_page


async def apply_dynamic_folder_status(folder_doc: dict) -> dict:
    """
    Apply dynamic status calculation to a folder document based on time
    
    This calculates the real-time status for Deep Archive retrievals without
    needing background workers or job queues.
    """
    if not folder_doc:
        return folder_doc
    
    # Preserve 'copying' status if set
    if folder_doc.get("status") == "copying":
        folder_doc["effective_storage_type"] = folder_doc.get("storage_type")
        return folder_doc

    # Calculate current status and effective storage type
    current_status, effective_storage = await storage_conversion_service.calculate_folder_status(folder_doc)
    
    # Update the document with calculated values
    folder_doc["status"] = current_status.value
    folder_doc["effective_storage_type"] = effective_storage.value
    
    # Add timing information for Deep Archive retrievals
    if (folder_doc.get("deep_archive_retrieval_start") and 
        folder_doc.get("deep_archive_retrieval_ready")):
        
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        
        retrieval_start = folder_doc["deep_archive_retrieval_start"]
        retrieval_ready = folder_doc["deep_archive_retrieval_ready"]
        retrieval_expires = folder_doc.get("deep_archive_retrieval_expires")
        
        # Ensure all datetime objects are timezone-aware
        if retrieval_start and retrieval_start.tzinfo is None:
            retrieval_start = retrieval_start.replace(tzinfo=timezone.utc)
        if retrieval_ready and retrieval_ready.tzinfo is None:
            retrieval_ready = retrieval_ready.replace(tzinfo=timezone.utc)
        if retrieval_expires and retrieval_expires.tzinfo is None:
            retrieval_expires = retrieval_expires.replace(tzinfo=timezone.utc)
        
        # Add helpful timing info
        folder_doc["retrieval_status"] = {
            "is_retrieving": now < retrieval_ready if retrieval_ready else False,
            "is_ready": (now >= retrieval_ready if retrieval_ready else False) and (retrieval_expires is None or now < retrieval_expires),
            "is_expired": retrieval_expires is not None and now >= retrieval_expires,
            "time_until_ready": max(0, (retrieval_ready - now).total_seconds()) if retrieval_ready and now < retrieval_ready else 0,
            "time_until_expires": max(0, (retrieval_expires - now).total_seconds()) if retrieval_expires and now < retrieval_expires else None
        }
    
    return folder_doc


async def apply_dynamic_folder_status_batch(folder_docs: list) -> list:
    """Apply dynamic status calculation to a list of folder documents"""
    if not folder_docs:
        return folder_docs
    
    # Process each folder
    updated_folders = []
    for folder_doc in folder_docs:
        updated_folder = await apply_dynamic_folder_status(folder_doc)
        updated_folders.append(updated_folder)
    
    return updated_folders 


def determine_file_type(content_type: str, filename: str):
    """Determine file type based on content type and file extension"""
    from app.models.file import FileType
    from app.config import settings
    import os
    file_extension = os.path.splitext(filename)[1].lower().lstrip('.')
    if content_type.startswith('image/') or file_extension in settings.allowed_image_extensions:
        return FileType.IMAGE
    elif content_type.startswith('video/') or file_extension in settings.allowed_video_extensions:
        return FileType.VIDEO
    elif content_type in ['application/pdf', 'application/msword', 'text/plain']:
        return FileType.DOCUMENT
    else:
        return FileType.OTHER

def should_generate_thumbnail(storage_type, file_type):
    """Determine if thumbnail should be generated based on storage type and file type"""
    from app.models.file import FileType
    from app.models.folder import StorageType
    if file_type not in [FileType.IMAGE, FileType.VIDEO]:
        return False
    # Don't generate thumbnails for Deep Archive storage
    if storage_type == StorageType.DEEP_ARCHIVE:
        return False
    return True

def calculate_file_hash(file_content: bytes, filename: str, content_type: str, file_size: int) -> str:
    import hashlib
    if file_size > 20000:
        first_10k = file_content[:10000]
        last_10k = file_content[-10000:]
        hash_input = content_type.encode() + str(file_size).encode() + first_10k + last_10k
    else:
        hash_input = content_type.encode() + str(file_size).encode() + file_content
    return hashlib.sha256(hash_input).hexdigest() 


def convert_objectid(obj):
    """
    Recursively convert ObjectId fields to strings in dicts/lists for JSON serialization.
    """
    if isinstance(obj, list):
        return [convert_objectid(item) for item in obj]
    elif isinstance(obj, dict):
        return {k: convert_objectid(v) for k, v in obj.items()}
    elif isinstance(obj, ObjectId):
        return str(obj)
    else:
        return obj 