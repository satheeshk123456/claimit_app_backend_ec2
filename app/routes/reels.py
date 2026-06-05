"""
Reels routes — short promo video clips posted by shops.

Like tracking
─────────────
Each reel stores a `liked_by` list of user-ID strings.
  • $addToSet   → prevents double-liking at DB level
  • $pull       → unlike
  • like_count  → derived as len(liked_by) — always accurate
  • liked_by_me → returned per-request based on the authenticated user

Endpoints
─────────
GET  /reels              → list reels (liked_by_me per user)
GET  /reels/{id}         → single reel
POST /reels/{id}/like    → toggle like   {"liked": true|false}
POST /reels/{id}/view    → record a view (increments view_count)
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from bson import ObjectId

from ..database import get_db
from ..utils.auth import get_current_user

router = APIRouter(prefix="/reels", tags=["reels"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _oid(reel_id: str) -> ObjectId:
    try:
        return ObjectId(reel_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid reel id")


def _serialize(doc: dict, user_id: str = "") -> dict:
    doc["id"] = str(doc.pop("_id"))
    liked_by: list = doc.pop("liked_by", [])
    # Derive like_count from the list so it's always in sync
    doc["like_count"] = len(liked_by)
    doc["liked_by_me"] = user_id in liked_by
    return doc


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("")
async def list_reels(
    limit: int = Query(default=20, le=100),
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    cursor = db["reels"].find({}).limit(limit)
    docs = await cursor.to_list(length=limit)
    user_id: str = current_user["_id"]
    return {"reels": [_serialize(d, user_id) for d in docs]}


# ── Single ────────────────────────────────────────────────────────────────────

@router.get("/{reel_id}")
async def get_reel(
    reel_id: str,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    doc = await db["reels"].find_one({"_id": _oid(reel_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Reel not found")
    return {"reel": _serialize(doc, current_user["_id"])}


# ── Like toggle ───────────────────────────────────────────────────────────────

class LikeBody(BaseModel):
    liked: bool   # true = user is liking; false = user is unliking


@router.post("/{reel_id}/like")
async def toggle_like(
    reel_id: str,
    body: LikeBody,
    current_user: dict = Depends(get_current_user),
):
    """
    One like per user — enforced by $addToSet (no duplicates).
    Returns updated like_count and liked_by_me flag.
    """
    db = get_db()
    oid = _oid(reel_id)
    user_id: str = current_user["_id"]

    if body.liked:
        # $addToSet silently ignores if user_id already present
        await db["reels"].update_one(
            {"_id": oid},
            {"$addToSet": {"liked_by": user_id}},
        )
    else:
        # $pull removes the user_id if it exists
        await db["reels"].update_one(
            {"_id": oid},
            {"$pull": {"liked_by": user_id}},
        )

    doc = await db["reels"].find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Reel not found")

    liked_by: list = doc.get("liked_by", [])
    return {
        "like_count": len(liked_by),
        "liked_by_me": user_id in liked_by,
    }


# ── View increment ────────────────────────────────────────────────────────────

@router.post("/{reel_id}/view")
async def record_view(
    reel_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Increment view_count by 1. Called when a reel becomes the active page."""
    db = get_db()
    result = await db["reels"].find_one_and_update(
        {"_id": _oid(reel_id)},
        {"$inc": {"view_count": 1}},
        return_document=True,
    )
    if not result:
        raise HTTPException(status_code=404, detail="Reel not found")
    return {"view_count": result.get("view_count", 0)}
