"""
Admin / maintenance endpoints.
These routes are NOT auth-protected in the usual sense — use a secret key header
so they can't be triggered by regular app users.

Usage:
  POST /admin/migrate-shops-gps
  Header: X-Admin-Key: <ADMIN_SECRET>

Patches all existing shop documents in MongoDB that are missing lat/lng
with coordinates derived from their location string.
"""
import os
from typing import Dict, Tuple, Optional
from fastapi import APIRouter, HTTPException, Header

from ..database import get_db

router = APIRouter(prefix="/admin", tags=["Admin"])

# ── Admin key check ────────────────────────────────────────────────────────────
_ADMIN_KEY = os.getenv("ADMIN_SECRET", "claimit-admin-2024")


def _require_admin(x_admin_key: Optional[str] = Header(None)):
    if x_admin_key != _ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Forbidden: invalid admin key")


# ── Location → GPS lookup table ─────────────────────────────────────────────
# Keywords are matched case-insensitively against the shop's `location` field.
# More-specific strings (longer) are matched first.
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
    "chennai":         (13.0827, 80.2707),  # city-level fallback
}


def _coords_for_location(location: str) -> Optional[Tuple[float, float]]:
    """Return (lat, lng) for the first keyword that matches `location`."""
    loc_lower = location.lower()
    # Try longest keys first so "besant nagar" beats "nagar"
    for keyword in sorted(_LOCATION_GPS, key=len, reverse=True):
        if keyword in loc_lower:
            return _LOCATION_GPS[keyword]
    return None


# ─────────────────────────────────────────────────────────────────────────────
# POST /admin/migrate-shops-gps
# ─────────────────────────────────────────────────────────────────────────────
@router.post("/migrate-shops-gps")
async def migrate_shops_gps(x_admin_key: Optional[str] = Header(None)):
    """
    Iterate every shop in MongoDB and fill in `lat` / `lng` where missing.
    Returns a summary: how many were updated, skipped (already had coords),
    and unresolved (location string not in the lookup table).
    """
    _require_admin(x_admin_key)

    db = get_db()
    all_shops = await db.shops.find({}, {"_id": 1, "name": 1, "location": 1, "lat": 1, "lng": 1}).to_list(length=1000)

    updated = []
    skipped = []
    unresolved = []

    for shop in all_shops:
        shop_id = shop["_id"]
        name = shop.get("name", str(shop_id))

        # Already has coordinates — skip
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


# ─────────────────────────────────────────────────────────────────────────────
# POST /admin/migrate-shops-gps/force
# Overwrites ALL shops (even those that already have coords) — useful if
# you want to correct bad coordinates.
# ─────────────────────────────────────────────────────────────────────────────
@router.post("/migrate-shops-gps/force")
async def migrate_shops_gps_force(x_admin_key: Optional[str] = Header(None)):
    """Force-update lat/lng on every shop, replacing any existing values."""
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
