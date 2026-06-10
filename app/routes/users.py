from fastapi import APIRouter, HTTPException, status, Depends, UploadFile, File  # noqa: F401
from datetime import datetime
from bson import ObjectId
import os
import aiofiles
from ..database import get_db
from ..utils.auth import get_current_user
from ..utils.s3 import upload_bytes as _s3_upload_bytes, generate_presigned_url as _s3_presign
from ..utils.helpers import serialize_doc
from ..models.user import UserUpdate, LocationUpdate
from ..config import get_settings

router = APIRouter(prefix="/users", tags=["Users"])
settings = get_settings()


@router.get("/profile")
async def get_profile(current_user: dict = Depends(get_current_user)):
    profile = serialize_doc(current_user)
    avatar_s3_key = current_user.get("avatar_s3_key")
    if avatar_s3_key:
        profile["avatar_url"] = await _s3_presign(avatar_s3_key)
    return profile


@router.put("/profile/update")
async def update_profile(
    update_data: UserUpdate,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    user_id = current_user.get("_id") or current_user.get("id")

    update_dict = {k: v for k, v in update_data.model_dump().items() if v is not None}
    update_dict["updated_at"] = datetime.utcnow()

    if not update_dict:
        raise HTTPException(status_code=400, detail="No fields to update")

    if "phone" in update_dict:
        existing = await db.users.find_one({"phone": update_dict["phone"]})
        if existing and str(existing.get("_id")) != str(user_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This mobile number is already linked to another account.",
            )

    if "email" in update_dict:
        existing = await db.users.find_one({"email": update_dict["email"]})
        if existing and str(existing.get("_id")) != str(user_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This email address is already linked to another account.",
            )

    await db.users.update_one(
        {"_id": ObjectId(str(user_id))},
        {"$set": update_dict},
    )

    updated_user = await db.users.find_one({"_id": ObjectId(str(user_id))})
    return serialize_doc(updated_user)


@router.post("/location")
async def update_location(
    data: LocationUpdate,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    user_id = current_user.get("_id") or current_user.get("id")

    await db.users.update_one(
        {"_id": ObjectId(str(user_id))},
        {"$set": {"location": data.location, "updated_at": datetime.utcnow()}},
    )

    updated_user = await db.users.find_one({"_id": ObjectId(str(user_id))})
    return {"success": True, "location": data.location, "user": serialize_doc(updated_user)}


@router.post("/avatar")
async def upload_avatar(
    avatar: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user.get("_id") or current_user.get("id")

    allowed_types = ["image/jpeg", "image/png", "image/jpg"]
    if avatar.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="Only JPEG/PNG images allowed")

    content = await avatar.read()
    if len(content) > settings.max_file_size_mb * 1024 * 1024:
        raise HTTPException(
            status_code=400,
            detail=f"File size exceeds {settings.max_file_size_mb}MB limit",
        )

    s3_key = await _s3_upload_bytes(content, folder="avatars",
                 filename=avatar.filename,
                 content_type=avatar.content_type or "image/jpeg")

    avatar_url = await _s3_presign(s3_key) or ""

    db = get_db()
    await db.users.update_one(
        {"_id": ObjectId(str(user_id))},
        {"$set": {"avatar_s3_key": s3_key, "updated_at": datetime.utcnow()}},
    )

    return {"avatar_url": avatar_url, "success": True}


@router.post("/favourites/{shop_id}")
async def toggle_favourite(
    shop_id: str,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    user_id = str(current_user.get("_id") or current_user.get("id"))
    liked_shop_ids = current_user.get("liked_shop_ids", [])

    if shop_id in liked_shop_ids:
        await db.users.update_one(
            {"_id": ObjectId(user_id)},
            {"$pull": {"liked_shop_ids": shop_id}},
        )
        return {"success": True, "liked": False, "shop_id": shop_id}
    else:
        await db.users.update_one(
            {"_id": ObjectId(user_id)},
            {"$addToSet": {"liked_shop_ids": shop_id}},
        )
        return {"success": True, "liked": True, "shop_id": shop_id}


@router.get("/favourites")
async def get_favourites(
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    liked_shop_ids: list = current_user.get("liked_shop_ids", [])

    shops = []
    for shop_id in liked_shop_ids:
        try:
            shop = await db.shops.find_one({"_id": ObjectId(shop_id)})
            if shop:
                shops.append(serialize_doc(shop))
        except Exception:
            pass

    return {"success": True, "shops": shops, "total": len(shops)}


@router.get("/liked-ids")
async def get_liked_ids(
    current_user: dict = Depends(get_current_user),
):
    liked_shop_ids: list = current_user.get("liked_shop_ids", [])
    return {"success": True, "liked_shop_ids": liked_shop_ids}


@router.post("/deal-favourites/{deal_id}")
async def toggle_deal_favourite(
    deal_id: str,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    user_id = str(current_user.get("_id") or current_user.get("id"))
    liked_deal_ids = current_user.get("liked_deal_ids", [])

    if deal_id in liked_deal_ids:
        await db.users.update_one(
            {"_id": ObjectId(user_id)},
            {"$pull": {"liked_deal_ids": deal_id}},
        )
        return {"success": True, "liked": False, "deal_id": deal_id}
    else:
        await db.users.update_one(
            {"_id": ObjectId(user_id)},
            {"$addToSet": {"liked_deal_ids": deal_id}},
        )
        return {"success": True, "liked": True, "deal_id": deal_id}


@router.get("/liked-deal-ids")
async def get_liked_deal_ids(
    current_user: dict = Depends(get_current_user),
):
    liked_deal_ids: list = current_user.get("liked_deal_ids", [])
    return {"success": True, "liked_deal_ids": liked_deal_ids}


@router.get("/deal-favourites")
async def get_deal_favourites(
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    liked_deal_ids: list = current_user.get("liked_deal_ids", [])

    deals = []
    for deal_id in liked_deal_ids:
        try:
            deal = await db.deals.find_one({"_id": ObjectId(deal_id)})
            if deal:
                deals.append(serialize_doc(deal))
        except Exception:
            pass

    return {"success": True, "deals": deals, "total": len(deals)}
