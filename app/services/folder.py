from typing import Dict, List
from bson import ObjectId
from app.database import get_files_collection, get_folders_collection


class FolderService:
    """Service for folder-related operations"""
    
    async def calculate_folder_size(self, folder_id: str) -> int:
        """Calculate total size of a folder including all files and subfolders recursively"""
        total_size = 0
        
        # Get files collection
        files_collection = await get_files_collection()
        folders_collection = await get_folders_collection()
        
        # Calculate size of direct files in this folder (excluding deleted files)
        files_cursor = files_collection.find({"folder_id": folder_id, "deleted": False})
        async for file_doc in files_cursor:
            total_size += file_doc.get("file_size", 0)
        
        # Get all subfolders and calculate their sizes recursively (excluding deleted folders)
        subfolders_cursor = folders_collection.find({"parent_folder_id": folder_id, "deleted": False})
        async for subfolder in subfolders_cursor:
            subfolder_size = await self.calculate_folder_size(str(subfolder["_id"]))
            total_size += subfolder_size
        
        return total_size
    
    async def calculate_multiple_folder_sizes(self, folder_ids: List[str]) -> Dict[str, int]:
        """Calculate sizes for multiple folders efficiently"""
        sizes = {}
        for folder_id in folder_ids:
            sizes[folder_id] = await self.calculate_folder_size(folder_id)
        return sizes
    
    async def get_folder_stats(self, folder_id: str) -> Dict[str, int]:
        """Get comprehensive stats for a folder"""
        files_collection = await get_files_collection()
        folders_collection = await get_folders_collection()
        
        # Count direct files (excluding deleted files)
        file_count = await files_collection.count_documents({"folder_id": folder_id, "deleted": False})
        
        # Count direct subfolders (excluding deleted folders)
        subfolder_count = await folders_collection.count_documents({"parent_folder_id": folder_id, "deleted": False})
        
        # Calculate total size
        total_size = await self.calculate_folder_size(folder_id)
        
        # Get file type breakdown (excluding deleted files)
        file_type_pipeline = [
            {"$match": {"folder_id": folder_id, "deleted": False}},
            {"$group": {
                "_id": "$file_type",
                "count": {"$sum": 1},
                "total_size": {"$sum": "$file_size"}
            }}
        ]
        
        file_types = {}
        async for result in files_collection.aggregate(file_type_pipeline):
            file_types[result["_id"]] = {
                "count": result["count"],
                "total_size": result["total_size"]
            }
        
        return {
            "file_count": file_count,
            "subfolder_count": subfolder_count,
            "total_size": total_size,
            "file_types": file_types
        }


# Create a global instance
folder_service = FolderService() 