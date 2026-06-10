"""
Shops endpoints — data served from MongoDB.

Route ORDER matters in FastAPI:
  /search  and  /nearby  MUST come before  /{shop_id}
"""
import math
from typing import Optional, List
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status, Query
from bson import ObjectId
from ..database import get_db
from ..utils.auth import get_current_user
from ..utils.s3 import generate_presigned_url
from ..utils.helpers import serialize_doc
from ..models.shop import ReviewCreate

router = APIRouter(prefix="/shops", tags=["Shops"])


def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2))
         * math.sin(dlng / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


async def _doc_to_response(doc: dict, distance_km: Optional[float] = None) -> dict:
    result = serialize_doc(doc)
    if distance_km is not None:
        result["distance"] = f"{distance_km:.1f} km"

    bucket = "claimit-image-bucket"
    region = "eu-north-1"

    if not result.get("image_url") and result.get("image_s3_key"):
        result["image_url"] = f"https://{bucket}.s3.{region}.amazonaws.com/{result['image_s3_key']}"

    if not result.get("image_urls") and result.get("image_s3_keys"):
        result["image_urls"] = [
            f"https://{bucket}.s3.{region}.amazonaws.com/{k}" for k in result["image_s3_keys"]
        ]

    shop_type = result.get("shop_type", "")
    if shop_type == "reward":
        result.setdefault("has_rewards", True)
        result.setdefault("has_redeem",  False)
    elif shop_type == "redeem":
        result.setdefault("has_rewards", False)
        result.setdefault("has_redeem",  True)
    else:
        result.setdefault("has_rewards", True)
        result.setdefault("has_redeem",  True)

    return result


@router.get("")
async def get_shops(
    category_id: Optional[int] = Query(None),
    q: Optional[str] = Query(None),
    area: Optional[str] = Query(None),
    exclude_id: Optional[str] = Query(None),
    has_rewards: Optional[bool] = Query(None),
    has_redeem: Optional[bool] = Query(None),
    limit: int = Query(200, ge=1, le=500),
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    query: dict = {}

    if category_id is not None:
        query["category_ids"] = category_id
    if has_rewards is not None:
        query["has_rewards"] = has_rewards
    if has_redeem is not None:
        query["has_redeem"] = has_redeem

    cursor = db.shops.find(query).sort("added_days_ago", 1)
    shops: List[dict] = await cursor.to_list(length=500)

    if q:
        q_lower = q.lower()
        shops = [
            s for s in shops
            if q_lower in s.get("name", "").lower()
            or q_lower in s.get("location", "").lower()
        ]

    if area:
        area_lower = area.lower()
        shops = [
            s for s in shops
            if area_lower in s.get("location", "").lower()
        ]

    if exclude_id:
        shops = [s for s in shops if str(s.get("_id", "")) != exclude_id]

    shops = shops[:limit]

    shops_out = []
    for s in shops:
        shops_out.append(await _doc_to_response(s))
    return {
        "success": True,
        "shops": shops_out,
        "total": len(shops_out),
    }


@router.get("/search")
async def search_shops(
    q: str = Query(..., min_length=1),
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    q_lower = q.lower()
    cursor = db.shops.find({})
    all_shops = await cursor.to_list(length=500)
    results = [
        s for s in all_shops
        if q_lower in s.get("name", "").lower()
        or q_lower in s.get("location", "").lower()
    ]
    results_out = []
    for s in results:
        results_out.append(await _doc_to_response(s))
    return {
        "success": True,
        "shops": results_out,
        "total": len(results_out),
    }


@router.get("/nearby")
async def get_nearby_shops(
    lat: float = Query(...),
    lng: float = Query(...),
    radius_km: float = Query(4.0, ge=0.1, le=50.0),
    exclude_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    all_shops: List[dict] = await db.shops.find({}).to_list(length=500)

    nearby = []
    for shop in all_shops:
        shop_lat = shop.get("lat")
        shop_lng = shop.get("lng")
        if shop_lat is not None and shop_lng is not None:
            dist = _haversine(lat, lng, float(shop_lat), float(shop_lng))
            if dist <= radius_km:
                doc = await _doc_to_response(shop, distance_km=dist)
                nearby.append((dist, doc))

    if exclude_id:
        nearby = [(d, doc) for d, doc in nearby if doc.get("id") != exclude_id]

    nearby.sort(key=lambda x: x[0])
    shops_out = [doc for _, doc in nearby]

    return {
        "success": True,
        "shops": shops_out,
        "total": len(shops_out),
        "radius_km": radius_km,
        "user_lat": lat,
        "user_lng": lng,
    }


@router.get("/{shop_id}")
async def get_shop(
    shop_id: str,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    try:
        oid = ObjectId(shop_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid shop ID")

    shop = await db.shops.find_one({"_id": oid})
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")

    review_count = await db.shop_reviews.count_documents({"shop_id": shop_id})
    shop["review_count"] = review_count

    return {"success": True, "shop": await _doc_to_response(shop)}


@router.get("/{shop_id}/image")
async def get_shop_image(
    shop_id: str,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    try:
        oid = ObjectId(shop_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid shop ID")

    shop = await db.shops.find_one(
        {"_id": oid},
        {"image_s3_key": 1, "image_s3_keys": 1,
         "image_url": 1,    "image_urls": 1,
         "image_data": 1,   "image_name": 1},
    )
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")

    s3_key  = shop.get("image_s3_key")
    s3_keys = shop.get("image_s3_keys") or []

    image_url = (
        await generate_presigned_url(s3_key)
        if s3_key
        else shop.get("image_url", "")
    )
    image_urls = []
    for key in s3_keys:
        url = await generate_presigned_url(key)
        if url:
            image_urls.append(url)
    if not image_urls:
        image_urls = shop.get("image_urls", [])

    return {
        "success":      True,
        "shop_id":      shop_id,
        "image_url":    image_url or "",
        "image_urls":   image_urls,
        "image_s3_key": s3_key,
        "image_name":   shop.get("image_name", ""),
        "image_data":   shop.get("image_data", ""),
    }


@router.get("/{shop_id}/reviews")
async def get_shop_reviews(
    shop_id: str,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    try:
        oid = ObjectId(shop_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid shop ID")

    shop = await db.shops.find_one({"_id": oid}, {"_id": 1})
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")

    cursor = db.shop_reviews.find({"shop_id": shop_id}).sort("created_at", -1)
    reviews = await cursor.to_list(length=100)

    total = len(reviews)
    avg_rating = (
        round(sum(r.get("rating", 0) for r in reviews) / total, 1)
        if total > 0 else 0.0
    )

    return {
        "success": True,
        "reviews": [serialize_doc(r) for r in reviews],
        "avg_rating": avg_rating,
        "total": total,
    }


@router.post("/{shop_id}/reviews", status_code=201)
async def submit_review(
    shop_id: str,
    data: ReviewCreate,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    try:
        oid = ObjectId(shop_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid shop ID")

    shop = await db.shops.find_one({"_id": oid}, {"_id": 1})
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")

    user_id = str(current_user.get("_id") or current_user.get("id"))
    user_name = current_user.get("full_name") or current_user.get("name") or "User"
    user_avatar = current_user.get("avatar_url")

    review_doc = {
        "shop_id": shop_id,
        "user_id": user_id,
        "user_name": user_name,
        "user_avatar": user_avatar,
        "rating": round(float(data.rating), 1),
        "comment": data.comment.strip(),
        "created_at": datetime.now(timezone.utc),
    }

    await db.shop_reviews.update_one(
        {"shop_id": shop_id, "user_id": user_id},
        {"$set": review_doc},
        upsert=True,
    )

    cursor = db.shop_reviews.find({"shop_id": shop_id})
    all_reviews = await cursor.to_list(length=500)
    total = len(all_reviews)
    new_avg = round(sum(r.get("rating", 0) for r in all_reviews) / total, 1) if total else 0
    await db.shops.update_one(
        {"_id": oid},
        {"$set": {"rating": new_avg, "review_count": total}},
    )

    return {
        "success": True,
        "message": "Review submitted successfully",
        "review": serialize_doc(review_doc),
        "new_avg_rating": new_avg,
    }
