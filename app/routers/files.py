from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File as FastAPIFile, Query, BackgroundTasks
from fastapi.responses import JSONResponse
from typing import List, Optional
from bson import ObjectId
from datetime import datetime
import os
import io
import hashlib
from app.models.file import (
    FileCreate, File, FileUpdate, FileInDB, FileUploadResponse, 
    FileType, FileMetadata, StorageType, FileDownloadResponse, 
    FileThumbnailResponse, FileDeleteResponse, PaginatedFilesResponse
)
from app.models.folder import StorageType as FolderStorageType, AccessLevel, StorageType, FolderStatus
from app.models.user import User, UserRole
from app.dependencies import get_current_user, get_current_user_id, folder_read_access, folder_write_access, verify_folder_access
from app.database import get_files_collection, get_folders_collection
from app.services.s3 import s3_service
from app.services.thumbnail import thumbnail_service
from app.config import settings
from app.utils import format_file_size, calculate_pagination_metadata, calculate_skip_from_page, calculate_file_hash, should_generate_thumbnail, determine_file_type, convert_objectid
from pydantic import BaseModel
from botocore.exceptions import ClientError
import asyncio
from app.routers.auth import deny_if_viewer
from app.services.gdrive_utility import is_drive_public
from app.services.gdrive_download import process_gdrive_import

router = APIRouter(prefix="/files", tags=["Files"])


def determine_file_type(content_type: str, filename: str) -> FileType:
    """Determine file type based on content type and file extension"""
    file_extension = os.path.splitext(filename)[1].lower().lstrip('.')
    
    if content_type.startswith('image/') or file_extension in settings.allowed_image_extensions:
        return FileType.IMAGE
    elif content_type.startswith('video/') or file_extension in settings.allowed_video_extensions:
        return FileType.VIDEO
    elif content_type in ['application/pdf', 'application/msword', 'text/plain']:
        return FileType.DOCUMENT
    else:
        return FileType.OTHER


def should_generate_thumbnail(storage_type: StorageType, file_type: FileType) -> bool:
    """Determine if thumbnail should be generated based on storage type and file type"""
    if file_type not in [FileType.IMAGE, FileType.VIDEO]:
        return False
    
    # Don't generate thumbnails for Deep Archive storage
    if storage_type == StorageType.DEEP_ARCHIVE:
        return False
    
    return True


class PresignUploadRequest(BaseModel):
    folder_id: str
    filename: str
    content_type: str
    file_hash: str  # <-- Add this line

@router.post("/presign-upload")
async def presign_upload(
    data: PresignUploadRequest,
    user_id: str = Depends(folder_write_access),
    current_user: User = Depends(get_current_user)
):
    deny_if_viewer(current_user)
    files_collection = await get_files_collection()
    folders_collection = await get_folders_collection()
    folder = await folders_collection.find_one({"_id": ObjectId(data.folder_id)})
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")
    storage_type = StorageType(folder.get("storage_type", StorageType.GLACIER_IR))
    # Check for deduplication by file_hash (client must send file_hash)
    file_hash = getattr(data, 'file_hash', None)
    if file_hash:
        existing = await files_collection.find_one({'file_hash': file_hash})
        if existing:
            existing_storage = StorageType(existing['storage_type'])
            # Deduplication rules
            if (
                (existing_storage in [StorageType.STANDARD_IA, StorageType.GLACIER_IR] and storage_type in [StorageType.STANDARD_IA, StorageType.GLACIER_IR]) or
                (existing_storage == StorageType.DEEP_ARCHIVE and storage_type == StorageType.DEEP_ARCHIVE)
            ):
                return {
                    "already_uploaded": True,
                    "s3_key": existing['s3_key'],
                    "storage_class": s3_service.storage_type_to_s3_class(existing_storage),
                    "thumbnail_s3_key": existing.get('thumbnail_s3_key'),
                    "file_hash": file_hash
                }
    s3_key = s3_service.generate_s3_key(user_id, data.folder_id, data.filename)
    # Generate presigned PUT URL with correct storage class
    presigned_url = await s3_service.generate_presigned_url(
        s3_key, 3600, method='put_object', content_type=data.content_type, storage_type=storage_type
    )
    return {"already_uploaded": False, "url": presigned_url, "s3_key": s3_key, "storage_class": s3_service.storage_type_to_s3_class(storage_type)}


@router.post("/upload", response_model=FileUploadResponse)
async def upload_file(
    folder_id: str,
    file: UploadFile = FastAPIFile(...),
    user_id: str = Depends(folder_write_access),
    current_user: User = Depends(get_current_user)
):
    deny_if_viewer(current_user)
    files_collection = await get_files_collection()
    folders_collection = await get_folders_collection()
    folder = await folders_collection.find_one({"_id": ObjectId(folder_id)})
    if not folder:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Folder not found"
        )
    folder_storage_type = StorageType(folder.get("storage_type", StorageType.GLACIER_IR))
    file_type = determine_file_type(file.content_type, file.filename)
    file_size = file.size
    if not file_size:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File size could not be determined"
        )
    file_extension = os.path.splitext(file.filename)[1].lower().lstrip('.')
    # Always read file content for hashing and deduplication
    await file.seek(0)
    file_content = await file.read()
    await file.seek(0)
    file_hash = calculate_file_hash(file_content, file.filename, file.content_type, file_size)
    # Deduplication check BEFORE upload
    existing = await files_collection.find_one({'file_hash': file_hash})
    deduplicate = False
    if existing:
        existing_storage = StorageType(existing['storage_type'])
        if (
            (existing_storage in [StorageType.STANDARD_IA, StorageType.GLACIER_IR] and folder_storage_type in [StorageType.STANDARD_IA, StorageType.GLACIER_IR]) or
            (existing_storage == StorageType.DEEP_ARCHIVE and folder_storage_type == StorageType.DEEP_ARCHIVE)
        ):
            deduplicate = True
    public_token = folder.get("public_token")
    if deduplicate:
        # Reuse S3 key and thumbnail, create new DB record
        file_in_db = FileInDB(
            filename=file.filename,
            original_filename=file.filename,
            file_type=file_type,
            content_type=file.content_type,
            file_size=file_size,
            folder_id=folder_id,
            owner_id=user_id,
            s3_key=existing['s3_key'],
            s3_url="",
            thumbnail_s3_key=existing.get('thumbnail_s3_key'),
            thumbnail_s3_url="",
            storage_type=folder_storage_type,
            metadata=existing.get('metadata', {}),
            file_hash=file_hash,
            public_token=public_token
        )
        result = await files_collection.insert_one(file_in_db.dict(by_alias=True))
        if result.inserted_id:
            await folders_collection.update_one(
                {"_id": ObjectId(folder_id)},
                {"$inc": {"file_count": 1}}
            )
            file_presigned_url = await s3_service.generate_presigned_url(existing['s3_key'], 3600)
            thumbnail_presigned_url = None
            if existing.get('thumbnail_s3_key'):
                thumbnail_presigned_url = await s3_service.generate_presigned_url(existing['thumbnail_s3_key'], 3600)
            return FileUploadResponse(
                file_id=str(result.inserted_id),
                filename=file.filename,
                file_size=file_size,
                file_type=file_type,
                s3_url=file_presigned_url,
                thumbnail_url=thumbnail_presigned_url,
                storage_type=folder_storage_type
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to save file record")
    # Proceed with upload if not duplicate
        # Initialize thumbnail variables
        thumbnail_s3_key = None
        thumbnail_s3_url = None
        metadata = {}
    # For images and videos, use file_content for thumbnail generation
    if (should_generate_thumbnail(folder_storage_type, file_type)
        and ((file_type == FileType.IMAGE and thumbnail_service.can_generate_thumbnail(file.content_type))
             or (file_type == FileType.VIDEO and thumbnail_service.can_generate_video_thumbnail(file.content_type)))
        and file_size <= 200 * 1024 * 1024):
        pass  # file_content already read above
    else:
        file_content = None
    
    # Stream upload file to S3 with folder's storage type
    s3_key = s3_service.generate_s3_key(user_id, folder_id, file.filename)
    s3_url = await s3_service.upload_streaming_file(file, s3_key, folder_storage_type)
    
    # Generate thumbnail after S3 upload using the pre-read content
    thumbnail_s3_key = None
    if file_content and len(file_content) > 0:
        try:
            if file_type == FileType.IMAGE:
                image_stream = io.BytesIO(file_content)
                metadata = thumbnail_service.get_image_metadata(image_stream)
                image_stream.seek(0)
                thumbnail_stream = thumbnail_service.generate_thumbnail(image_stream)
            elif file_type == FileType.VIDEO:
                video_stream = io.BytesIO(file_content)
                thumbnail_stream = thumbnail_service.generate_video_thumbnail(video_stream)
                metadata = {
                    'format': file_extension,
                    'content_type': file.content_type,
                    'size': file_size
                }
            else:
                thumbnail_stream = None
            
            if thumbnail_stream:
                thumbnail_s3_key = s3_service.generate_s3_key(
                    user_id, folder_id, f"thumb_{file.filename}", "thumbnail"
                )
                await s3_service.upload_file(
                    thumbnail_stream, thumbnail_s3_key, "image/webp", StorageType.STANDARD_IA
                )
        except Exception as e:
            print(f"❌ Thumbnail generation failed for {file.filename}: {str(e)}")
            import traceback
            traceback.print_exc()
    elif file_type == FileType.VIDEO:
        metadata = {
            'format': file_extension,
            'content_type': file.content_type,
            'size': file_size
        }
    
    # Create file record - only store S3 keys, not URLs
    file_in_db = FileInDB(
        filename=file.filename,
        original_filename=file.filename,
        file_type=file_type,
        content_type=file.content_type,
        file_size=file_size,
        folder_id=folder_id,
        owner_id=user_id,
        s3_key=s3_key,
        s3_url="",  # Don't store static URLs
        thumbnail_s3_key=thumbnail_s3_key,
        thumbnail_s3_url="",  # Don't store static URLs
        storage_type=folder_storage_type,
        metadata=metadata,
        file_hash=file_hash,
        public_token=public_token
    )
    
    result = await files_collection.insert_one(file_in_db.dict(by_alias=True))
    if result.inserted_id:
        await folders_collection.update_one(
            {"_id": ObjectId(folder_id)},
            {"$inc": {"file_count": 1}}
        )
        file_presigned_url = await s3_service.generate_presigned_url(s3_key, 3600)
        thumbnail_presigned_url = None
        if thumbnail_s3_key:
            thumbnail_presigned_url = await s3_service.generate_presigned_url(thumbnail_s3_key, 3600)
        return FileUploadResponse(
            file_id=str(result.inserted_id),
            filename=file.filename,
            file_size=file_size,
            file_type=file_type,
            s3_url=file_presigned_url or s3_url,  # Fallback to static URL if presigned fails
            thumbnail_url=thumbnail_presigned_url,
            storage_type=folder_storage_type
        )
    else:
        # Clean up S3 files if database insert failed
        await s3_service.delete_file(s3_key)
        if thumbnail_s3_key:
            await s3_service.delete_file(thumbnail_s3_key)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save file record"
        )
    

async def generate_and_upload_thumbnail(file_id: str, s3_key: str, filename: str, folder_id: str, content_type: str, file_size: int, user_id: str):
    from app.models.file import FileType, StorageType
    from app.services.s3 import s3_service
    from app.services.thumbnail import thumbnail_service
    from app.database import get_files_collection, get_folders_collection
    import io, os
    from bson import ObjectId
    files_collection = await get_files_collection()
    folders_collection = await get_folders_collection()
    file_doc = await files_collection.find_one({"_id": ObjectId(file_id)})
    if not file_doc:
        return
    folder = await folders_collection.find_one({"_id": ObjectId(folder_id)})
    if not folder:
        return
    storage_type = StorageType(folder.get("storage_type", StorageType.GLACIER_IR))
    file_type = determine_file_type(content_type, filename)
    file_extension = os.path.splitext(filename)[1].lower().lstrip('.')
    # Download file from S3
    file_obj = io.BytesIO()
    # Use asyncio to run the synchronous S3 download in a thread pool
    await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: s3_service.s3_client.download_fileobj(s3_service.bucket_name, s3_key, file_obj)
    )
    file_obj.seek(0)
    thumbnail_s3_key = None
    metadata = file_doc.get('metadata', {})
    thumbnail_stream = None
    if (should_generate_thumbnail(storage_type, file_type)
        and ((file_type == FileType.IMAGE and thumbnail_service.can_generate_thumbnail(content_type))
             or (file_type == FileType.VIDEO and thumbnail_service.can_generate_video_thumbnail(content_type)))
        and file_size <= 200 * 1024 * 1024):
        try:
            if file_type == FileType.IMAGE:
                image_stream = io.BytesIO(file_obj.getvalue())
                metadata = thumbnail_service.get_image_metadata(image_stream)
                image_stream.seek(0)
                thumbnail_stream = thumbnail_service.generate_thumbnail(image_stream)
            elif file_type == FileType.VIDEO:
                video_stream = io.BytesIO(file_obj.getvalue())
                thumbnail_stream = thumbnail_service.generate_video_thumbnail(video_stream)
                metadata = {
                    'format': file_extension,
                    'content_type': content_type,
                    'size': file_size
                }
            if thumbnail_stream:
                thumbnail_s3_key = s3_service.generate_s3_key(
                    user_id, folder_id, f"thumb_{filename}", "thumbnail"
                )
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    lambda: s3_service.upload_file(
                        thumbnail_stream, thumbnail_s3_key, "image/webp", StorageType.STANDARD_IA
                    )
                )
                await files_collection.update_one(
                    {"_id": ObjectId(file_id)},
                    {"$set": {
                        "thumbnail_s3_key": thumbnail_s3_key,
                        "metadata": metadata
                    }}
                )
        except Exception as e:
            print(f"Thumbnail generation failed (background): {str(e)}")
    # No deletion of file record if thumbnail fails


class UploadCompleteRequest(BaseModel):
    s3_key: str
    filename: str
    folder_id: str
    content_type: str
    file_size: int
    file_hash: str

@router.post("/upload-complete", response_model=FileUploadResponse)
async def upload_complete(
    data: UploadCompleteRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(folder_write_access),
    current_user: User = Depends(get_current_user)
):
    deny_if_viewer(current_user)
    files_collection = await get_files_collection()
    folders_collection = await get_folders_collection()
    folder = await folders_collection.find_one({"_id": ObjectId(data.folder_id)})
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")
    storage_type = StorageType(folder.get("storage_type", StorageType.GLACIER_IR))
    file_type = determine_file_type(data.content_type, data.filename)
    # Deduplication check
    existing = await files_collection.find_one({'file_hash': data.file_hash})
    deduplicate = False
    if existing:
        existing_storage = StorageType(existing['storage_type'])
        if (
            (existing_storage in [StorageType.STANDARD_IA, StorageType.GLACIER_IR] and storage_type in [StorageType.STANDARD_IA, StorageType.GLACIER_IR]) or
            (existing_storage == StorageType.DEEP_ARCHIVE and storage_type == StorageType.DEEP_ARCHIVE)
        ):
            deduplicate = True
    public_token = folder.get("public_token")
    if deduplicate:
        # Reuse S3 key and thumbnail, create new DB record
        file_in_db = FileInDB(
            filename=data.filename,
            original_filename=data.filename,
            file_type=file_type,
            content_type=data.content_type,
            file_size=data.file_size,
            folder_id=data.folder_id,
            owner_id=user_id,
            s3_key=existing['s3_key'],
            s3_url="",
            thumbnail_s3_key=existing.get('thumbnail_s3_key'),
            thumbnail_s3_url="",
            storage_type=storage_type,
            metadata=existing.get('metadata', {}),
            file_hash=data.file_hash,
            public_token=public_token
        )
        result = await files_collection.insert_one(file_in_db.dict(by_alias=True))
        if result.inserted_id:
            await folders_collection.update_one(
                {"_id": ObjectId(data.folder_id)},
                {"$inc": {"file_count": 1}}
            )
            file_presigned_url = await s3_service.generate_presigned_url(existing['s3_key'], 3600)
            thumbnail_presigned_url = None
            if existing.get('thumbnail_s3_key'):
                thumbnail_presigned_url = await s3_service.generate_presigned_url(existing['thumbnail_s3_key'], 3600)
            return FileUploadResponse(
                file_id=str(result.inserted_id),
                filename=data.filename,
                file_size=data.file_size,
                file_type=file_type,
                s3_url=file_presigned_url,
                thumbnail_url=thumbnail_presigned_url,
                storage_type=storage_type
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to save file record")
    # 1. Create DB record with no thumbnail
    file_in_db = FileInDB(
        filename=data.filename,
        original_filename=data.filename,
        file_type=file_type,
        content_type=data.content_type,
        file_size=data.file_size,
        folder_id=data.folder_id,
        owner_id=user_id,
        s3_key=data.s3_key,
        s3_url="",
        thumbnail_s3_key=None,
        thumbnail_s3_url="",
        storage_type=storage_type,
        metadata={},
        file_hash=data.file_hash,
        public_token=public_token
    )
    result = await files_collection.insert_one(file_in_db.dict(by_alias=True))
    if result.inserted_id:
        await folders_collection.update_one(
            {"_id": ObjectId(data.folder_id)},
            {"$inc": {"file_count": 1}}
        )
        # 2. Kick off background task for thumbnail
        background_tasks.add_task(
            generate_and_upload_thumbnail,
            str(result.inserted_id),
            data.s3_key,
            data.filename,
            data.folder_id,
            data.content_type,
            data.file_size,
            user_id
        )
        file_presigned_url = await s3_service.generate_presigned_url(data.s3_key, 3600)
        return FileUploadResponse(
            file_id=str(result.inserted_id),
            filename=data.filename,
            file_size=data.file_size,
            file_type=file_type,
            s3_url=file_presigned_url,
            thumbnail_url=None,
            storage_type=storage_type
        )
    else:
        raise HTTPException(status_code=500, detail="Failed to save file record")


class AddFromGDriveRequest(BaseModel):
    gdrive_url: str
    folder_name: str
    folder_storage_type: Optional[str] = None

@router.post("/add-from-gdrive")
async def add_from_gdrive(
    data: AddFromGDriveRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user)
):
    # 1. Check if GDrive is public
    if not is_drive_public(data.gdrive_url):
        return JSONResponse(status_code=400, content={"success": False, "message": "Google Drive folder is not public."})

    # 2. Validate storage type
    storage_type = data.folder_storage_type or FolderStorageType.GLACIER_IR.value
    valid_types = {t.value for t in StorageType}
    if storage_type not in valid_types:
        return JSONResponse(status_code=400, content={"success": False, "message": f"Invalid folder_storage_type. Must be one of: {', '.join(valid_types)}"})

    # 3. Create root folder in DB (status copying)
    folders_collection = await get_folders_collection()
    folder_doc = {
        "name": data.folder_name,
        "parent_folder_id": None,
        "storage_type": storage_type,
        "owner_id": current_user.id,
        "status": FolderStatus.COPYING.value,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "is_shared": False,
        "file_count": 0,
        "subfolder_count": 0,
    }
    result = await folders_collection.insert_one(folder_doc)
    folder_id = str(result.inserted_id)

    # 4. Start background task
    background_tasks.add_task(
        process_gdrive_import,
        data.gdrive_url,
        folder_id,
        current_user.id,
        storage_type
    )

    return {"success": True, "message": "Copying started", "folder_id": folder_id, "status": "copying"} \
    
@router.get("/folder/{folder_id}", response_model=PaginatedFilesResponse)
async def get_files_in_folder(
    folder_id: str,
    search: Optional[str] = Query(None, description="Search by filename"),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    per_page: int = Query(20, ge=1, le=100, description="Number of files per page"),
    sort_by: str = Query("filename", description="Field to sort by: filename, file_size, created_at, updated_at, file_type"),
    sort_order: str = Query("asc", description="Sort order: asc or desc"),
    file_type: Optional[FileType] = Query(None, description="Filter by file type: IMAGE, VIDEO, DOCUMENT, OTHER"),
    storage_type: Optional[str] = Query(None, description="Filter by storage type: STANDARD, STANDARD_IA, GLACIER_IR, DEEP_ARCHIVE"),
    min_size: Optional[int] = Query(None, description="Minimum file size in bytes"),
    max_size: Optional[int] = Query(None, description="Maximum file size in bytes"),
    user_id: str = Depends(folder_read_access),
    current_user: User = Depends(get_current_user)
):
    """Get files in a folder with comprehensive search, filtering, pagination, and sorting"""
    files_collection = await get_files_collection()
    # If super_admin, skip owner/access validation
    if current_user.user_role == UserRole.super_admin:
        user_id = None
    
    # Build query
    query = {"folder_id": folder_id}
    
    # Add search filter
    if search:
        search_pattern = {"$regex": search, "$options": "i"}
        query["$or"] = [
            {"filename": search_pattern},
            {"original_filename": search_pattern}
        ]
    
    # Add file type filter
    if file_type:
        query["file_type"] = file_type.value
    
    # Add storage type filter
    if storage_type:
        query["storage_type"] = storage_type
    
    # Add file size filters
    if min_size is not None or max_size is not None:
        size_filter = {}
        if min_size is not None:
            size_filter["$gte"] = min_size
        if max_size is not None:
            size_filter["$lte"] = max_size
        query["file_size"] = size_filter
    
    # Validate and set sort parameters
    allowed_sort_fields = ["filename", "file_size", "created_at", "updated_at", "file_type", "original_filename"]
    if sort_by not in allowed_sort_fields:
        sort_by = "filename"
    
    sort_direction = 1 if sort_order.lower() == "asc" else -1
    
    # Get total count for pagination metadata
    total_count = await files_collection.count_documents(query)
    
    # Calculate pagination metadata
    pagination_meta = calculate_pagination_metadata(total_count, page, per_page)
    
    # Calculate skip for database query
    skip = calculate_skip_from_page(page, per_page)
    
    # Get files with pagination and sorting
    cursor = files_collection.find(query).skip(skip).limit(per_page).sort(sort_by, sort_direction)
    files = await cursor.to_list(None)
    
    # Convert to response format with fresh presigned URLs
    result = []
    tasks = []
    
    for file_doc in files:
        file_doc["_id"] = str(file_doc["_id"])
        
        # Create tasks for parallel presigned URL generation
        if file_doc.get("s3_key"):
            tasks.append(s3_service.generate_presigned_url(file_doc["s3_key"], 3600))
        else:
            tasks.append(None)
            
        if file_doc.get("thumbnail_s3_key"):
            tasks.append(s3_service.generate_presigned_url(file_doc["thumbnail_s3_key"], 3600))
        else:
            tasks.append(None)
        
        result.append(file_doc)
    
    # Execute all S3 calls in parallel
    if tasks:
        presigned_urls = await asyncio.gather(*[task for task in tasks if task is not None])
        
        # Assign results back to files
        url_index = 0
        for file_doc in result:
            if file_doc.get("s3_key"):
                file_doc["s3_url"] = presigned_urls[url_index]
                url_index += 1
            if file_doc.get("thumbnail_s3_key"):
                file_doc["thumbnail_s3_url"] = presigned_urls[url_index]
                url_index += 1
    
    # Convert to File objects
    file_objects = [File(**file_doc) for file_doc in result]
    
    return PaginatedFilesResponse(
        data=file_objects,
        meta=pagination_meta
    )


@router.get("/{file_id}", response_model=File)
async def get_file(
    file_id: str,
    user_id: str = Depends(get_current_user_id),
    current_user: User = Depends(get_current_user)
):
    """Get a specific file"""
    files_collection = await get_files_collection()
    
    file_doc = await files_collection.find_one({"_id": ObjectId(file_id)})
    if not file_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found"
        )
    
    # If not super_admin, check access
    if not (hasattr(current_user, 'user_role') and str(current_user.user_role) == 'super_admin'):
        await verify_folder_access(file_doc["folder_id"], user_id, AccessLevel.READ)
    
    file_doc["_id"] = str(file_doc["_id"])
    
    # Generate fresh presigned URLs (valid for 1 hour)
    if file_doc.get("s3_key"):
        file_doc["s3_url"] = await s3_service.generate_presigned_url(file_doc["s3_key"], 3600)
    
    if file_doc.get("thumbnail_s3_key"):
        file_doc["thumbnail_s3_url"] = await s3_service.generate_presigned_url(file_doc["thumbnail_s3_key"], 3600)
    
    return File(**file_doc)


@router.get("/{file_id}/download", response_model=FileDownloadResponse)
async def download_file(
    file_id: str,
    user_id: str = Depends(get_current_user_id)
):
    """Get a presigned URL to download the file"""
    files_collection = await get_files_collection()
    
    file_doc = await files_collection.find_one({"_id": ObjectId(file_id)})
    if not file_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found"
        )
    
    # Check if file is in Deep Archive (needs restoration)
    if file_doc.get("storage_type") == StorageType.DEEP_ARCHIVE.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File is in Deep Archive storage. Please restore it first (takes 12+ hours)."
        )
    
    # Skip access check if file is public
    print(file_doc)
    if not file_doc.get("public_token"):
        await verify_folder_access(file_doc["folder_id"], user_id, AccessLevel.READ)
    
    # Generate presigned URL (valid for 1 hour) with forced download
    presigned_url = await s3_service.generate_download_presigned_url(file_doc["s3_key"], file_doc["filename"], 3600)
    
    if not presigned_url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate download URL"
        )
    
    return FileDownloadResponse(download_url=presigned_url, filename=file_doc["filename"])


@router.get("/{file_id}/public/download", response_model=FileDownloadResponse)
async def download_file(
    file_id: str,
):
    """Get a presigned URL to download the file"""
    files_collection = await get_files_collection()
    
    file_doc = await files_collection.find_one({"_id": ObjectId(file_id)})
    if not file_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found"
        )
    
    # Check if file is in Deep Archive (needs restoration)
    if file_doc.get("storage_type") == StorageType.DEEP_ARCHIVE.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File is in Deep Archive storage. Please restore it first (takes 12+ hours)."
        )
    
    # Skip access check if file is public
    print(file_doc)
    if not file_doc.get("public_token"):
        await verify_folder_access(file_doc["folder_id"], user_id, AccessLevel.READ)
    
    # Generate presigned URL (valid for 1 hour) with forced download
    presigned_url = await s3_service.generate_download_presigned_url(file_doc["s3_key"], file_doc["filename"], 3600)
    
    if not presigned_url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate download URL"
        )
    
    return FileDownloadResponse(download_url=presigned_url, filename=file_doc["filename"])

@router.get("/{file_id}/thumbnail", response_model=FileThumbnailResponse)
async def get_thumbnail(
    file_id: str,
    user_id: str = Depends(get_current_user_id)
):
    """Get a presigned URL for the file thumbnail"""
    files_collection = await get_files_collection()
    
    file_doc = await files_collection.find_one({"_id": ObjectId(file_id)})
    if not file_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found"
        )
    
    if not file_doc.get("thumbnail_s3_key"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Thumbnail not available for this file"
        )
    
    # Check access to the folder containing this file
    await verify_folder_access(file_doc["folder_id"], user_id, AccessLevel.READ)
    
    # Generate presigned URL for thumbnail (valid for 1 hour)
    presigned_url = await s3_service.generate_presigned_url(file_doc["thumbnail_s3_key"], 3600)
    
    if not presigned_url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate thumbnail URL"
        )
    
    return FileThumbnailResponse(thumbnail_url=presigned_url)


@router.put("/{file_id}", response_model=File)
async def update_file(
    file_id: str,
    file_update: FileUpdate,
    user_id: str = Depends(get_current_user_id)
):
    """Update file information"""
    files_collection = await get_files_collection()
    
    file_doc = await files_collection.find_one({"_id": ObjectId(file_id)})
    if not file_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found"
        )
    
    # Check access to the current folder
    await verify_folder_access(file_doc["folder_id"], user_id, AccessLevel.WRITE)
    
    # Build update data
    update_data = {}
    if file_update.filename is not None:
        update_data["filename"] = file_update.filename
    
    # If moving to a different folder, check access to new folder
    if file_update.folder_id and file_update.folder_id != file_doc["folder_id"]:
        await verify_folder_access(file_update.folder_id, user_id, AccessLevel.WRITE)
        
        # Get target folder to inherit storage type
        folders_collection = await get_folders_collection()
        target_folder = await folders_collection.find_one({"_id": ObjectId(file_update.folder_id)})
        if not target_folder:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Target folder not found"
            )
        
        target_storage_type = StorageType(target_folder.get("storage_type", StorageType.GLACIER_IR))
        current_storage_type = StorageType(file_doc.get("storage_type", StorageType.GLACIER_IR))
        
        # Update storage type if different
        if target_storage_type != current_storage_type:
            # Change S3 storage class
            await s3_service.change_storage_class(file_doc["s3_key"], target_storage_type)
            update_data["storage_type"] = target_storage_type.value
            
            # Handle thumbnails based on storage type change
            if (target_storage_type == StorageType.DEEP_ARCHIVE and 
                current_storage_type != StorageType.DEEP_ARCHIVE and 
                file_doc.get("thumbnail_s3_key")):
                # Keep thumbnail but remove references from database for consistency
                # (Thumbnail will remain in S3 but won't be accessible via API)
                update_data["thumbnail_s3_key"] = None
                update_data["thumbnail_s3_url"] = None
        
        update_data["folder_id"] = file_update.folder_id
        
        # Update folder file counts
        await folders_collection.update_one(
            {"_id": ObjectId(file_doc["folder_id"])},
            {"$inc": {"file_count": -1}}
        )
        await folders_collection.update_one(
            {"_id": ObjectId(file_update.folder_id)},
            {"$inc": {"file_count": 1}}
        )
    
    update_data["updated_at"] = datetime.utcnow()
    
    result = await files_collection.update_one(
        {"_id": ObjectId(file_id)},
        {"$set": update_data}
    )
    
    if result.modified_count:
        updated_file = await files_collection.find_one({"_id": ObjectId(file_id)})
        updated_file["_id"] = str(updated_file["_id"])
        return File(**updated_file)
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update file"
        )


@router.delete("/{file_id}", response_model=FileDeleteResponse)
async def delete_file(
    file_id: str,
    user_id: str = Depends(get_current_user_id)
):
    """Delete a file"""
    files_collection = await get_files_collection()
    folders_collection = await get_folders_collection()
    
    file_doc = await files_collection.find_one({"_id": ObjectId(file_id)})
    if not file_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found"
        )
    
    # Check access to the folder containing this file
    await verify_folder_access(file_doc["folder_id"], user_id, AccessLevel.WRITE)
    
    # Check if this file has duplicates (same file_hash)
    file_hash = file_doc.get("file_hash")
    should_delete_s3 = True
    should_delete_thumbnail_s3 = True
    
    if file_hash:
        # Count how many files have the same hash
        duplicate_count = await files_collection.count_documents({"file_hash": file_hash})
        
        if duplicate_count > 1:
            # There are other files with the same hash, don't delete from S3
            should_delete_s3 = False
            should_delete_thumbnail_s3 = False
            print(f"📁 File {file_id} has {duplicate_count} duplicates, keeping S3 objects")
        else:
            # This is the only file with this hash, safe to delete from S3
            print(f"🗑️  File {file_id} is unique, deleting from S3")
    
    # Delete file from S3 only if it's the only copy
    if should_delete_s3 and file_doc.get("s3_key"):
        await s3_service.delete_file(file_doc["s3_key"])
    
    if should_delete_thumbnail_s3 and file_doc.get("thumbnail_s3_key"):
        await s3_service.delete_file(file_doc["thumbnail_s3_key"])
    
    # Delete file record from database
    await files_collection.delete_one({"_id": ObjectId(file_id)})
    
    # Update folder's file count
    await folders_collection.update_one(
        {"_id": ObjectId(file_doc["folder_id"])},
        {"$inc": {"file_count": -1}}
    )
    
    return FileDeleteResponse(message="File deleted successfully") 

public_router = APIRouter(prefix="/public/files", tags=["Public Files"])

@public_router.get("/", response_model=PaginatedFilesResponse)
async def get_files_in_public_folder_by_id(
    token: str,
    folder_id: str,
    search: Optional[str] = Query(None, description="Search by filename"),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    per_page: int = Query(20, ge=1, le=100, description="Number of files per page"),
    sort_by: str = Query("filename", description="Field to sort by: filename, file_size, created_at, updated_at, file_type"),
    sort_order: str = Query("asc", description="Sort order: asc or desc"),
    file_type: Optional[FileType] = Query(None, description="Filter by file type: IMAGE, VIDEO, DOCUMENT, OTHER"),
    storage_type: Optional[str] = Query(None, description="Filter by storage type: STANDARD, STANDARD_IA, GLACIER_IR, DEEP_ARCHIVE"),
    min_size: Optional[int] = Query(None, description="Minimum file size in bytes"),
    max_size: Optional[int] = Query(None, description="Maximum file size in bytes")
):
    folders_collection = await get_folders_collection()
    folder = await folders_collection.find_one({"_id": ObjectId(folder_id), "is_public": True, "public_token": token})
    if not folder:
        raise HTTPException(status_code=404, detail="Public folder not found or not public")
    files_collection = await get_files_collection()
    query = {"folder_id": folder_id}
    if search:
        search_pattern = {"$regex": search, "$options": "i"}
        query["$or"] = [
            {"filename": search_pattern},
            {"original_filename": search_pattern}
        ]
    if file_type:
        query["file_type"] = file_type.value
    if storage_type:
        query["storage_type"] = storage_type
    if min_size is not None or max_size is not None:
        size_filter = {}
        if min_size is not None:
            size_filter["$gte"] = min_size
        if max_size is not None:
            size_filter["$lte"] = max_size
        query["file_size"] = size_filter
    allowed_sort_fields = ["filename", "file_size", "created_at", "updated_at", "file_type", "original_filename"]
    if sort_by not in allowed_sort_fields:
        sort_by = "filename"
    sort_direction = 1 if sort_order.lower() == "asc" else -1
    total_count = await files_collection.count_documents(query)
    pagination_meta = calculate_pagination_metadata(total_count, page, per_page)
    skip = calculate_skip_from_page(page, per_page)
    cursor = files_collection.find(query).skip(skip).limit(per_page).sort(sort_by, sort_direction)
    files = await cursor.to_list(None)
    result = []
    tasks = []
    for file_doc in files:
        file_doc["_id"] = str(file_doc["_id"])
        if file_doc.get("s3_key"):
            tasks.append(s3_service.generate_presigned_url(file_doc["s3_key"], 3600))
        else:
            tasks.append(None)
        if file_doc.get("thumbnail_s3_key"):
            tasks.append(s3_service.generate_presigned_url(file_doc["thumbnail_s3_key"], 3600))
        else:
            tasks.append(None)
        result.append(file_doc)
    if tasks:
        presigned_urls = await asyncio.gather(*[task for task in tasks if task is not None])
        url_index = 0
        for file_doc in result:
            if file_doc.get("s3_key"):
                file_doc["s3_url"] = presigned_urls[url_index]
                url_index += 1
            if file_doc.get("thumbnail_s3_key"):
                file_doc["thumbnail_s3_url"] = presigned_urls[url_index]
                url_index += 1
    file_objects = [File(**file_doc) for file_doc in result]
    return PaginatedFilesResponse(
        data=file_objects,
        meta=pagination_meta
    )

@public_router.get("/file", response_model=File)
async def get_public_file_by_id(token: str, file_id: str):
    folders_collection = await get_folders_collection()
    files_collection = await get_files_collection()
    file_doc = await files_collection.find_one({"_id": ObjectId(file_id)})
    if not file_doc:
        raise HTTPException(status_code=404, detail="File not found")
    folder = await folders_collection.find_one({"_id": ObjectId(file_doc["folder_id"]), "is_public": True, "public_token": token})
    if not folder:
        raise HTTPException(status_code=403, detail="File is not in a public folder or token invalid")
    file_doc["_id"] = str(file_doc["_id"])
    if file_doc.get("s3_key"):
        file_doc["s3_url"] = await s3_service.generate_presigned_url(file_doc["s3_key"], 3600)
    if file_doc.get("thumbnail_s3_key"):
        file_doc["thumbnail_s3_url"] = await s3_service.generate_presigned_url(file_doc["thumbnail_s3_key"], 3600)
    return File(**file_doc)

# Register the public router
router.include_router(public_router) 