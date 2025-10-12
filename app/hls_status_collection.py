"""
HLS Status Collection Module
Direct access to the hls_conversion_status collection
"""
from app.database import get_hls_conversion_status_collection
from datetime import datetime
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
# Let uvicorn's handler display the logs
logger.propagate = True

class HlsStatusCollection:
    """Direct access to HLS conversion status collection"""
    
    def __init__(self):
        self._collection = None
    
    async def get_collection(self):
        """Get the collection instance"""
        if self._collection is None:
            self._collection = await get_hls_conversion_status_collection()
        return self._collection
    
    async def find_one_and_update(self, query: dict, update: dict, return_document: bool = True):
        """Find and update a document in the collection"""
        try:
            collection = await self.get_collection()
            if collection is None:
                logger.error("Failed to get HLS conversion status collection")
                return None
            
            # Add updated_at timestamp to update
            if "$set" in update:
                update["$set"]["updated_at"] = datetime.utcnow()
            else:
                update["updated_at"] = datetime.utcnow()
            
            result = await collection.find_one_and_update(
                query,
                update,
                return_document=return_document
            )
            
            if result:
                logger.info("Updated HLS conversion status: query=%s, update=%s", query, update)
            else:
                logger.warning("No HLS conversion status record found for query: %s", query)
            
            return result
            
        except Exception as e:
            logger.exception("Database update failed: %s", e)
            return None
    
    async def insert_one(self, document: dict):
        """Insert a new document into the collection"""
        try:
            collection = await self.get_collection()
            if collection is None:
                logger.error("Failed to get HLS conversion status collection")
                return None
            
            # Add timestamps
            document["created_at"] = datetime.utcnow()
            document["updated_at"] = datetime.utcnow()
            
            result = await collection.insert_one(document)
            if result.inserted_id:
                logger.info("Created new HLS conversion status: %s", document.get("job_id"))
            
            return result
            
        except Exception as e:
            logger.exception("Database insert failed: %s", e)
            return None

# Create a global instance
hls_status_collection = HlsStatusCollection()
