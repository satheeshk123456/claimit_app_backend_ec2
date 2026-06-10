from fastapi import APIRouter, Depends, HTTPException
from bson import ObjectId
from datetime import datetime
from pydantic import BaseModel
from typing import Optional, List
from ..database import get_db
from ..utils.auth import get_current_user
from ..utils.helpers import serialize_doc

router = APIRouter(prefix="/policies", tags=["Policies"])


class PolicyCreate(BaseModel):
    policy_number: str
    policy_type: str
    insurer_name: str
    premium_amount: float
    sum_insured: float
    start_date: datetime
    end_date: datetime
    coverages: Optional[List[str]] = []


@router.get("")
async def get_policies(current_user: dict = Depends(get_current_user)):
    db = get_db()
    user_id = current_user.get("_id") or current_user.get("id")

    cursor = db.policies.find({"user_id": user_id}).sort("created_at", -1)
    policies = await cursor.to_list(length=50)
    return [serialize_doc(p) for p in policies]


@router.post("", status_code=201)
async def create_policy(
    policy_data: PolicyCreate,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    user_id = current_user.get("_id") or current_user.get("id")

    existing = await db.policies.find_one({"policy_number": policy_data.policy_number})
    if existing:
        raise HTTPException(status_code=409, detail="Policy number already exists")

    policy_doc = {
        "user_id": user_id,
        **policy_data.model_dump(),
        "status": "active",
        "created_at": datetime.utcnow(),
    }

    result = await db.policies.insert_one(policy_doc)
    policy_doc["_id"] = str(result.inserted_id)
    return serialize_doc(policy_doc)


@router.get("/{policy_id}")
async def get_policy(
    policy_id: str,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    user_id = current_user.get("_id") or current_user.get("id")

    try:
        policy = await db.policies.find_one({
            "_id": ObjectId(policy_id),
            "user_id": user_id,
        })
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid policy ID")

    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")

    return serialize_doc(policy)
