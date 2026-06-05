"""
Banners endpoint — serves home_banner ads from claimit_db.banners.
Written there by the web portal whenever an advertiser creates a
home_banner ad via POST /advertiser/ads/create.

GET /banners   → { banners: [...] }   (no auth required)
"""
from fastapi import APIRouter
from datetime import datetime

from ..database import get_db
from ..utils.helpers import serialize_doc

router = APIRouter(prefix="/banners", tags=["Banners"])


def _is_active(banner: dict) -> bool:
    status = banner.get("status", "active")
    if status == "active":
        return True
    if status == "scheduled":
        pub = banner.get("publish_date", "")
        try:
            return datetime.utcnow() >= datetime.strptime(pub, "%d/%m/%Y")
        except Exception:
            return True
    return False


@router.get("")
async def get_banners():
    """Return all active home-banner ads for the Flutter home screen."""
    db = get_db()
    banners = await db.banners.find({}).sort("created_at", -1).to_list(50)
    active = [serialize_doc(b) for b in banners if _is_active(b)]
    return {"success": True, "banners": active}
