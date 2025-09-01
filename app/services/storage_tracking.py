"""
Storage tracking service for managing user storage usage across standard and archived storage types.
"""
from app.database import get_users_collection
from app.models.file import StorageType
from app.services.billing_utils import calculate_total_billing_size
from bson import ObjectId


class StorageTrackingService:
    """Service to track user storage usage across different storage types"""
    
    @staticmethod
    async def update_user_storage(user_id: str, size_bytes: int, storage_type: StorageType, operation: str):
        """
        Update user storage usage based on file operations.
        
        Args:
            user_id: The user ID
            size_bytes: Size in bytes (positive for additions, negative for deletions)
            storage_type: The storage type (STANDARD or DEEP_ARCHIVE)
            operation: Operation type for logging ('upload', 'delete', 'archive', 'unarchive')
        """
        users_collection = await get_users_collection()
        
        # Determine which storage field to update based on storage type
        if storage_type == StorageType.DEEP_ARCHIVE:
            storage_field = "storage_used_archived"
        else:  # STANDARD
            storage_field = "storage_used_standard"
        
        # Update the appropriate storage field
        await users_collection.update_one(
            {"_id": user_id},
            {"$inc": {storage_field: size_bytes}}
        )
        
        print(f"📊 Storage tracking: {operation} - User {user_id}, {size_bytes} bytes, {storage_type.value} -> {storage_field}")
    
    @staticmethod
    async def update_user_storage_with_billing_size(user_id: str, file_size: int, thumbnail_size: int, storage_type: StorageType, operation: str):
        """
        Update user storage usage using billing size calculation (file + thumbnail, each with minimum 128KB).
        
        Args:
            user_id: The user ID
            file_size: The actual file size in bytes
            thumbnail_size: The thumbnail size in bytes (0 if no thumbnail)
            storage_type: The storage type (STANDARD or DEEP_ARCHIVE)
            operation: Operation type for logging ('upload', 'delete', 'archive', 'unarchive')
        """
        total_billing_size = calculate_total_billing_size(file_size, thumbnail_size)
        await StorageTrackingService.update_user_storage(user_id, total_billing_size, storage_type, operation)
    
    @staticmethod
    async def move_storage_usage(user_id: str, size_bytes: int, from_storage: StorageType, to_storage: StorageType):
        """
        Move storage usage from one type to another (e.g., when archiving files).
        
        Args:
            user_id: The user ID
            size_bytes: Size in bytes to move
            from_storage: Source storage type
            to_storage: Destination storage type
        """
        users_collection = await get_users_collection()
        
        # Determine source and destination fields
        from_field = "storage_used_archived" if from_storage == StorageType.DEEP_ARCHIVE else "storage_used_standard"
        to_field = "storage_used_archived" if to_storage == StorageType.DEEP_ARCHIVE else "storage_used_standard"
        
        # If moving between different storage categories, update both fields
        if from_field != to_field:
            await users_collection.update_one(
                {"_id": user_id},
                {
                    "$inc": {
                        from_field: -size_bytes,  # Subtract from source
                        to_field: size_bytes      # Add to destination
                    }
                }
            )
            print(f"📊 Storage tracking: Archive conversion - User {user_id}, {size_bytes} bytes, {from_storage.value} -> {to_storage.value}")
        else:
            print(f"📊 Storage tracking: No change needed - both {from_storage.value} and {to_storage.value} use same field {from_field}")

    @staticmethod
    async def soft_delete_file(user_id: str, file_size: int, thumbnail_size: int, storage_type: StorageType):
        """
        Handle soft deletion of a file by moving storage from active to deleted tracking.
        
        Args:
            user_id: The user ID
            file_size: The actual file size in bytes
            thumbnail_size: The thumbnail size in bytes (0 if no thumbnail)
            storage_type: The storage type (STANDARD or DEEP_ARCHIVE)
        """
        users_collection = await get_users_collection()
        
        # Calculate total billing size
        total_billing_size = calculate_total_billing_size(file_size, thumbnail_size)
        
        # Determine which fields to update based on storage type
        if storage_type == StorageType.DEEP_ARCHIVE:
            active_field = "storage_used_archived"
            deleted_field = "storage_used_archived_deleted"
        else:  # STANDARD
            active_field = "storage_used_standard"
            deleted_field = "storage_used_standard_deleted"
        
        # Move storage from active to deleted tracking
        await users_collection.update_one(
            {"_id": user_id},
            {
                "$inc": {
                    active_field: -total_billing_size,      # Subtract from active
                    deleted_field: total_billing_size       # Add to deleted
                }
            }
        )
        
        print(f"📊 Storage tracking: Soft delete - User {user_id}, {total_billing_size} bytes (file: {file_size}, thumbnail: {thumbnail_size}), {storage_type.value} -> moved to deleted tracking")

    @staticmethod
    async def restore_deleted_file(user_id: str, file_size: int, thumbnail_size: int, storage_type: StorageType):
        """
        Handle restoration of a soft-deleted file by moving storage from deleted back to active tracking.
        
        Args:
            user_id: The user ID
            file_size: The actual file size in bytes
            thumbnail_size: The thumbnail size in bytes (0 if no thumbnail)
            storage_type: The storage type (STANDARD or DEEP_ARCHIVE)
        """
        users_collection = await get_users_collection()
        
        # Calculate total billing size
        total_billing_size = calculate_total_billing_size(file_size, thumbnail_size)
        
        # Determine which fields to update based on storage type
        if storage_type == StorageType.DEEP_ARCHIVE:
            active_field = "storage_used_archived"
            deleted_field = "storage_used_archived_deleted"
        else:  # STANDARD
            active_field = "storage_used_standard"
            deleted_field = "storage_used_standard_deleted"
        
        # Move storage from deleted back to active tracking
        await users_collection.update_one(
            {"_id": user_id},
            {
                "$inc": {
                    deleted_field: -total_billing_size,     # Subtract from deleted
                    active_field: total_billing_size        # Add back to active
                }
            }
        )
        
        print(f"📊 Storage tracking: Restore deleted - User {user_id}, {total_billing_size} bytes (file: {file_size}, thumbnail: {thumbnail_size}), {storage_type.value} -> moved back to active tracking")

    @staticmethod
    async def bulk_soft_delete_files(user_id: str, files_data: list):
        """
        Handle bulk soft deletion of files by moving storage from active to deleted tracking.
        
        Args:
            user_id: The user ID
            files_data: List of dicts with 'file_size', 'thumbnail_size', and 'storage_type' keys
        """
        users_collection = await get_users_collection()
        
        # Group files by storage type and calculate totals
        standard_total = 0
        archived_total = 0
        
        for file_data in files_data:
            file_size = file_data.get('file_size', 0)
            thumbnail_size = file_data.get('thumbnail_size', 0)
            storage_type = StorageType(file_data.get('storage_type', StorageType.STANDARD))
            
            total_billing_size = calculate_total_billing_size(file_size, thumbnail_size)
            
            if storage_type == StorageType.DEEP_ARCHIVE:
                archived_total += total_billing_size
            else:
                standard_total += total_billing_size
        
        # Update storage tracking for all files at once
        update_fields = {}
        if standard_total > 0:
            update_fields.update({
                "storage_used_standard": -standard_total,
                "storage_used_standard_deleted": standard_total
            })
        if archived_total > 0:
            update_fields.update({
                "storage_used_archived": -archived_total,
                "storage_used_archived_deleted": archived_total
            })
        
        if update_fields:
            await users_collection.update_one(
                {"_id": user_id},
                {"$inc": update_fields}
            )
            
            print(f"📊 Storage tracking: Bulk soft delete - User {user_id}, Standard: {standard_total} bytes, Archived: {archived_total} bytes")


# Global instance
storage_tracking_service = StorageTrackingService()



