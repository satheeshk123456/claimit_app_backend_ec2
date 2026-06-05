"""
Rewards endpoints — loyalty rewards associated with shops.
Seeded via seed.py, retrieved from MongoDB.
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from bson import ObjectId
from datetime import datetime, timezone
from ..database import get_db
from ..utils.auth import get_current_user
from ..utils.helpers import serialize_doc

router = APIRouter(prefix="/rewards", tags=["Rewards"])


@router.get("")
async def get_all_rewards(
    shop_id: Optional[str] = Query(None),
    active_only: bool = Query(True),
    current_user: dict = Depends(get_current_user),
):
    """
    GET /rewards
    Returns all active rewards. Optionally filtered by shop_id.
    """
    db = get_db()
    query: dict = {}

    if active_only:
        query["is_active"] = True
        query["expires_at"] = {"$gt": datetime.now(timezone.utc)}

    if shop_id:
        query["shop_id"] = shop_id

    cursor = db.rewards.find(query).sort("points_required", 1)
    rewards = await cursor.to_list(length=100)

    # Enrich with shop name
    enriched = []
    for r in rewards:
        doc = serialize_doc(r)
        shop = await db.shops.find_one(
            {"_id": ObjectId(r["shop_id"])} if ObjectId.is_valid(r.get("shop_id", "")) else {}
        )
        doc["shop_name"] = shop.get("name", "") if shop else ""
        enriched.append(doc)

    return {"success": True, "rewards": enriched, "total": len(enriched)}


@router.get("/shop/{shop_id}")
async def get_rewards_by_shop(
    shop_id: str,
    current_user: dict = Depends(get_current_user),
):
    """GET /rewards/shop/{shop_id} — rewards for one specific shop."""
    db = get_db()
    now = datetime.now(timezone.utc)
    cursor = db.rewards.find({
        "shop_id": shop_id,
        "is_active": True,
        "expires_at": {"$gt": now},
    }).sort("points_required", 1)
    rewards = await cursor.to_list(length=50)
    return {
        "success": True,
        "rewards": [serialize_doc(r) for r in rewards],
        "total": len(rewards),
    }


@router.get("/{reward_id}")
async def get_reward(
    reward_id: str,
    current_user: dict = Depends(get_current_user),
):
    """GET /rewards/{id} — single reward detail."""
    db = get_db()
    try:
        oid = ObjectId(reward_id)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid reward ID")

    reward = await db.rewards.find_one({"_id": oid})
    if not reward:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reward not found")

    return {"success": True, "reward": serialize_doc(reward)}
