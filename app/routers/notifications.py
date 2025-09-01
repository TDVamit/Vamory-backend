from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import Optional
from bson import ObjectId
from datetime import datetime, timezone
from app.models.notification import (
    NotificationCreate, Notification, NotificationUpdate, NotificationInDB,
    PaginatedNotificationsResponse, NotificationStatus, NotificationMarkReadResponse,
    NotificationStatsResponse
)
from app.models.user import User
from app.dependencies import get_current_user, get_current_user_id
from app.database import get_notifications_collection
from app.utils import calculate_pagination_metadata, calculate_skip_from_page, convert_objectid
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.post("/", response_model=Notification)
async def create_notification(
    notification: NotificationCreate,
    current_user: User = Depends(get_current_user)
):
    """Create a new notification"""
    try:
        notifications_collection = await get_notifications_collection()
        
        # Create notification document
        notification_doc = {
            "message": notification.message,
            "message_data": notification.message_data,
            "to_user_id": notification.to_user_id,
            "email_sent": notification.email_sent,
            "notification_read": notification.notification_read,
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
        raise HTTPException(status_code=500, detail="Failed to create notification")


@router.get("/", response_model=PaginatedNotificationsResponse)
async def get_notifications(
    status_filter: NotificationStatus = Query(NotificationStatus.ALL, description="Filter by notification status"),
    search: str = Query("", description="Search in notification messages (regex supported)"),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    per_page: int = Query(20, ge=1, le=100, description="Number of notifications per page"),
    sort_by: str = Query("created_at", description="Sort by field: created_at, message, notification_read"),
    sort_order: str = Query("desc", regex="^(asc|desc)$", description="Sort order"),
    user_id: str = Depends(get_current_user_id)
):
    """Get notifications with pagination, filtering, and search"""
    try:
        notifications_collection = await get_notifications_collection()
        
        # Build query
        query = {"to_user_id": user_id}
        
        # Add status filter
        if status_filter == NotificationStatus.READ:
            query["notification_read"] = True
        elif status_filter == NotificationStatus.UNREAD:
            query["notification_read"] = False
        # For ALL, don't add any filter
        
        # Add search filter
        if search:
            query["message"] = {"$regex": search, "$options": "i"}
        
        # Get total count
        total_count = await notifications_collection.count_documents(query)
        
        # Calculate pagination
        skip = calculate_skip_from_page(page, per_page)
        
        # Build sort
        sort_direction = 1 if sort_order == "asc" else -1
        sort_criteria = [(sort_by, sort_direction)]
        
        # Get notifications
        cursor = notifications_collection.find(query).sort(sort_criteria).skip(skip).limit(per_page)
        notifications = await cursor.to_list(None)
        
        # Convert ObjectIds to strings
        notifications_converted = [Notification(**convert_objectid(notification)) for notification in notifications]
        
        # Calculate pagination metadata
        pagination_metadata = calculate_pagination_metadata(total_count, page, per_page)
        
        return PaginatedNotificationsResponse(
            notifications=notifications_converted,
            total_count=total_count,
            page=page,
            per_page=per_page,
            total_pages=pagination_metadata.page_count,
            has_next=pagination_metadata.has_next,
            has_prev=pagination_metadata.has_prev
        )
        
    except Exception as e:
        logger.error(f"Error getting notifications: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve notifications")


@router.get("/stats", response_model=NotificationStatsResponse)
async def get_notification_stats(
    user_id: str = Depends(get_current_user_id)
):
    """Get notification statistics for the current user"""
    try:
        notifications_collection = await get_notifications_collection()
        
        # Get total count
        total_notifications = await notifications_collection.count_documents({"to_user_id": user_id})
        
        # Get unread count
        unread_count = await notifications_collection.count_documents({
            "to_user_id": user_id,
            "notification_read": False
        })
        
        # Get read count
        read_count = await notifications_collection.count_documents({
            "to_user_id": user_id,
            "notification_read": True
        })
        
        # Get email sent count
        email_sent_count = await notifications_collection.count_documents({
            "to_user_id": user_id,
            "email_sent": True
        })
        
        return NotificationStatsResponse(
            total_notifications=total_notifications,
            unread_count=unread_count,
            read_count=read_count,
            email_sent_count=email_sent_count
        )
        
    except Exception as e:
        logger.error(f"Error getting notification stats: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve notification statistics")


@router.get("/{notification_id}", response_model=Notification)
async def get_notification(
    notification_id: str,
    user_id: str = Depends(get_current_user_id)
):
    """Get a specific notification"""
    try:
        notifications_collection = await get_notifications_collection()
        
        notification = await notifications_collection.find_one({
            "_id": ObjectId(notification_id),
            "to_user_id": user_id
        })
        
        if not notification:
            raise HTTPException(status_code=404, detail="Notification not found")
        
        return Notification(**convert_objectid(notification))
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting notification {notification_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve notification")


@router.put("/{notification_id}", response_model=Notification)
async def update_notification(
    notification_id: str,
    notification_update: NotificationUpdate,
    user_id: str = Depends(get_current_user_id)
):
    """Update a notification"""
    try:
        notifications_collection = await get_notifications_collection()
        
        # Check if notification exists and belongs to user
        notification = await notifications_collection.find_one({
            "_id": ObjectId(notification_id),
            "to_user_id": user_id
        })
        
        if not notification:
            raise HTTPException(status_code=404, detail="Notification not found")
        
        # Build update data
        update_data = {}
        for field, value in notification_update.dict(exclude_unset=True).items():
            if value is not None:
                update_data[field] = value
        
        if update_data:
            update_data["updated_at"] = datetime.now(timezone.utc)
            
            await notifications_collection.update_one(
                {"_id": ObjectId(notification_id)},
                {"$set": update_data}
            )
        
        # Get updated notification
        updated_notification = await notifications_collection.find_one({"_id": ObjectId(notification_id)})
        
        return Notification(**convert_objectid(updated_notification))
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating notification {notification_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to update notification")


@router.post("/{notification_id}/mark-read", response_model=NotificationMarkReadResponse)
async def mark_notification_as_read(
    notification_id: str,
    user_id: str = Depends(get_current_user_id)
):
    """Mark a notification as read"""
    try:
        notifications_collection = await get_notifications_collection()
        
        # Check if notification exists and belongs to user
        notification = await notifications_collection.find_one({
            "_id": ObjectId(notification_id),
            "to_user_id": user_id
        })
        
        if not notification:
            raise HTTPException(status_code=404, detail="Notification not found")
        
        # Check if already read
        if notification.get("notification_read", False):
            return NotificationMarkReadResponse(
                message="Notification was already marked as read",
                notification_id=notification_id,
                marked_as_read=False,
                read_at=notification.get("read_at")
            )
        
        # Mark as read
        read_time = datetime.now(timezone.utc)
        await notifications_collection.update_one(
            {"_id": ObjectId(notification_id)},
            {"$set": {
                "notification_read": True,
                "read_at": read_time
            }}
        )
        
        return NotificationMarkReadResponse(
            message="Notification marked as read",
            notification_id=notification_id,
            marked_as_read=True,
            read_at=read_time
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error marking notification {notification_id} as read: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to mark notification as read")


@router.post("/mark-all-read")
async def mark_all_notifications_as_read(
    user_id: str = Depends(get_current_user_id)
):
    """Mark all unread notifications as read for the current user"""
    try:
        notifications_collection = await get_notifications_collection()
        
        read_time = datetime.now(timezone.utc)
        
        # Update all unread notifications
        result = await notifications_collection.update_many(
            {
                "to_user_id": user_id,
                "notification_read": False
            },
            {"$set": {
                "notification_read": True,
                "read_at": read_time
            }}
        )
        
        return {
            "message": f"Marked {result.modified_count} notifications as read",
            "notifications_updated": result.modified_count,
            "read_at": read_time
        }
        
    except Exception as e:
        logger.error(f"Error marking all notifications as read for user {user_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to mark all notifications as read")


@router.delete("/{notification_id}")
async def delete_notification(
    notification_id: str,
    user_id: str = Depends(get_current_user_id)
):
    """Delete a notification"""
    try:
        notifications_collection = await get_notifications_collection()
        
        # Check if notification exists and belongs to user
        notification = await notifications_collection.find_one({
            "_id": ObjectId(notification_id),
            "to_user_id": user_id
        })
        
        if not notification:
            raise HTTPException(status_code=404, detail="Notification not found")
        
        # Delete the notification
        await notifications_collection.delete_one({"_id": ObjectId(notification_id)})
        
        return {"message": "Notification deleted successfully", "notification_id": notification_id}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting notification {notification_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to delete notification")
