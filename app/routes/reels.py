"""
Reels routes — short promo video clips posted by shops / advertisers.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from bson import ObjectId

from ..database import get_db
from ..utils.auth import get_current_user
from ..utils.s3 import generate_video_url_sync, generate_presigned_url_sync

router = APIRouter(prefix="/reels", tags=["reels"])


def _oid(reel_id: str) -> ObjectId:
    try:
        return ObjectId(reel_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid reel id")


def _serialize(doc: dict, user_id: str = "") -> dict:
    doc["id"] = str(doc.pop("_id"))
    liked_by: list = doc.pop("liked_by", [])
    doc["like_count"] = len(liked_by)
    doc["liked_by_me"] = user_id in liked_by

    video_key = doc.pop("video_key", None)
    thumbnail_key = doc.pop("thumbnail_key", None)
    if video_key:
        doc["video_url"] = generate_video_url_sync(video_key)
    if thumbnail_key:
        doc["thumbnail_url"] = generate_presigned_url_sync(thumbnail_key)
    return doc


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


class LikeBody(BaseModel):
    liked: bool


@router.post("/{reel_id}/like")
async def toggle_like(
    reel_id: str,
    body: LikeBody,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    oid = _oid(reel_id)
    user_id: str = current_user["_id"]

    if body.liked:
        await db["reels"].update_one(
            {"_id": oid},
            {"$addToSet": {"liked_by": user_id}},
        )
    else:
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


@router.post("/{reel_id}/view")
async def record_view(
    reel_id: str,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    oid = _oid(reel_id)
    await db["reels"].update_one({"_id": oid}, {"$inc": {"view_count": 1}})
    doc = await db["reels"].find_one({"_id": oid}, {"view_count": 1})
    return {"view_count": doc.get("view_count", 0) if doc else 0}
