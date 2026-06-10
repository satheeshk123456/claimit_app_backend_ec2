from fastapi import APIRouter, Depends
from bson import ObjectId
from datetime import datetime
from pydantic import BaseModel
from typing import Optional
from ..database import get_db
from ..utils.auth import get_current_user
from ..utils.helpers import serialize_doc

router = APIRouter(prefix="/notifications", tags=["Notifications"])


class FcmTokenRequest(BaseModel):
    token: str
    platform: Optional[str] = None


@router.post("/fcm-token")
async def register_fcm_token(
    request: FcmTokenRequest,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    user_id = str(current_user.get("_id") or current_user.get("id"))
    token = request.token.strip()

    if not token:
        return {"success": False, "detail": "Empty token"}

    await db.fcm_tokens.update_one(
        {"token": token},
        {
            "$set": {
                "user_id": user_id,
                "platform": request.platform,
                "updated_at": datetime.utcnow(),
            },
            "$setOnInsert": {"created_at": datetime.utcnow()},
        },
        upsert=True,
    )
    return {"success": True}


@router.delete("/fcm-token")
async def unregister_fcm_token(
    request: FcmTokenRequest,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    user_id = str(current_user.get("_id") or current_user.get("id"))
    token = request.token.strip()

    await db.fcm_tokens.delete_one({"token": token, "user_id": user_id})
    return {"success": True}


@router.get("")
async def get_notifications(
    page: int = 1,
    page_size: int = 20,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    user_id = current_user.get("_id") or current_user.get("id")

    skip = (page - 1) * page_size
    cursor = db.notifications.find({"user_id": user_id}).sort(
        "created_at", -1
    ).skip(skip).limit(page_size)

    notifications = await cursor.to_list(length=page_size)
    return [serialize_doc(n) for n in notifications]


@router.get("/unread-count")
async def get_unread_count(current_user: dict = Depends(get_current_user)):
    db = get_db()
    user_id = current_user.get("_id") or current_user.get("id")

    count = await db.notifications.count_documents(
        {"user_id": user_id, "is_read": False}
    )
    return {"count": count}


@router.patch("/read-all")
async def mark_all_read(current_user: dict = Depends(get_current_user)):
    db = get_db()
    user_id = current_user.get("_id") or current_user.get("id")

    await db.notifications.update_many(
        {"user_id": user_id, "is_read": False},
        {"$set": {"is_read": True}},
    )
    return {"success": True}


@router.patch("/{notification_id}/read")
async def mark_as_read(
    notification_id: str,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    user_id = current_user.get("_id") or current_user.get("id")

    await db.notifications.update_one(
        {"_id": ObjectId(notification_id), "user_id": user_id},
        {"$set": {"is_read": True}},
    )
    return {"success": True}
