"""
Redeem endpoints — users redeem rewards from shops.
"""
import random
import string
from fastapi import APIRouter, Depends, HTTPException, status
from bson import ObjectId
from datetime import datetime, timezone
from ..database import get_db
from ..utils.auth import get_current_user
from ..utils.helpers import serialize_doc
from ..models.shop import RedeemCreate, EligibilityRequest

router = APIRouter(prefix="/redeem", tags=["Redeem"])


def _generate_coupon(length: int = 8) -> str:
    """Generate a random alphanumeric coupon code."""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))


# ─────────────────────────────────────────────────────────────────────────────
# POST /redeem/eligibility
# Must be defined BEFORE POST /redeem (empty path) so FastAPI doesn't confuse
# the literal "eligibility" path segment with the body of POST /redeem.
# ─────────────────────────────────────────────────────────────────────────────
@router.post("/eligibility")
async def check_eligibility(
    data: EligibilityRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    POST /redeem/eligibility
    Body: { shop_id, lat?, lng? }
    Returns: { eligible: bool, discount: int, message: str }

    Eligibility rules:
    1. Shop must exist and have has_redeem=True.
    2. User must have at least 1 reward point (or any existing bill history).
       (Simplified: always eligible if shop is valid; extend logic as needed.)
    """
    db = get_db()

    # Validate shop
    try:
        shop_oid = ObjectId(data.shop_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid shop ID")

    shop = await db.shops.find_one({"_id": shop_oid})
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")

    has_redeem = shop.get("has_redeem", True)
    discount = shop.get("discount", 0)

    if not has_redeem:
        return {
            "eligible": False,
            "discount": 0,
            "message": "This shop does not participate in the Redeem programme.",
        }

    # Check user has any bill history (reward points)
    user_id = str(current_user.get("_id") or current_user.get("id"))
    bill_count = await db.bill_rewards.count_documents({"user_id": user_id})

    # Always eligible if shop has redeem enabled (you can tighten this later)
    eligible = True
    message = f"You are eligible for {discount}% discount at {shop.get('name', 'this shop')}."

    if bill_count == 0:
        # Still eligible on first visit — remove this check to require prior bills
        message = f"First visit! Enjoy {discount}% discount at {shop.get('name', 'this shop')}."

    return {
        "eligible": eligible,
        "discount": discount,
        "message": message,
        "shop_name": shop.get("name", ""),
    }


@router.post("", status_code=201)
async def redeem_reward(
    data: RedeemCreate,
    current_user: dict = Depends(get_current_user),
):
    """
    POST /redeem
    Redeem a reward for the current user.
    Creates a redeem record with a unique coupon code.
    """
    db = get_db()
    user_id = str(current_user.get("_id") or current_user.get("id"))

    # Validate reward exists and is active
    try:
        reward_oid = ObjectId(data.reward_id)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid reward ID")

    reward = await db.rewards.find_one({"_id": reward_oid, "is_active": True})
    if not reward:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reward not found or inactive")

    # Check expiry
    expires_at = reward.get("expires_at")
    if expires_at and expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Reward has expired")

    # Validate shop
    try:
        shop_oid = ObjectId(data.shop_id)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid shop ID")

    shop = await db.shops.find_one({"_id": shop_oid})
    if not shop:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shop not found")

    # Prevent double-redeem for the same reward by same user
    existing = await db.redeem.find_one({
        "user_id": user_id,
        "reward_id": data.reward_id,
        "status": {"$in": ["pending", "approved"]},
    })
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already have an active redeem for this reward",
        )

    coupon_code = _generate_coupon()
    redeem_doc = {
        "user_id": user_id,
        "shop_id": data.shop_id,
        "shop_name": shop.get("name", ""),
        "reward_id": data.reward_id,
        "reward_title": reward.get("title", ""),
        "status": "pending",
        "coupon_code": coupon_code,
        "redeemed_at": datetime.now(timezone.utc),
        "used_at": None,
    }

    result = await db.redeem.insert_one(redeem_doc)
    redeem_doc["_id"] = result.inserted_id
    return {
        "success": True,
        "message": f"Reward redeemed! Your coupon: {coupon_code}",
        "redeem": serialize_doc(redeem_doc),
    }


@router.get("/my")
async def get_my_redeems(
    current_user: dict = Depends(get_current_user),
):
    """GET /redeem/my — all redeems for the current user."""
    db = get_db()
    user_id = str(current_user.get("_id") or current_user.get("id"))

    cursor = db.redeem.find({"user_id": user_id}).sort("redeemed_at", -1)
    redeems = await cursor.to_list(length=100)
    return {
        "success": True,
        "redeems": [serialize_doc(r) for r in redeems],
        "total": len(redeems),
    }


@router.patch("/{redeem_id}/use")
async def mark_redeem_used(
    redeem_id: str,
    current_user: dict = Depends(get_current_user),
):
    """PATCH /redeem/{id}/use — mark a coupon as used at the shop."""
    db = get_db()
    user_id = str(current_user.get("_id") or current_user.get("id"))

    try:
        oid = ObjectId(redeem_id)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid redeem ID")

    result = await db.redeem.update_one(
        {"_id": oid, "user_id": user_id, "status": {"$in": ["pending", "approved"]}},
        {"$set": {"status": "used", "used_at": datetime.now(timezone.utc)}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Redeem record not found")

    return {"success": True, "message": "Coupon marked as used"}


@router.get("/{redeem_id}")
async def get_redeem(
    redeem_id: str,
    current_user: dict = Depends(get_current_user),
):
    """GET /redeem/{id} — single redeem detail."""
    db = get_db()
    user_id = str(current_user.get("_id") or current_user.get("id"))

    try:
        oid = ObjectId(redeem_id)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid ID")

    redeem = await db.redeem.find_one({"_id": oid, "user_id": user_id})
    if not redeem:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Redeem not found")

    return {"success": True, "redeem": serialize_doc(redeem)}
