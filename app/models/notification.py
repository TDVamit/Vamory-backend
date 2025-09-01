from pydantic import BaseModel, Field
from typing import Dict, Any, Optional
from datetime import datetime
from bson import ObjectId
from enum import Enum


class NotificationStatus(str, Enum):
    READ = "read"
    UNREAD = "unread"
    ALL = "all"


class NotificationBase(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000, description="Notification message content")
    message_data: Dict[str, Any] = Field(default_factory=dict, description="Additional data for the notification")
    to_user_id: str = Field(..., description="ID of the user receiving the notification")
    email_sent: bool = Field(default=False, description="Whether email has been sent for this notification")
    notification_read: bool = Field(default=False, description="Whether the notification has been read")


class NotificationCreate(NotificationBase):
    pass


class NotificationInDB(NotificationBase):
    id: str = Field(..., alias="_id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    read_at: Optional[datetime] = None
    mail_sent_at: Optional[datetime] = None
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}


class Notification(NotificationBase):
    id: str = Field(..., alias="_id")
    created_at: datetime
    read_at: Optional[datetime] = None
    mail_sent_at: Optional[datetime] = None
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}


class NotificationUpdate(BaseModel):
    notification_read: Optional[bool] = None
    email_sent: Optional[bool] = None
    read_at: Optional[datetime] = None
    mail_sent_at: Optional[datetime] = None


class PaginatedNotificationsResponse(BaseModel):
    notifications: list[Notification]
    total_count: int
    page: int
    per_page: int
    total_pages: int
    has_next: bool
    has_prev: bool


class NotificationMarkReadResponse(BaseModel):
    message: str
    notification_id: str
    marked_as_read: bool
    read_at: datetime


class NotificationStatsResponse(BaseModel):
    total_notifications: int
    unread_count: int
    read_count: int
    email_sent_count: int
