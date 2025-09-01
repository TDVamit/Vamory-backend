from typing import Dict, Any, Optional
from datetime import datetime, timezone, timedelta
from bson import ObjectId
from app.database import get_notifications_collection
from app.models.notification import NotificationCreate, Notification
from app.utils import convert_objectid
import uuid
import logging

logger = logging.getLogger(__name__)


class NotificationService:
    """Service for managing notifications"""
    
    async def create_notification(
        self,
        to_user_id: str,
        message: str,
        message_data: Optional[Dict[str, Any]] = None
    ) -> Notification:
        """
        Create a new notification
        
        Args:
            to_user_id: ID of the user receiving the notification
            message: Notification message content
            message_data: Additional data for the notification
        
        Returns:
            Created notification object
        """
        try:
            notifications_collection = await get_notifications_collection()
            
            # Create notification document
            notification_doc = {
                "message": message,
                "message_data": message_data or {},
                "to_user_id": to_user_id,
                "email_sent": False,
                "notification_read": False,
                "created_at": datetime.now(timezone.utc),
                "read_at": None,
                "mail_sent_at": None
            }
            
            result = await notifications_collection.insert_one(notification_doc)
            
            # Retrieve the created notification
            created_notification = await notifications_collection.find_one({"_id": result.inserted_id})
            
            return Notification(**convert_objectid(created_notification))
            
        except Exception as e:
            logger.error(f"Error creating notification: {str(e)}")
            raise
    
    async def create_bulk_notifications(
        self,
        user_ids: list[str],
        message: str,
        message_data: Optional[Dict[str, Any]] = None
    ) -> list[Notification]:
        """
        Create notifications for multiple users
        
        Args:
            user_ids: List of user IDs to send notifications to
            message: Notification message content
            message_data: Additional data for the notification
        
        Returns:
            List of created notification objects
        """
        try:
            notifications_collection = await get_notifications_collection()
            
            # Prepare notification documents
            notification_docs = []
            for user_id in user_ids:
                notification_doc = {
                    "message": message,
                    "message_data": message_data or {},
                    "to_user_id": user_id,
                    "email_sent": False,
                    "notification_read": False,
                    "created_at": datetime.now(timezone.utc),
                    "read_at": None,
                    "mail_sent_at": None
                }
                notification_docs.append(notification_doc)
            
            # Insert all notifications
            result = await notifications_collection.insert_many(notification_docs)
            
            # Retrieve created notifications
            created_notifications = await notifications_collection.find({
                "_id": {"$in": result.inserted_ids}
            }).to_list(None)
            
            return [Notification(**convert_objectid(notification)) for notification in created_notifications]
            
        except Exception as e:
            logger.error(f"Error creating bulk notifications: {str(e)}")
            raise
    
    async def mark_notification_as_read(self, notification_id: str, user_id: str) -> bool:
        """
        Mark a notification as read
        
        Args:
            notification_id: ID of the notification
            user_id: ID of the user (for security check)
        
        Returns:
            True if marked as read, False if already read or not found
        """
        try:
            notifications_collection = await get_notifications_collection()
            
            result = await notifications_collection.update_one(
                {
                    "_id": ObjectId(notification_id),
                    "to_user_id": user_id,
                    "notification_read": False
                },
                {"$set": {
                    "notification_read": True,
                    "read_at": datetime.now(timezone.utc)
                }}
            )
            
            return result.modified_count > 0
            
        except Exception as e:
            logger.error(f"Error marking notification as read: {str(e)}")
            raise
    
    async def mark_email_as_sent(self, notification_id: str) -> bool:
        """
        Mark a notification email as sent
        
        Args:
            notification_id: ID of the notification
        
        Returns:
            True if marked as sent, False if not found
        """
        try:
            notifications_collection = await get_notifications_collection()
            
            result = await notifications_collection.update_one(
                {"_id": ObjectId(notification_id)},
                {"$set": {
                    "email_sent": True,
                    "mail_sent_at": datetime.now(timezone.utc)
                }}
            )
            
            return result.modified_count > 0
            
        except Exception as e:
            logger.error(f"Error marking email as sent: {str(e)}")
            raise
    
    async def get_unread_count(self, user_id: str) -> int:
        """
        Get count of unread notifications for a user
        
        Args:
            user_id: ID of the user
        
        Returns:
            Number of unread notifications
        """
        try:
            notifications_collection = await get_notifications_collection()
            
            count = await notifications_collection.count_documents({
                "to_user_id": user_id,
                "notification_read": False
            })
            
            return count
            
        except Exception as e:
            logger.error(f"Error getting unread count: {str(e)}")
            raise
    
    async def delete_old_notifications(self, days_old: int = 90) -> int:
        """
        Delete notifications older than specified days
        
        Args:
            days_old: Number of days old to consider for deletion
        
        Returns:
            Number of notifications deleted
        """
        try:
            notifications_collection = await get_notifications_collection()
            
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_old)
            
            result = await notifications_collection.delete_many({
                "created_at": {"$lt": cutoff_date}
            })
            
            logger.info(f"Deleted {result.deleted_count} old notifications")
            return result.deleted_count
            
        except Exception as e:
            logger.error(f"Error deleting old notifications: {str(e)}")
            raise


# Global instance
notification_service = NotificationService()
