from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from enum import Enum


class ClaimStatus(str, Enum):
    PENDING = "pending"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    SETTLED = "settled"


class ClaimType(str, Enum):
    HEALTH = "Health Insurance"
    MOTOR = "Motor Insurance"
    HOME = "Home Insurance"
    LIFE = "Life Insurance"
    TRAVEL = "Travel Insurance"
    PROPERTY = "Property Insurance"


class ClaimTimelineEvent(BaseModel):
    status: str
    message: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    updated_by: Optional[str] = None


class ClaimCreate(BaseModel):
    claim_type: str
    policy_number: str
    description: str
    claim_amount: float
    incident_date: datetime


class ClaimUpdate(BaseModel):
    status: Optional[ClaimStatus] = None
    rejection_reason: Optional[str] = None
    approved_amount: Optional[float] = None
    assigned_agent: Optional[str] = None


class ClaimInDB(BaseModel):
    id: Optional[str] = Field(alias="_id", default=None)
    user_id: str
    claim_number: str
    claim_type: str
    policy_number: str
    description: str
    claim_amount: float
    status: ClaimStatus = ClaimStatus.PENDING
    documents: List[str] = []
    timeline: List[ClaimTimelineEvent] = []
    incident_date: datetime
    submitted_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    approved_amount: Optional[float] = None
    assigned_agent: Optional[str] = None

    class Config:
        populate_by_name = True
        use_enum_values = True


class ClaimResponse(BaseModel):
    id: Optional[str] = None
    user_id: str
    claim_number: str
    claim_type: str
    policy_number: str
    description: str
    claim_amount: float
    status: str
    documents: List[str] = []
    timeline: List[ClaimTimelineEvent] = []
    incident_date: datetime
    submitted_at: datetime
    updated_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    approved_amount: Optional[float] = None
    assigned_agent: Optional[str] = None

    class Config:
        populate_by_name = True
