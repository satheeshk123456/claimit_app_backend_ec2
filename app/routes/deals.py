"""
Deals endpoints — data served from MongoDB (seeded via seed.py).
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from bson import ObjectId
from ..database import get_db
from ..utils.auth import get_current_user
from ..utils.helpers import serialize_doc

router = APIRouter(prefix="/deals", tags=["Deals"])


@router.get("/nearby")
async def get_nearby_deals(
    location: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    query: dict = {"deal_group": "nearby"}
    if category and category.lower() not in ("all", ""):
        query["category"] = {"$regex": category, "$options": "i"}

    cursor = db.deals.find(query).sort("name", 1)
    deals = await cursor.to_list(length=100)
    return {"success": True, "deals": [serialize_doc(d) for d in deals], "total": len(deals)}


@router.get("/brand")
async def get_brand_deals(
    category: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    query: dict = {"deal_group": "brand"}
    if category and category.lower() not in ("all", ""):
        query["category"] = {"$regex": category, "$options": "i"}

    cursor = db.deals.find(query).sort("name", 1)
    deals = await cursor.to_list(length=100)
    return {"success": True, "deals": [serialize_doc(d) for d in deals], "total": len(deals)}


@router.get("/{deal_id}")
async def get_deal_detail(
    deal_id: str,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()

    deal = None
    if ObjectId.is_valid(deal_id):
        deal = await db.deals.find_one({"_id": ObjectId(deal_id)})
    if not deal:
        deal = await db.deals.find_one({"id": deal_id})

    if not deal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deal not found")

    return {"success": True, "deal": serialize_doc(deal)}
