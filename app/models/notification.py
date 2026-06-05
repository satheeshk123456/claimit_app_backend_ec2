from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class NotificationCreate(BaseModel):
    user_id: str
    title: str
    message: str
    type: str = "info"  # info, success, warning, error
    claim_id: Optional[str] = None


class NotificationInDB(BaseModel):
    id: Optional[str] = Field(alias="_id", default=None)
    user_id: str
    title: str
    message: str
    type: str = "info"
    is_read: bool = False
    claim_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True


class NotificationResponse(BaseModel):
    id: Optional[str] = None
    user_id: str
    title: str
    message: str
    type: str
    is_read: bool
    claim_id: Optional[str] = None
    created_at: datetime

    class Config:
        populate_by_name = True
