from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from bson import ObjectId
from enum import Enum


class EmailType(str, Enum):
    LOW_CREDIT = "low_credit"
    CREDITS_EXPIRED = "credits_expired"
    PAYMENT_SUCCESS = "payment_success"
    RETRIEVAL_SUCCESS = "retrieval_success"
    FOLDER_SHARED = "folder_shared"


class EmailTrackingBase(BaseModel):
    email_type: EmailType = Field(..., description="Type of email sent")
    to_email: str = Field(..., description="Recipient email address")
    user_name: str = Field(..., description="Name of the user")
    credit_amount: Optional[str] = Field(None, description="Credit amount (for credit-related emails)")
    sent_at: datetime = Field(default_factory=datetime.utcnow, description="When the email was sent")
    subject: str = Field(..., description="Email subject line")


class EmailTrackingCreate(EmailTrackingBase):
    pass


class EmailTrackingInDB(EmailTrackingBase):
    id: str = Field(..., alias="_id")
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}


class EmailTracking(EmailTrackingBase):
    id: str = Field(..., alias="_id")
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}
