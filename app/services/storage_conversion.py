import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from bson import ObjectId
from app.models.folder import StorageType, FolderStatus
from app.models.file import ArchivalStatus
from app.database import get_folders_collection, get_files_collection
from app.services.s3 import s3_service
from app.services.storage_tracking import storage_tracking_service
from app.services.billing_utils import apply_minimum_file_size


class StorageConversionService:
    """Service to handle storage type conversions with time-based status tracking"""
    
    def generate_job_id(self) -> str:
        """Generate a unique job ID for tracking conversions"""
        return f"conv_{uuid.uuid4().hex[:12]}"
    
    def calculate_retrieval_times(
        self, 
        retrieval_days: Optional[int]
    ) -> Tuple[datetime, datetime, Optional[datetime]]:
        """
        Calculate retrieval timing for Deep Archive conversions
        
        Returns:
            (retrieval_start, retrieval_ready, retrieval_expires)
        """
        now = datetime.now(timezone.utc)
        retrieval_start = now
        
        # Calculate when files will be ready (using bulk mode timing for safety)
        retrieval_ready = now + timedelta(hours=48)  # 5-48 hours, use 48 for safety
        
        # Calculate expiration time
        if retrieval_days is not None:
            retrieval_expires = retrieval_ready + timedelta(days=retrieval_days)
        else:
            retrieval_expires = None  # Permanent retrieval
        
        return retrieval_start, retrieval_ready, retrieval_expires
    
    async def calculate_folder_status(self, folder_doc: dict) -> Tuple[FolderStatus, StorageType]:
        """
        Calculate the current status and effective storage type based on timing
        
        Args:
            folder_doc: MongoDB folder document
            
        Returns:
            (current_status, effective_storage_type)
        """
        now = datetime.now(timezone.utc)
        
        # Check if this is a Deep Archive retrieval in progress using retrieval_expiry_date
        retrieval_expiry_date = folder_doc.get("retrieval_expiry_date")
        
        if retrieval_expiry_date:
            # Ensure datetime object is timezone-aware
            if retrieval_expiry_date.tzinfo is None:
                retrieval_expiry_date = retrieval_expiry_date.replace(tzinfo=timezone.utc)
            
            # If retrieval has expired, clean up and return to Deep Archive
            if now >= retrieval_expiry_date:
                await self._cleanup_expired_retrieval(folder_doc["_id"])
                return FolderStatus.INACTIVE, StorageType.DEEP_ARCHIVE
            else:
                # Files are ready and available
                return FolderStatus.ACTIVE, StorageType.GLACIER_IR
        
        # No Deep Archive retrieval in progress - use actual storage type
        storage_type = StorageType(folder_doc.get("storage_type", StorageType.GLACIER_IR))
        
        if storage_type == StorageType.DEEP_ARCHIVE:
            return FolderStatus.INACTIVE, storage_type
        else:
            return FolderStatus.ACTIVE, storage_type
    
    async def _cleanup_expired_retrieval(self, folder_id: ObjectId):
        """Clean up expired Deep Archive retrieval and return to Deep Archive"""
        folders_collection = await get_folders_collection()
        files_collection = await get_files_collection()
        
        print(f"🔄 Cleaning up expired Deep Archive retrieval for folder {folder_id}")
        
        # Get all child folders for cleanup
        folder_ids = await self._get_all_child_folders(str(folder_id))
        folder_object_ids = [ObjectId(fid) for fid in folder_ids]
        
        # Clean up folder documents
        await folders_collection.update_many(
            {"_id": {"$in": folder_object_ids}},
            {
                "$set": {
                    "storage_type": StorageType.DEEP_ARCHIVE.value,
                    "status": FolderStatus.INACTIVE.value,
                    "updated_at": datetime.now(timezone.utc)
                },
                "$unset": {
                    "retrieval_days": "",
                    "retrieval_expiry_date": ""
                }
            }
        )
        
        # Update files back to Deep Archive (excluding deleted files)
        await files_collection.update_many(
            {"folder_id": {"$in": folder_ids}, "deleted": False},
            {
                "$set": {
                    "storage_type": StorageType.DEEP_ARCHIVE.value,
                    "updated_at": datetime.now(timezone.utc)
                },
                "$unset": {
                    "thumbnail_s3_key": "",
                    "thumbnail_s3_url": ""
                }
            }
        )
        
        print(f"✅ Expired retrieval cleaned up - folder returned to Deep Archive")
    
    async def start_deep_archive_retrieval(
        self,
        folder_id: str,
        target_storage: StorageType,
        apply_to_children: bool = False,
        retrieval_days: Optional[int] = None
    ) -> Tuple[str, datetime, datetime, Optional[datetime]]:
        """
        Start a Deep Archive retrieval with time-based tracking
        
        Returns:
            (job_id, retrieval_start, retrieval_ready, retrieval_expires)
        """
        folders_collection = await get_folders_collection()
        files_collection = await get_files_collection()
        
        # Generate job ID
        job_id = self.generate_job_id()
        
        # Calculate timing
        retrieval_start, retrieval_ready, retrieval_expires = self.calculate_retrieval_times(
            retrieval_days
        )
        
        # Get all folders to be converted
        if apply_to_children:
            folder_ids = await self._get_all_child_folders(folder_id)
        else:
            folder_ids = [folder_id]
        
        # Get all files that need S3 restoration (excluding deleted files)
        files_cursor = files_collection.find({"folder_id": {"$in": folder_ids}, "deleted": False})
        files_to_restore = await files_cursor.to_list(None)
        
        # Start S3 Deep Archive restoration for all files
        print(f"🔄 Starting S3 Deep Archive restoration for {len(files_to_restore)} files...")
        restoration_success = 0
        restoration_errors = []
        
        for file_doc in files_to_restore:
            if file_doc.get("s3_key"):
                try:
                    # Determine restoration tier based on mode
                    restore_days = retrieval_days if retrieval_days else 1  # Minimum 1 day for S3 restore
                    success = await s3_service.restore_from_deep_archive(file_doc["s3_key"], restore_days)
                    if success:
                        restoration_success += 1
                    else:
                        restoration_errors.append(f"Failed to restore {file_doc['s3_key']}")
                except Exception as e:
                    restoration_errors.append(f"Error restoring {file_doc['s3_key']}: {str(e)}")
        
        print(f"✅ Started restoration for {restoration_success} files")
        if restoration_errors:
            print(f"⚠️  {len(restoration_errors)} restoration errors")
            for error in restoration_errors[:5]:  # Show first 5 errors
                print(f"   - {error}")
        
        # Update folder(s) with Deep Archive retrieval tracking
        update_data = {
            "status": FolderStatus.CONVERTING.value,
            "retrieval_days": retrieval_days,
            "retrieval_expiry_date": retrieval_expires,
            "updated_at": datetime.now(timezone.utc)
        }
        
        # Update all affected folders
        folder_object_ids = [ObjectId(fid) for fid in folder_ids]
        await folders_collection.update_many(
            {"_id": {"$in": folder_object_ids}},
            {"$set": update_data}
        )
        
        print(f"🚀 Started Deep Archive retrieval job {job_id}")
        print(f"   Target storage: {target_storage.value}")
        print(f"   Ready at: {retrieval_ready}")
        if retrieval_expires:
            print(f"   Expires at: {retrieval_expires}")
        else:
            print(f"   Permanent retrieval")
        
        return job_id, retrieval_start, retrieval_ready, retrieval_expires
    
    async def start_immediate_conversion(
        self,
        folder_id: str,
        storage_from: StorageType,
        storage_to: StorageType,
        apply_to_children: bool = False
    ) -> Tuple[str, int, int]:
        """
        Start an immediate storage conversion (non-Deep Archive)
        
        Returns:
            (job_id, folders_updated, files_updated)
        """
        folders_collection = await get_folders_collection()
        files_collection = await get_files_collection()
        
        # Generate job ID
        job_id = self.generate_job_id()
        
        # Get all folders to be converted
        if apply_to_children:
            folder_ids = await self._get_all_child_folders(folder_id)
        else:
            folder_ids = [folder_id]
        
        folder_object_ids = [ObjectId(fid) for fid in folder_ids]
        
        # Get all files that need S3 storage class conversion (excluding deleted files)
        files_cursor = files_collection.find({"folder_id": {"$in": folder_ids}, "deleted": False})
        files_to_convert = await files_cursor.to_list(None)
        
        # Perform S3 storage class conversion for all files
        print(f"🔄 Converting S3 storage class for {len(files_to_convert)} files: {storage_from.value} → {storage_to.value}")
        s3_conversion_success = 0
        s3_conversion_errors = []
        
        for file_doc in files_to_convert:
            if file_doc.get("s3_key"):
                # Only apply special logic if converting TO Deep Archive
                if storage_to == StorageType.DEEP_ARCHIVE:
                    # Check for other references to this file_hash in non-Deep Archive folders
                    file_hash = file_doc.get("file_hash")
                    if file_hash:
                        # Find other file records with same hash, different folder, not in this conversion set
                        other_refs = await files_collection.find({
                            "file_hash": file_hash,
                            "folder_id": {"$nin": folder_ids},
                        }).to_list(None)
                        # Now filter out those whose folder is Deep Archive
                        non_deep_refs = []
                        for ref in other_refs:
                            ref_folder = await folders_collection.find_one({"_id": ObjectId(ref["folder_id"])})
                            if ref_folder and ref_folder.get("storage_type") != StorageType.DEEP_ARCHIVE.value:
                                non_deep_refs.append(ref)
                        if non_deep_refs:
                            # There is at least one other non-Deep Archive reference, so make a copy
                            print(f"[DeepArchive] File {file_doc['_id']} is referenced in other non-Deep Archive folders. Making a copy.")
                            
                            # Generate new S3 key for the main file
                            new_s3_key = s3_service.generate_s3_key(
                                file_doc["owner_id"], file_doc["folder_id"], file_doc["filename"]
                            )
                            
                            # Copy the main file in S3
                            copy_success = await s3_service.copy_file(file_doc["s3_key"], new_s3_key)
                            
                            if copy_success:
                                # Change storage class of the new copy to Deep Archive
                                await s3_service.change_storage_class(new_s3_key, StorageType.DEEP_ARCHIVE)
                                
                                # Prepare DB update
                                update_payload = {
                                    "s3_key": new_s3_key,
                                    "storage_type": StorageType.DEEP_ARCHIVE.value,
                                    "updated_at": datetime.now(timezone.utc)
                                }
                                
                                # Check if a thumbnail exists and copy it as well
                                old_thumbnail_key = file_doc.get("thumbnail_s3_key")
                                if old_thumbnail_key:
                                    print(f"[DeepArchive] Copying thumbnail for file {file_doc['_id']}")
                                    # Generate a new key for the thumbnail copy
                                    new_thumbnail_key = f"thumbnails/{new_s3_key}"
                                    
                                    # Copy the thumbnail
                                    thumb_copy_success = await s3_service.copy_file(old_thumbnail_key, new_thumbnail_key)
                                    
                                    if thumb_copy_success:
                                        # Change storage class of the new thumbnail to Deep Archive
                                        await s3_service.change_storage_class(new_thumbnail_key, StorageType.DEEP_ARCHIVE)
                                        # Add to DB update payload
                                        update_payload["thumbnail_s3_key"] = new_thumbnail_key
                                    else:
                                        print(f"⚠️  [DeepArchive] Failed to copy thumbnail {old_thumbnail_key}")
                                        # Keep original thumbnail key even if copy fails
                                        # The original thumbnail will still be accessible
                                # Keep existing thumbnail keys if no original thumbnail
                                # No need to clear thumbnail references

                                # Update DB record for this file to point to new S3 key(s) and Deep Archive
                                await files_collection.update_one(
                                    {"_id": file_doc["_id"]},
                                    {"$set": update_payload}
                                )
                                s3_conversion_success += 1
                            else:
                                s3_conversion_errors.append(f"Failed to copy and archive {file_doc['s3_key']}")
                            continue  # Skip the rest of the loop for this file
                            
                # Default: just change storage class as before
                try:
                    success = await s3_service.change_storage_class(file_doc["s3_key"], storage_to)
                    if success:
                        s3_conversion_success += 1
                    else:
                        s3_conversion_errors.append(f"Failed to convert {file_doc['s3_key']}")
                except Exception as e:
                    s3_conversion_errors.append(f"Error converting {file_doc['s3_key']}: {str(e)}")
            
            # Handle thumbnail S3 conversion if exists and not going to Deep Archive
            if file_doc.get("thumbnail_s3_key") and storage_to != StorageType.DEEP_ARCHIVE:
                try:
                    await s3_service.change_storage_class(file_doc["thumbnail_s3_key"], storage_to)
                except Exception as e:
                    s3_conversion_errors.append(f"Error converting thumbnail {file_doc['thumbnail_s3_key']}: {str(e)}")
        
        print(f"✅ S3 conversion completed for {s3_conversion_success} files")
        if s3_conversion_errors:
            print(f"⚠️  {len(s3_conversion_errors)} S3 conversion errors")
            for error in s3_conversion_errors[:5]:  # Show first 5 errors
                print(f"   - {error}")
        
        # Determine new status
        new_status = FolderStatus.INACTIVE if storage_to == StorageType.DEEP_ARCHIVE else FolderStatus.ACTIVE
        
        # Update folders
        folder_result = await folders_collection.update_many(
            {"_id": {"$in": folder_object_ids}},
            {"$set": {
                "storage_type": storage_to.value,
                "status": new_status.value,
                "updated_at": datetime.now(timezone.utc)
            }}
        )
        
        # Track storage usage changes for all affected files
        files_to_track = await files_collection.find({"folder_id": {"$in": folder_ids}}).to_list(None)
        for file_doc in files_to_track:
            # Move storage from old type to new type (with minimum 128KB)
            billing_size = apply_minimum_file_size(file_doc.get("file_size", 0))
            await storage_tracking_service.move_storage_usage(
                user_id=file_doc["owner_id"],
                size_bytes=billing_size,
                from_storage=storage_from,
                to_storage=storage_to
            )
        
        # Update files
        file_result = await files_collection.update_many(
            {"folder_id": {"$in": folder_ids}},
            {"$set": {
                "storage_type": storage_to.value,
                "updated_at": datetime.now(timezone.utc)
            }}
        )
        
        # Update archival status for Deep Archive
        if storage_to == StorageType.DEEP_ARCHIVE:
            # Update archival_status to deep_archive but keep thumbnail references
            await files_collection.update_many(
                {"folder_id": {"$in": folder_ids}},
                {"$set": {
                    "archival_status": ArchivalStatus.DEEP_ARCHIVE.value,
                    "archived_at": datetime.now(timezone.utc)
                }}
            )
        
        print(f"✅ Immediate conversion completed: {storage_from.value} → {storage_to.value}")
        
        return job_id, folder_result.modified_count, file_result.modified_count
    
    async def check_and_update_folder_conversion_status(self, folder_id: str, target_storage: StorageType, apply_to_children: bool = True) -> dict:
        """
        Check if all files in the folder (and optionally subfolders) are converted to the target storage type.
        If so, update the folder's status to ACTIVE and update related fields.
        Returns a dict with the result and what was changed.
        """
        folders_collection = await get_folders_collection()
        files_collection = await get_files_collection()

        # Get all relevant folder IDs
        if apply_to_children:
            folder_ids = await self._get_all_child_folders(folder_id)
        else:
            folder_ids = [folder_id]

        # Check all files' storage_type (excluding deleted files)
        files_cursor = files_collection.find({"folder_id": {"$in": folder_ids}, "deleted": False})
        files = await files_cursor.to_list(None)
        not_converted = [f for f in files if f.get("storage_type") != target_storage.value]

        if not not_converted:
            # All files are converted, update folders
            now = datetime.now(timezone.utc)
            # Check if this is a Deep Archive retrieval window
            sample_folder = await folders_collection.find_one({"_id": ObjectId(folder_ids[0])}) if folder_ids else None
            retrieval_expiry_date = sample_folder.get("retrieval_expiry_date") if sample_folder else None
            # Ensure timezone-aware
            if retrieval_expiry_date and retrieval_expiry_date.tzinfo is None:
                retrieval_expiry_date = retrieval_expiry_date.replace(tzinfo=timezone.utc)
            if retrieval_expiry_date:
                if now > retrieval_expiry_date:
                    # Retrieval window expired: return to Deep Archive and clear fields
                    update_data = {
                        "status": FolderStatus.INACTIVE.value,
                        "storage_type": StorageType.DEEP_ARCHIVE.value,
                        "updated_at": now
                    }
                    # Clear retrieval fields
                    await folders_collection.update_many(
                        {"_id": {"$in": [ObjectId(fid) for fid in folder_ids]}},
                        {"$unset": {"retrieval_days": "", "retrieval_expiry_date": ""}}
                    )
                else:
                    # Retrieval window active: set ACTIVE but keep retrieval fields
                    update_data = {
                        "status": FolderStatus.ACTIVE.value,
                        "storage_type": sample_folder.get("storage_type"),
                        "updated_at": now
                        # Do NOT clear retrieval fields
                    }
            else:
                # Not a Deep Archive retrieval: clear fields as before
                update_data = {
                    "status": FolderStatus.ACTIVE.value,
                    "storage_type": target_storage.value,
                    "updated_at": now
                }
                # Clear retrieval fields
                await folders_collection.update_many(
                    {"_id": {"$in": [ObjectId(fid) for fid in folder_ids]}},
                    {"$unset": {"retrieval_days": "", "retrieval_expiry_date": ""}}
                )
            await folders_collection.update_many(
                {"_id": {"$in": [ObjectId(fid) for fid in folder_ids]}},
                {"$set": update_data}
            )
            return {"success": True, "message": "All files converted. Folder(s) set to ACTIVE.", "folders_updated": folder_ids}
        else:
            return {"success": False, "message": f"{len(not_converted)} file(s) not yet converted.", "not_converted_files": [f["_id"] for f in not_converted]}
    
    async def _get_all_child_folders(self, parent_folder_id: str) -> List[str]:
        """Recursively get all child folder IDs"""
        folders_collection = await get_folders_collection()
        
        all_folders = [parent_folder_id]
        
        async def get_children(folder_id: str):
            children = await folders_collection.find(
                {"parent_folder_id": folder_id, "deleted": False}
            ).to_list(None)
            
            for child in children:
                child_id = str(child["_id"])
                all_folders.append(child_id)
                await get_children(child_id)  # Recursive
        
        await get_children(parent_folder_id)
        return all_folders
    



# Global instance
storage_conversion_service = StorageConversionService() 