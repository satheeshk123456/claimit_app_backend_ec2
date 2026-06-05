"""
Classifieds routes — local service & buy-sell postings.

Endpoints
─────────
GET   /classifieds              → list (filter: category, subcategory, pincode)
GET   /classifieds/{id}         → single post
POST  /classifieds              → create post (auth required)
PATCH /classifieds/{id}/toggle  → toggle availability (owner only)
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List
from bson import ObjectId
from datetime import datetime, timezone

from ..database import get_db
from ..utils.auth import get_current_user

router = APIRouter(prefix="/classifieds", tags=["classifieds"])


def _oid(id_str: str) -> ObjectId:
    try:
        return ObjectId(id_str)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")


def _serialize(doc: dict) -> dict:
    doc["id"] = str(doc.pop("_id"))
    return doc


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("")
async def list_classifieds(
    category: Optional[str] = Query(None),
    subcategory: Optional[str] = Query(None),
    pincode: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(default=20, le=50),
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    query: dict = {}
    if category:
        query["category"] = category
    if subcategory:
        query["subcategory"] = {"$regex": f"^{subcategory}$", "$options": "i"}
    if pincode:
        query["pincode"] = pincode
    if search:
        query["$or"] = [
            {"title": {"$regex": search, "$options": "i"}},
            {"user_name": {"$regex": search, "$options": "i"}},
        ]

    cursor = db["classifieds"].find(query).sort("created_at", -1).limit(limit)
    docs = await cursor.to_list(length=limit)
    return {"classifieds": [_serialize(d) for d in docs], "total": len(docs)}


# ── Single ────────────────────────────────────────────────────────────────────

@router.get("/{classified_id}")
async def get_classified(
    classified_id: str,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    doc = await db["classifieds"].find_one({"_id": _oid(classified_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Not found")
    return {"classified": _serialize(doc)}


# ── Create ────────────────────────────────────────────────────────────────────

class CreateClassifiedRequest(BaseModel):
    category: str
    subcategory: str
    title: str
    description: str
    price: float
    years_of_exp: int = 0
    pincode: str
    area: str
    address: str = ""
    payment_method: str = "Credit/Debit Card"
    photos: List[str] = []   # S3 keys (uploaded via /classifieds/upload-photo)


@router.post("", status_code=201)
async def create_classified(
    body: CreateClassifiedRequest,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    doc = {
        "user_id": current_user["_id"],
        "user_name": current_user.get("name", "User"),
        "user_phone": current_user.get("phone", ""),
        "category": body.category,
        "subcategory": body.subcategory,
        "title": body.title,
        "description": body.description,
        "price": body.price,
        "years_of_exp": body.years_of_exp,
        "pincode": body.pincode,
        "area": body.area,
        "address": body.address,
        "payment_method": body.payment_method,
        "photos": body.photos,   # list of S3 keys
        "is_available": True,
        "created_at": datetime.now(timezone.utc),
    }
    result = await db["classifieds"].insert_one(doc)
    doc["id"] = str(result.inserted_id)
    doc.pop("_id", None)
    return {"classified": doc, "message": "Post published successfully"}


# ── Toggle availability ───────────────────────────────────────────────────────

@router.patch("/{classified_id}/toggle")
async def toggle_availability(
    classified_id: str,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    oid = _oid(classified_id)
    doc = await db["classifieds"].find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Not found")
    if str(doc.get("user_id", "")) != current_user["_id"]:
        raise HTTPException(status_code=403, detail="Not your post")

    new_status = not doc.get("is_available", True)
    await db["classifieds"].update_one(
        {"_id": oid}, {"$set": {"is_available": new_status}}
    )
    return {"is_available": new_status}
