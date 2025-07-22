import os
import re
import io
import asyncio
import tempfile
import functools
import shutil
import aiohttp
import aiofiles
from datetime import datetime
from bson import ObjectId
from app.models.file import FileType, FileInDB
from app.models.folder import StorageType, FolderStatus
from app.services.s3 import s3_service
from app.services.thumbnail import thumbnail_service
from app.database import get_files_collection, get_folders_collection
from app.routers.files import calculate_file_hash, should_generate_thumbnail
from app.config import settings

# Google Drive API settings
API_KEY = settings.drive_api_key
BASE_URL = "https://www.googleapis.com/drive/v3/files"
MEDIA_PREFIXES = ("image/", "video/")
MAX_INFLIGHT_BYTES = 20 * 1024 ** 3  # 20 GB limit for inflight downloads
MAX_FILE_SIZE = MAX_INFLIGHT_BYTES  # skip files larger than this

class ByteSemaphore:
    def __init__(self, limit_bytes: int):
        self._limit = limit_bytes
        self._current = 0
        self._waiters = []
        self._lock = asyncio.Lock()

    async def acquire(self, nbytes: int):
        async with self._lock:
            while self._current + nbytes > self._limit:
                fut = asyncio.get_event_loop().create_future()
                self._waiters.append(fut)
                await fut
            self._current += nbytes

    async def release(self, nbytes: int):
        async with self._lock:
            self._current -= nbytes
            for fut in list(self._waiters):
                if not fut.done() and self._current < self._limit:
                    fut.set_result(True)
                    self._waiters.remove(fut)
                    break

async def process_gdrive_import(gdrive_url: str, root_folder_id: str, user_id: str, storage_type: str):
    """
    Download public GDrive folder (images/videos only), upload to S3, and clean up local files.
    Enforces a 20 GB inflight download cap and skips single files above 20 GB.
    """
    # Setup DB and loop
    folders_collection = await get_folders_collection()
    files_collection = await get_files_collection()
    loop = asyncio.get_event_loop()
    byte_sem = ByteSemaphore(MAX_INFLIGHT_BYTES)

    # Helpers
    def extract_folder_id(url: str) -> str:
        for pat in (r"/folders/([A-Za-z0-9_-]+)", r"[?&]id=([A-Za-z0-9_-]+)"):
            m = re.search(pat, url)
            if m:
                return m.group(1)
        raise ValueError(f"Invalid Drive folder URL: {url}")

    async def list_folder(session: aiohttp.ClientSession, folder_id: str, page_token: str = None) -> dict:
        params = {
            "key": API_KEY,
            "q": f"'{folder_id}' in parents and trashed=false",
            "fields": "nextPageToken, files(id,name,mimeType,size)",
            "pageSize": 1000,
        }
        if page_token:
            params["pageToken"] = page_token
        async with session.get(BASE_URL, params=params) as r:
            r.raise_for_status()
            return await r.json()

    async def download_and_upload_file(session: aiohttp.ClientSession, item: dict, local_dir: str, parent_db_id: str):
        fid = item['id']
        name = item['name']
        size = int(item.get('size', 0) or 0)
        mime = item['mimeType']

        # Skip oversized files
        if size > MAX_FILE_SIZE:
            print(f"[SKIP] {name} ({size} bytes) > {MAX_FILE_SIZE} bytes limit")
            return

        await byte_sem.acquire(size)
        try:
            # Prepare local path
            os.makedirs(local_dir, exist_ok=True)
            temp_path = os.path.join(local_dir, name)

            # Download file stream
            url = f"{BASE_URL}/{fid}"
            params = {"alt": "media", "key": API_KEY}
            async with session.get(url, params=params) as resp:
                resp.raise_for_status()
                async with aiofiles.open(temp_path, 'wb') as f:
                    async for chunk in resp.content.iter_chunked(32768):
                        await f.write(chunk)

            # Read file for hashing & metadata
            async with aiofiles.open(temp_path, 'rb') as f:
                content = await f.read()
            content_type = mime
            file_hash = calculate_file_hash(content, name, content_type, size)

            # Determine file type
            file_type = FileType.IMAGE if mime.startswith('image/') else FileType.VIDEO

            # Dedup: reuse existing S3 key if present
            existing = await files_collection.find_one({'file_hash': file_hash})
            deduplicate = False
            if existing:
                existing_storage = StorageType(existing['storage_type'])
                if (
                    (existing_storage in [StorageType.STANDARD_IA, StorageType.GLACIER_IR] and StorageType(storage_type) in [StorageType.STANDARD_IA, StorageType.GLACIER_IR]) or
                    (existing_storage == StorageType.DEEP_ARCHIVE and StorageType(storage_type) == StorageType.DEEP_ARCHIVE)
                ):
                    deduplicate = True
            if deduplicate:
                s3_key = existing['s3_key']
                thumbnail_key = existing.get('thumbnail_s3_key')
                metadata = existing.get('metadata', {})
            else:
                # Upload to S3
                s3_key = s3_service.generate_s3_key(user_id, parent_db_id, name)
                if size > 100 * 1024**2:
                    # Offload blocking S3 upload to thread pool
                    await loop.run_in_executor(None, functools.partial(
                        s3_service.upload_large_file, open(temp_path, 'rb'), s3_key, content_type, size, StorageType(storage_type)
                    ))
                else:
                    await loop.run_in_executor(None, functools.partial(
                        s3_service.upload_file, open(temp_path, 'rb'), s3_key, content_type, StorageType(storage_type)
                    ))

                # Thumbnail generation
                thumbnail_key = None
                metadata = {}
                if should_generate_thumbnail(StorageType(storage_type), file_type):
                    try:
                        if file_type == FileType.IMAGE and thumbnail_service.can_generate_thumbnail(content_type):
                            image_stream = io.BytesIO(content)
                            metadata = await loop.run_in_executor(None, functools.partial(thumbnail_service.get_image_metadata, image_stream))
                            image_stream.seek(0)
                            thumbnail_stream = await loop.run_in_executor(None, functools.partial(thumbnail_service.generate_thumbnail, image_stream))
                        elif file_type == FileType.VIDEO and thumbnail_service.can_generate_video_thumbnail(content_type):
                            video_stream = io.BytesIO(content)
                            thumbnail_stream = await loop.run_in_executor(None, functools.partial(thumbnail_service.generate_video_thumbnail, video_stream))
                            metadata = {
                                'format': os.path.splitext(name)[1].lower().lstrip('.'),
                                'content_type': content_type,
                                'size': size
                            }
                        else:
                            thumbnail_stream = None

                        if thumbnail_stream:
                            thumbnail_key = s3_service.generate_s3_key(user_id, parent_db_id, f"thumb_{name}", "thumbnail")
                            if isinstance(thumbnail_stream, io.BytesIO):
                                thumbnail_stream.seek(0)
                                await loop.run_in_executor(None, functools.partial(
                                    s3_service.upload_file, thumbnail_stream, thumbnail_key, "image/webp", StorageType(storage_type)
                                ))
                            else:
                                await loop.run_in_executor(None, functools.partial(
                                    s3_service.upload_file, io.BytesIO(thumbnail_stream), thumbnail_key, "image/webp", StorageType(storage_type)
                                ))
                    except Exception as e:
                        print(f"[THUMBNAIL ERROR] {name}: {e}")

            # Insert DB record
            file_doc = FileInDB(
                filename=name,
                original_filename=name,
                file_type=file_type,
                content_type=content_type,
                file_size=size,
                folder_id=parent_db_id,
                owner_id=user_id,
                s3_key=s3_key,
                s3_url="",
                thumbnail_s3_key=thumbnail_key,
                thumbnail_s3_url="",
                storage_type=StorageType(storage_type),
                metadata=metadata,
                file_hash=file_hash
            )
            await files_collection.insert_one(file_doc.dict(by_alias=True))
            await folders_collection.update_one({'_id': ObjectId(parent_db_id)}, {'$inc': {'file_count': 1}})

        finally:
            # Clean up local file and release semaphore
            if os.path.exists(temp_path):
                await loop.run_in_executor(None, functools.partial(os.remove, temp_path))
            await byte_sem.release(size)

    async def traverse(session: aiohttp.ClientSession, drive_folder_id: str, parent_db_id: str, local_path: str):
        """
        Recursively list, create DB folders, and download/upload media.
        """
        # Create folder doc if not root
        if local_path:
            folder_doc = {
                "name": os.path.basename(local_path),
                "parent_folder_id": parent_db_id,
                "storage_type": storage_type,
                "owner_id": user_id,
                "status": FolderStatus.ACTIVE.value,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
                "is_shared": False,
                "file_count": 0,
                "subfolder_count": 0,
            }
            res = await folders_collection.insert_one(folder_doc)
            db_id = str(res.inserted_id)
            await folders_collection.update_one({'_id': ObjectId(parent_db_id)}, {'$inc': {'subfolder_count': 1}})
        else:
            db_id = parent_db_id

        tasks = []
        page_token = None
        # List and process items
        while True:
            data = await list_folder(session, drive_folder_id, page_token)
            for item in data.get('files', []):
                mime = item['mimeType']
                name = item['name']
                if mime == 'application/vnd.google-apps.folder':
                    sub_local = os.path.join(temp_root, drive_folder_id, name)
                    tasks.append(traverse(session, item['id'], db_id, sub_local))
                elif any(mime.startswith(p) for p in MEDIA_PREFIXES):
                    tasks.append(download_and_upload_file(session, item, os.path.join(temp_root, drive_folder_id), db_id))
            page_token = data.get('nextPageToken')
            if not page_token:
                break

        await asyncio.gather(*tasks)

    # Main flow
    root_id = extract_folder_id(gdrive_url)
    temp_root = tempfile.mkdtemp(prefix="gdrive_")
    try:
        async with aiohttp.ClientSession() as session:
            await traverse(session, root_id, root_folder_id, "")
        await folders_collection.update_one({'_id': ObjectId(root_folder_id)}, {'$set': {'status': FolderStatus.ACTIVE.value}})
    except Exception as e:
        await folders_collection.update_one({'_id': ObjectId(root_folder_id)}, {'$set': {'status': FolderStatus.ERROR.value, 'error': str(e)}})
    finally:
        await loop.run_in_executor(None, functools.partial(shutil.rmtree, temp_root, ignore_errors=True))
