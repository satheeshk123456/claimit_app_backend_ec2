"""
Shops endpoints — data served from MongoDB.
Images stored as base64 strings returned in JSON responses.

Route ORDER matters in FastAPI:
  /search  and  /nearby  MUST come before  /{shop_id}
  otherwise "nearby" / "search" are matched as shop_id values.
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


# ── Haversine distance (km) ────────────────────────────────────────────────────
def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Return distance in km between two GPS coordinates."""
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

    # ── Generate presigned URLs from S3 keys (private bucket) ────────────────
    s3_key  = result.get("image_s3_key")
    s3_keys = result.get("image_s3_keys") or []

    if s3_key:
        result["image_url"] = await generate_presigned_url(s3_key) or ""

    if s3_keys:
        presigned = []
        for k in s3_keys:
            url = await generate_presigned_url(k)
            if url:
                presigned.append(url)
        if presigned:
            result["image_urls"] = presigned

    # ── Derive has_rewards / has_redeem from shop_type ────────────────────────
    shop_type = result.get("shop_type", "")
    if shop_type == "reward":
        result.setdefault("has_rewards", True)
        result.setdefault("has_redeem",  False)
    elif shop_type == "redeem":
        result.setdefault("has_rewards", False)
        result.setdefault("has_redeem",  True)
    elif shop_type == "both":
        result.setdefault("has_rewards", True)
        result.setdefault("has_redeem",  True)
    else:
        result.setdefault("has_rewards", True)
        result.setdefault("has_redeem",  True)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# GET /shops  — list / filter / search
# ─────────────────────────────────────────────────────────────────────────────
@router.get("")
async def get_shops(
    category_id: Optional[int] = Query(None),
    q: Optional[str] = Query(None),
    area: Optional[str] = Query(None, description="Filter by area name in location field"),
    exclude_id: Optional[str] = Query(None, description="Exclude this shop ID"),
    has_rewards: Optional[bool] = Query(None),
    has_redeem: Optional[bool] = Query(None),
    limit: int = Query(200, ge=1, le=500),
    current_user: dict = Depends(get_current_user),
):
    """
    GET /shops
    Supports: category_id, q (search), area, exclude_id, has_rewards, has_redeem, limit
    """
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

    # Search filter (name / location)
    if q:
        q_lower = q.lower()
        shops = [
            s for s in shops
            if q_lower in s.get("name", "").lower()
            or q_lower in s.get("location", "").lower()
        ]

    # Area filter — match location string, e.g. "Padi" matches "Padi, Chennai"
    if area:
        area_lower = area.lower()
        shops = [
            s for s in shops
            if area_lower in s.get("location", "").lower()
        ]

    # Exclude a specific shop (used for "stores nearby" on the detail page)
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


# ─────────────────────────────────────────────────────────────────────────────
# GET /shops/search  — must be before /{shop_id}
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/search")
async def search_shops(
    q: str = Query(..., min_length=1),
    current_user: dict = Depends(get_current_user),
):
    """GET /shops/search?q=term"""
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


# ─────────────────────────────────────────────────────────────────────────────
# GET /shops/nearby  — must be before /{shop_id}
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/nearby")
async def get_nearby_shops(
    lat: float = Query(..., description="User latitude"),
    lng: float = Query(..., description="User longitude"),
    radius_km: float = Query(4.0, ge=0.1, le=50.0, description="Search radius in km"),
    exclude_id: Optional[str] = Query(None, description="Shop ID to exclude from results"),
    current_user: dict = Depends(get_current_user),
):
    """
    GET /shops/nearby?lat=X&lng=Y&radius_km=4
    Returns all shops within `radius_km` of the given GPS coordinates.
    Each shop gets a `distance` field e.g. "2.3 km".
    Shops without stored GPS coordinates are included only if their
    location string can be matched (they get distance=None).
    """
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
        # Shops without GPS: skip from nearby (they have no coordinates to measure)

    # Exclude specific shop (e.g. the current shop when showing "stores nearby")
    if exclude_id:
        nearby = [(d, doc) for d, doc in nearby if doc.get("id") != exclude_id]

    # Sort by distance ascending
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


# ─────────────────────────────────────────────────────────────────────────────
# GET /shops/{shop_id}  — single shop
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/{shop_id}")
async def get_shop(
    shop_id: str,
    current_user: dict = Depends(get_current_user),
):
    """GET /shops/{id}"""
    db = get_db()
    try:
        oid = ObjectId(shop_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid shop ID")

    shop = await db.shops.find_one({"_id": oid})
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")

    # Attach review count
    review_count = await db.shop_reviews.count_documents({"shop_id": shop_id})
    shop["review_count"] = review_count

    return {"success": True, "shop": await _doc_to_response(shop)}


# ─────────────────────────────────────────────────────────────────────────────
# GET /shops/{shop_id}/image
# ─────────────────────────────────────────────────────────────────────────────
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

    # Generate presigned URLs from S3 keys (private bucket); fall back to
    # whatever is stored in image_url for legacy/base64 records.
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
        # legacy fields kept for backward compat
        "image_name":   shop.get("image_name", ""),
        "image_data":   shop.get("image_data", ""),
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /shops/{shop_id}/reviews
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/{shop_id}/reviews")
async def get_shop_reviews(
    shop_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    GET /shops/{id}/reviews
    Returns: { reviews: [...], avg_rating, total }
    """
    db = get_db()
    # Validate shop exists
    try:
        oid = ObjectId(shop_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid shop ID")

    shop = await db.shops.find_one({"_id": oid}, {"_id": 1})
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")

    cursor = db.shop_reviews.find({"shop_id": shop_id}).sort("created_at", -1)
    reviews = await cursor.to_list(length=100)

    # Compute average rating
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


# ─────────────────────────────────────────────────────────────────────────────
# POST /shops/{shop_id}/reviews
# ─────────────────────────────────────────────────────────────────────────────
@router.post("/{shop_id}/reviews", status_code=201)
async def submit_review(
    shop_id: str,
    data: ReviewCreate,
    current_user: dict = Depends(get_current_user),
):
    """
    POST /shops/{id}/reviews
    Body: { rating: float, comment: str }
    One review per user per shop (upsert).
    """
    db = get_db()
    try:
        oid = ObjectId(shop_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid shop ID")

    shop = await db.shops.find_one({"_id": oid}, {"_id": 1})
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")

    user_id = str(current_user.get("_id") or current_user.get("id"))
    user_name = curre