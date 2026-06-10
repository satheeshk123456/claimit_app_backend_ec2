"""
Banners endpoint — serves home_banner ads from claimit_db.banners.
"""
from fastapi import APIRouter
from datetime import datetime

from ..database import get_db
from ..utils.helpers import serialize_doc
from ..utils.s3 import generate_presigned_url

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
    db = get_db()
    banners = await db.banners.find({}).sort("created_at", -1).to_list(50)
    active = []
    for b in banners:
        if not _is_active(b):
            continue
        doc = serialize_doc(b)
        if doc.get("image_key") and not doc.get("image_url"):
            doc["image_url"] = await generate_presigned_url(doc["image_key"]) or ""
        active.append(doc)
    return {"success": True, "banners": active}
