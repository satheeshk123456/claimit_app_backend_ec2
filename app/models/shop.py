from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


# ─────────────────────────────────────────────────────────────────────────────
# Shop models
# ─────────────────────────────────────────────────────────────────────────────

class ShopCreate(BaseModel):
    name: str
    location: str
    category_ids: List[int]
    discount: int
    rating: float
    has_rewards: bool = True
    has_redeem: bool = True
    address: str = ''
    timing: str = ''
    phone: str = ''
    email: str = ''
    image_name: str = ''
    added_days_ago: int = 0
    lat: Optional[float] = None   # GPS latitude
    lng: Optional[float] = None   # GPS longitude


class ShopResponse(BaseModel):
    id: Optional[str] = None
    name: str
    location: str
    category_ids: List[int]
    discount: int
    rating: float
    review_count: int = 0
    has_rewards: bool
    has_redeem: bool
    address: str = ''
    timing: str = ''
    phone: str = ''
    email: str = ''
    image_name: str = ''
    image_data: Optional[str] = None
    added_days_ago: int = 0
    lat: Optional[float] = None
    lng: Optional[float] = None
    distance: Optional[str] = None  # computed at query time e.g. "3.2 km"
    created_at: Optional[datetime] = None

    model_config = {"populate_by_name": True}


# ─────────────────────────────────────────────────────────────────────────────
# Review models
# ─────────────────────────────────────────────────────────────────────────────

class ReviewCreate(BaseModel):
    rating: float = Field(..., ge=1, le=5)
    comment: str = Field(..., min_length=1, max_length=1000)


class ReviewResponse(BaseModel):
    id: Optional[str] = None
    shop_id: str
    user_id: str
    user_name: str = ''
    user_avatar: Optional[str] = None
    rating: float
    comment: str
    created_at: Optional[datetime] = None

    model_config = {"populate_by_name": True}


# ─────────────────────────────────────────────────────────────────────────────
# Reward models
# ─────────────────────────────────────────────────────────────────────────────

class RewardCreate(BaseModel):
    shop_id: str
    title: str
    description: str
    points_required: int
    discount_percent: int
    valid_days: int = 30


class RewardResponse(BaseModel):
    id: Optional[str] = None
    shop_id: str
    shop_name: Optional[str] = None
    title: str
    description: str
    points_required: int
    discount_percent: int
    expires_at: Optional[datetime] = None
    is_active: bool = True

    model_config = {"populate_by_name": True}


# ─────────────────────────────────────────────────────────────────────────────
# Redeem models
# ─────────────────────────────────────────────────────────────────────────────

class RedeemCreate(BaseModel):
    shop_id: str
    reward_id: str


class RedeemResponse(BaseModel):
    id: Optional[str] = None
    user_id: str
    shop_id: str
    shop_name: Optional[str] = None
    reward_id: str
    reward_title: Optional[str] = None
    status: str = 'pending'
    coupon_code: str = ''
    redeemed_at: Optional[datetime] = None
    used_at: Optional[datetime] = None

    model_config = {"populate_by_name": True}


# ─────────────────────────────────────────────────────────────────────────────
# Eligibility models
# ─────────────────────────────────────────────────────────────────────────────

class EligibilityRequest(BaseModel):
    shop_id: str
    lat: Optional[float] = None
    lng: Optional[float] = None
