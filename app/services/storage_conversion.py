import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from bson import ObjectId
from app.models.folder import StorageType, FolderStatus, ConversionMode
from app.database import get_folders_collection, get_files_collection
from app.services.s3 import s3_service


class StorageConversionService:
    """Service to handle storage type conversions with time-based status tracking"""
    
    def generate_job_id(self) -> str:
        """Generate a unique job ID for tracking conversions"""
        return f"conv_{uuid.uuid4().hex[:12]}"
    
    def calculate_retrieval_times(
        self, 
        mode: ConversionMode, 
        retrieval_days: Optional[int]
    ) -> Tuple[datetime, datetime, Optional[datetime]]:
        """
        Calculate retrieval timing for Deep Archive conversions
        
        Returns:
            (retrieval_start, retrieval_ready, retrieval_expires)
        """
        now = datetime.now(timezone.utc)
        retrieval_start = now
        
        # Calculate when files will be ready based on mode
        if mode == ConversionMode.STANDARD:
            retrieval_ready = now + timedelta(hours=12)  # 1-12 hours, use 12 for safety
        else:  # BULK
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
        
        # Check if this is a Deep Archive retrieval in progress
        if (folder_doc.get("deep_archive_retrieval_start") and 
            folder_doc.get("deep_archive_retrieval_ready")):
            
            retrieval_start = folder_doc["deep_archive_retrieval_start"]
            retrieval_ready = folder_doc["deep_archive_retrieval_ready"]
            retrieval_expires = folder_doc.get("deep_archive_retrieval_expires")
            original_storage = folder_doc.get("deep_archive_original_storage")
            
            # Ensure all datetime objects are timezone-aware
            if retrieval_start and retrieval_start.tzinfo is None:
                retrieval_start = retrieval_start.replace(tzinfo=timezone.utc)
            if retrieval_ready and retrieval_ready.tzinfo is None:
                retrieval_ready = retrieval_ready.replace(tzinfo=timezone.utc)
            if retrieval_expires and retrieval_expires.tzinfo is None:
                retrieval_expires = retrieval_expires.replace(tzinfo=timezone.utc)
            
            # Phase 1: Converting (start -> ready)
            if retrieval_ready and now < retrieval_ready:
                return FolderStatus.CONVERTING, StorageType.DEEP_ARCHIVE
            
            # Phase 2: Active (ready -> expires or permanent)
            elif retrieval_expires is None or now < retrieval_expires:
                # Files are ready and available
                target_storage = StorageType(original_storage) if original_storage else StorageType.STANDARD_IA
                return FolderStatus.ACTIVE, target_storage
            
            # Phase 3: Expired - need to clean up and return to Deep Archive
            else:
                await self._cleanup_expired_retrieval(folder_doc["_id"])
                return FolderStatus.INACTIVE, StorageType.DEEP_ARCHIVE
        
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
                    "deep_archive_retrieval_start": "",
                    "deep_archive_retrieval_ready": "",
                    "deep_archive_retrieval_expires": "",
                    "deep_archive_original_storage": "",
                    "retrieval_days": "",
                    "retrieval_mode": "",
                    "conversion_job_id": "",
                    "conversion_started_at": "",
                    "conversion_estimated_completion": "",
                    "conversion_from_storage": "",
                    "conversion_to_storage": ""
                }
            }
        )
        
        # Update files back to Deep Archive
        await files_collection.update_many(
            {"folder_id": {"$in": folder_ids}},
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
        retrieval_days: Optional[int] = None,
        retrieval_mode: ConversionMode = ConversionMode.BULK
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
            retrieval_mode, retrieval_days
        )
        
        # Get all folders to be converted
        if apply_to_children:
            folder_ids = await self._get_all_child_folders(folder_id)
        else:
            folder_ids = [folder_id]
        
        # Get all files that need S3 restoration
        files_cursor = files_collection.find({"folder_id": {"$in": folder_ids}})
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
            "conversion_job_id": job_id,
            "conversion_started_at": retrieval_start,
            "conversion_estimated_completion": retrieval_ready,
            "conversion_from_storage": StorageType.DEEP_ARCHIVE.value,
            "conversion_to_storage": target_storage.value,
            "deep_archive_retrieval_start": retrieval_start,
            "deep_archive_retrieval_ready": retrieval_ready,
            "deep_archive_original_storage": target_storage.value,
            "retrieval_days": retrieval_days,
            "retrieval_mode": retrieval_mode.value,
            "updated_at": datetime.now(timezone.utc)
        }
        
        if retrieval_expires:
            update_data["deep_archive_retrieval_expires"] = retrieval_expires
        
        # Update all affected folders
        folder_object_ids = [ObjectId(fid) for fid in folder_ids]
        await folders_collection.update_many(
            {"_id": {"$in": folder_object_ids}},
            {"$set": update_data}
        )
        
        print(f"🚀 Started Deep Archive retrieval job {job_id}")
        print(f"   Target storage: {target_storage.value}")
        print(f"   Mode: {retrieval_mode.value}")
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
        
        # Get all files that need S3 storage class conversion
        files_cursor = files_collection.find({"folder_id": {"$in": folder_ids}})
        files_to_convert = await files_cursor.to_list(None)
        
        # Perform S3 storage class conversion for all files
        print(f"🔄 Converting S3 storage class for {len(files_to_convert)} files: {storage_from.value} → {storage_to.value}")
        s3_conversion_success = 0
        s3_conversion_errors = []
        
        for file_doc in files_to_convert:
            if file_doc.get("s3_key"):
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
        
        # Update files
        file_result = await files_collection.update_many(
            {"folder_id": {"$in": folder_ids}},
            {"$set": {
                "storage_type": storage_to.value,
                "updated_at": datetime.now(timezone.utc)
            }}
        )
        
        # Handle thumbnails for Deep Archive
        if storage_to == StorageType.DEEP_ARCHIVE:
            # Keep thumbnails but remove references from database for consistency
            # (Thumbnails will remain in S3 but won't be accessible via API)
            await files_collection.update_many(
                {"folder_id": {"$in": folder_ids}},
                {"$unset": {"thumbnail_s3_key": "", "thumbnail_s3_url": ""}}
            )
        
        print(f"✅ Immediate conversion completed: {storage_from.value} → {storage_to.value}")
        
        return job_id, folder_result.modified_count, file_result.modified_count
    
    async def _get_all_child_folders(self, parent_folder_id: str) -> List[str]:
        """Recursively get all child folder IDs"""
        folders_collection = await get_folders_collection()
        
        all_folders = [parent_folder_id]
        
        async def get_children(folder_id: str):
            children = await folders_collection.find(
                {"parent_folder_id": folder_id}
            ).to_list(None)
            
            for child in children:
                child_id = str(child["_id"])
                all_folders.append(child_id)
                await get_children(child_id)  # Recursive
        
        await get_children(parent_folder_id)
        return all_folders
    
    def get_cost_savings_info(self, mode: ConversionMode, file_count: int) -> str:
        """Get cost savings information for bulk mode"""
        if mode == ConversionMode.BULK:
            estimated_savings = min(file_count * 0.02, 100)  # Rough estimate
            return f"Bulk mode saves approximately ${estimated_savings:.2f} compared to Standard mode"
        return "Standard mode provides balanced cost and speed"


# Global instance
storage_conversion_service = StorageConversionService() 