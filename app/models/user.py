from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class UserBase(BaseModel):
    full_name: Optional[str] = None   # set by user later in Profile
    phone: Optional[str] = None
    email: Optional[str] = None


class UserCreate(UserBase):
    pass


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None        # Allow linking a phone number to an email-registered account
    date_of_birth: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None
    aadhar_number: Optional[str] = None
    pan_number: Optional[str] = None
    avatar_url: Optional[str] = None
    location: Optional[str] = None  # Selected area / locality


class LocationUpdate(BaseModel):
    location: str


class UserResponse(BaseModel):
    """Shape returned to the Flutter client.
    `id` is the Mongo `_id` rendered as a string by helpers.serialize_doc."""

    id: Optional[str] = None
    full_name: Optional[str] = None   # user fills this in via Profile
    phone: Optional[str] = None
    email: Optional[str] = None
    date_of_birth: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None
    aadhar_number: Optional[str] = None
    pan_number: Optional[str] = None
    avatar_url: Optional[str] = None
    location: Optional[str] = None
    is_verified: bool = False
    created_at: Optional[datetime] = None

    model_config = {"populate_by_name": True}
