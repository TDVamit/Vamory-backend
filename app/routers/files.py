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
from app.utils import format_file_size, calculate_pagination_metadata, calculate_skip_from_page, calculate_file_hash, should_generate_thumbnail, determine_file_type
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
            return {
                "already_uploaded": True,
                "s3_key": existing['s3_key'],
                "storage_class": s3_service.storage_type_to_s3_class(StorageType(existing['storage_type'])),
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
    if existing:
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
            file_hash=file_hash
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
        file_hash=file_hash
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
                await s3_service.upload_file(
                    thumbnail_stream, thumbnail_s3_key, "image/webp", StorageType.STANDARD_IA
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
    if existing:
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
            file_hash=data.file_hash
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
        file_hash=data.file_hash
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

    return {"success": True, "message": "Copying started", "folder_id": folder_id, "status": "copying"} 