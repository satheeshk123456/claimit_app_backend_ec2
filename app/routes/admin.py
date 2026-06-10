"""
Admin / maintenance endpoints.
"""
import os
from typing import Dict, Tuple, Optional
from fastapi import APIRouter, Depends, HTTPException, Header

from ..database import get_db

router = APIRouter(prefix="/admin", tags=["Admin"])

_ADMIN_KEY = os.getenv("ADMIN_SECRET", "claimit-admin-2024")


def _require_admin(x_admin_key: Optional[str] = Header(None)):
    if x_admin_key != _ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Forbidden: invalid admin key")


_LOCATION_GPS: Dict[str, Tuple[float, float]] = {
    "padi":            (13.1197, 80.2183),
    "anna nagar":      (13.0839, 80.2101),
    "t. nagar":        (13.0350, 80.2337),
    "t.nagar":         (13.0350, 80.2337),
    "tnagar":          (13.0350, 80.2337),
    "porur":           (13.0359, 80.1577),
    "velachery":       (12.9790, 80.2181),
    "nungambakkam":    (13.0569, 80.2425),
    "adyar":           (13.0012, 80.2565),
    "koramangala":     (12.9352, 77.6245),
    "egmore":          (13.0782, 80.2603),
    "besant nagar":    (12.9997, 80.2705),
    "besantnagar":     (12.9997, 80.2705),
    "mylapore":        (13.0368, 80.2676),
    "tambaram":        (12.9230, 80.1130),
    "perambur":        (13.1154, 80.2362),
    "vadapalani":      (13.0530, 80.2120),
    "chromepet":       (12.9516, 80.1462),
    "sholinganallur":  (12.9010, 80.2279),
    "omr":             (12.9010, 80.2279),
    "ambattur":        (13.0983, 80.1698),
    "kodambakkam":     (13.0530, 80.2240),
    "guindy":          (13.0067, 80.2206),
    "chennai":         (13.0827, 80.2707),
}


def _coords_for_location(location: str) -> Optional[Tuple[float, float]]:
    loc_lower = location.lower()
    for keyword in sorted(_LOCATION_GPS, key=len, reverse=True):
        if keyword in loc_lower:
            return _LOCATION_GPS[keyword]
    return None


@router.post("/migrate-shops-gps")
async def migrate_shops_gps(x_admin_key: Optional[str] = Header(None)):
    _require_admin(x_admin_key)

    db = get_db()
    all_shops = await db.shops.find({}, {"_id": 1, "name": 1, "location": 1, "lat": 1, "lng": 1}).to_list(length=1000)

    updated = []
    skipped = []
    unresolved = []

    for shop in all_shops:
        shop_id = shop["_id"]
        name = shop.get("name", str(shop_id))

        if shop.get("lat") is not None and shop.get("lng") is not None:
            skipped.append(name)
            continue

        location = shop.get("location", "")
        coords = _coords_for_location(location)

        if coords:
            lat, lng = coords
            await db.shops.update_one(
                {"_id": shop_id},
                {"$set": {"lat": lat, "lng": lng}},
            )
            updated.append({"name": name, "location": location, "lat": lat, "lng": lng})
        else:
            unresolved.append({"name": name, "location": location})

    return {
        "success": True,
        "summary": {
            "total": len(all_shops),
            "updated": len(updated),
            "already_had_gps": len(skipped),
            "unresolved": len(unresolved),
        },
        "updated_shops": updated,
        "unresolved_shops": unresolved,
    }


@router.post("/migrate-shops-gps/force")
async def migrate_shops_gps_force(x_admin_key: Optional[str] = Header(None)):
    _require_admin(x_admin_key)

    db = get_db()
    all_shops = await db.shops.find({}, {"_id": 1, "name": 1, "location": 1}).to_list(length=1000)

    updated = []
    unresolved = []

    for shop in all_shops:
        shop_id = shop["_id"]
        name = shop.get("name", str(shop_id))
        location = shop.get("location", "")
        coords = _coords_for_location(location)

        if coords:
            lat, lng = coords
            await db.shops.update_one(
                {"_id": shop_id},
                {"$set": {"lat": lat, "lng": lng}},
            )
            updated.append({"name": name, "location": location, "lat": lat, "lng": lng})
        else:
            unresolved.append({"name": name, "location": location})

    return {
        "success": True,
        "summary": {
            "total": len(all_shops),
            "updated": len(updated),
            "unresolved": len(unresolved),
        },
        "updated_shops": updated,
        "unresolved_shops": unresolved,
    }


@router.get("/app-config")
async def get_app_config(_: None = Depends(_require_admin)):
    db  = get_db()
    cfg = await db.app_config.find_one({"key": "new_user_bonus"})
    return {
        "reward_points": int(cfg.get("reward_points", 1000)) if cfg else 1000,
        "cashback":      float(cfg.get("cashback", 10.0))   if cfg else 10.0,
    }


from pydantic import BaseModel as _BM

class AppConfigUpdate(_BM):
    reward_points: int
    cashback:      float


@router.put("/app-config")
async def update_app_config(body: AppConfigUpdate, _: None = Depends(_require_admin)):
    if body.reward_points < 0:
        raise HTTPException(status_code=400, detail="reward_points must be >= 0")
    if body.cashback < 0:
        raise HTTPException(status_code=400, detail="cashback must be >= 0")
    db = get_db()
    await db.app_config.update_one(
        {"key": "new_user_bonus"},
        {"$set": {
            "key":           "new_user_bonus",
            "reward_points": body.reward_points,
            "cashback":      body.cashback,
            "updated_at":    __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        }},
        upsert=True,
    )
    return {"ok": True, "reward_points": body.reward_points, "cashback": body.cashback}
