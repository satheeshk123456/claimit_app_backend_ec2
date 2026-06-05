"""
Bill scan / wallet / history / manual-review endpoints — Vercel backend.

Routes:
  POST /bill/scan                        — record a scanned bill, award cashback + points
  GET  /bill/wallet                      — current wallet totals for the logged-in user
  GET  /bill/history                     — last 100 scans, newest first
  POST /bill/manual-review               — submit bill for manual staff review
  GET  /bill/my-reviews                  — user's own manual review submissions
  GET  /bill/manual-reviews              — admin: list all reviews (filter by status)
  POST /bill/manual-reviews/{id}/action  — admin: approve/reject → credit wallet + notify

Business rules (must stay in sync with BillRewardProvider in Flutter):
  • 1 % cashback  (earned_cashback  = total * 0.01)
  • 10 % points   (earned_points    = total * 0.10, rounded)
  • New users get NEW_USER_BONUS = 1 000 free welcome points on wallet creation
  • Duplicate detection: fingerprint = "shop|date|HH:MM|amount" (with time)
                        or           "shop|date|amount"         (without time)
    → matches the duplicateKey built in BillRewardEntry.duplicateKey (Flutter)
  • bill_scans documents auto-expire after 24 h (TTL index on scanned_at)
"""

import os
from datetime import datetime, timezone
from typing import Optional, Literal

from fastapi import APIRouter, Depends, HTTPException, Header, Query, Request
from pydantic import BaseModel, Field
from bson import ObjectId

from ..database import get_db
from ..utils.auth import get_current_user, decode_token
from ..utils.helpers import serialize_doc
from ..utils.s3 import upload_base64, generate_presigned_url


def _require_admin(x_admin_key: Optional[str] = Header(None)):
    secret = os.getenv("ADMIN_SECRET_KEY", "claimit-admin-secret")
    if x_admin_key != secret:
        raise HTTPException(status_code=403, detail="Admin access required")

router = APIRouter(prefix="/bill", tags=["Bill"])

NEW_USER_BONUS = 1000   # free points awarded on first wallet creation


# ── Optional auth ─────────────────────────────────────────────────────────────
# The Flutter app always sends a Bearer token, but we also accept a body
# user_id as a fallback (clock-skew / staging secret differences).

async def _resolve_user(request: Request, body_uid: Optional[str]) -> Optional[str]:
    """Return user_id string or None.  Never raises — callers decide if 401 needed."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[len("Bearer "):]
        try:
            payload = decode_token(token)
            uid = payload.get("sub")
            if uid:
                return uid
        except Exception:
            pass
    # Fallback: body-supplied user_id
    return body_uid


# ── Request model ──────────────────────────────────────────────────────────────
class BillScanRequest(BaseModel):
    total_amount:  float          = Field(..., gt=0)
    reward_points: Optional[int]  = None
    shop_name:     Optional[str]  = None
    bill_number:   Optional[str]  = None
    bill_date:     Optional[str]  = None
    bill_time:     Optional[str]  = None
    image_base64:  Optional[str]  = None
    user_id:       Optional[str]  = None


class ManualReviewRequest(BaseModel):
    total_amount:  float  = Field(..., gt=0)
    image_base64:  str    = Field(..., description="Base64-encoded bill image")
    shop_name:     Optional[str] = None
    bill_number:   Optional[str] = None
    bill_date:     Optional[str] = None
    bill_time:     Optional[str] = None
    manual_reason: Optional[str] = "missing_fields"
    user_id:       Optional[str] = None


class ReviewActionRequest(BaseModel):
    action:        Literal["approve", "reject"]
    reward_points: Optional[int]   = None
    cashback:      Optional[float] = None
    admin_note:    Optional[str]   = None


# ── Helpers ────────────────────────────────────────────────────────────────────
def _dup_key(shop: str, bill_date: str, amount: float,
             bill_time: Optional[str] = None) -> str:
    """
    Duplicate fingerprint — must match BillRewardEntry.duplicateKey in Flutter.

    With time:    "shop|YYYY-M-D|HH:MM|amount"
    Without time: "shop|YYYY-M-D|amount"

    Two purchases at the same shop on the same day for the same price but at
    different times produce different keys and are both allowed.
    """
    s   = shop.lower().strip()
    amt = f"{amount:.0f}"
    t   = (bill_time or "").strip()
    if t:
        return f"{s}|{bill_date}|{t}|{amt}"
    return f"{s}|{bill_date}|{amt}"


async def _get_or_create_wallet(db, uid: str) -> dict:
    """Return existing wallet or create a brand-new one with 1 000 welcome pts."""
    wallet = await db.user_wallets.find_one({"user_id": uid})
    if wallet is None:
        wallet = {
            "user_id":           uid,
            "reward_points":     NEW_USER_BONUS,   # ← 1 000 welcome bonus
            "cashback_wallet":   0.0,
            "lifetime_cashback": 0.0,
            "total_scans":       0,
            "created_at":        datetime.now(timezone.utc),
        }
        await db.user_wallets.insert_one(wallet)
    return wallet


# ── POST /bill/scan ────────────────────────────────────────────────────────────
@router.post("/scan")
async def scan_bill(data: BillScanRequest, request: Request):
    uid = await _resolve_user(request, data.user_id)
    if not uid:
        raise HTTPException(status_code=401,
                            detail="Authentication required — send Bearer token or user_id")

    db    = get_db()
    total = data.total_amount

    # ── Load / create wallet ──────────────────────────────────────────────────
    wallet   = await _get_or_create_wallet(db, uid)
    is_first = wallet.get("total_scans", 0) == 0   # first real scan this wallet?

    # ── Duplicate check ───────────────────────────────────────────────────────
    bill_date = data.bill_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = _dup_key(
        shop      = data.shop_name or "",
        bill_date = bill_date,
        amount    = total,
        bill_time = data.bill_time,
    )
    if await db.bill_scans.find_one({"user_id": uid, "dup_key": key}):
        raise HTTPException(status_code=409, detail="Bill already scanned")

    # ── Calculate rewards (server is authoritative) ───────────────────────────
    earned_cb  = round(total * 0.01, 2)    # 1 % cashback
    earned_pts = round(total * 0.10)       # 10 % reward points

    # ── Cumulative wallet update ──────────────────────────────────────────────
    new_pts      = wallet["reward_points"]     + earned_pts
    new_cb_wallet= wallet["cashback_wallet"]   + earned_cb
    new_lifetime = wallet["lifetime_cashback"] + earned_cb
    new_scans    = wallet.get("total_scans", 0) + 1

    await db.user_wallets.update_one(
        {"user_id": uid},
        {"$set": {
            "reward_points":     new_pts,
            "cashback_wallet":   new_cb_wallet,
            "lifetime_cashback": new_lifetime,
            "total_scans":       new_scans,
            "updated_at":        datetime.now(timezone.utc),
        }},
    )

    # ── Store scan record ─────────────────────────────────────────────────────
    shop_name = (data.shop_name or "Shop").strip()
    scan_doc = {
        "user_id":         uid,
        "dup_key":         key,
        "shop_name":       shop_name,
        "total_amount":    total,
        "earned_cashback": earned_cb,
        "earned_points":   earned_pts,
        "bill_number":     data.bill_number,
        "bill_date":       bill_date,
        "bill_time":       data.bill_time,
        "scanned_at":      datetime.now(timezone.utc),
    }
    res = await db.bill_scans.insert_one(scan_doc)

    return {
        "ok":               True,
        "scan_id":          str(res.inserted_id),
        "shop_name":        shop_name,
        # Earned this scan
        "earned_cashback":  earned_cb,
        "earned_points":    earned_pts,
        # New user welcome bonus info
        "is_new_user_bonus": is_first,
        "bonus_points":     NEW_USER_BONUS if is_first else 0,
        # Running wallet totals — Flutter app updates its local state from these
        "reward_points":    new_pts,
        "cashback_wallet":  new_cb_wallet,
        "lifetime_cashback": new_lifetime,
    }


# ── GET /bill/wallet ──────────────────────────────────────────────────────────
@router.get("/wallet")
async def get_wallet(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    db     = get_db()
    uid    = str(current_user["_id"])
    wallet = await _get_or_create_wallet(db, uid)
    return {
        "reward_points":     wallet["reward_points"],
        "cashback_wallet":   wallet["cashback_wallet"],
        "lifetime_cashback": wallet["lifetime_cashback"],
        "total_scans":       wallet.get("total_scans", 0),
    }


# ── GET /bill/history ─────────────────────────────────────────────────────────
@router.get("/history")
async def bill_history(
    current_user: dict = Depends(get_current_user),
):
    """Returns a plain list (Flutter app iterates it directly)."""
    db    = get_db()
    uid   = str(current_user["_id"])
    scans = await db.bill_scans.find(
        {"user_id": uid}
    ).sort("scanned_at", -1).to_list(100)

    return [
        {
            "id":              str(s["_id"]),
            "shop_name":       s.get("shop_name", ""),
            "total_amount":    s.get("total_amount", 0),
            "earned_cashback": s.get("earned_cashback", 0),
            "earned_points":   s.get("earned_points", 0),
            "bill_number":     s.get("bill_number"),
            "bill_date":       s.get("bill_date"),
            "bill_time":       s.get("bill_time"),
            "scanned_at":      s["scanned_at"].isoformat() if s.get("scanned_at") else "",
        }
        for s in scans
    ]


# ── POST /bill/manual-review ──────────────────────────────────────────────────
@router.post("/manual-review", status_code=201)
async def submit_manual_review(data: ManualReviewRequest, request: Request):
    """
    User submits a bill image + details for manual staff review.
    No points awarded yet — admin approves/rejects separately.
    """
    uid = await _resolve_user(request, data.user_id)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")

    db = get_db()

    parsed_date = None
    if data.bill_date:
        try:
            parsed_date = datetime.strptime(data.bill_date, "%Y-%m-%d")
        except ValueError:
            pass

    doc = {
        "user_id":       uid,
        "total_amount":  round(data.total_amount, 2),
        "shop_name":     (data.shop_name or "").strip() or None,
        "bill_number":   (data.bill_number or "").strip() or None,
        "bill_date":     parsed_date,
        "bill_time":     (data.bill_time or "").strip() or None,
        "manual_reason": data.manual_reason or "missing_fields",
        "has_image":     bool(data.image_base64),
        "image_s3_key":  (await upload_base64(data.image_base64, "bill-reviews")
                  if data.image_base64 else None),
        "status":        "pending",
        "created_at":    datetime.now(timezone.utc),
        "reviewed_at":   None,
        "review_note":   None,
    }

    result = await db.bill_manual_reviews.insert_one(doc)
    return {
        "success":   True,
        "review_id": str(result.inserted_id),
        "message":   "Your bill has been submitted for review. "
                     "Our team will verify it within 24 hours.",
    }


# ── GET /bill/my-reviews ──────────────────────────────────────────────────────
@router.get("/my-reviews")
async def my_reviews(current_user: dict = Depends(get_current_user)):
    """User's own manual review submissions (no image in response)."""
    db  = get_db()
    uid = str(current_user["_id"])
    cursor = db.bill_manual_reviews.find(
        {"user_id": uid}, {"image_s3_key": 0}
    ).sort("created_at", -1)
    docs = await cursor.to_list(length=100)
    return {
        "success": True,
        "reviews": [serialize_doc(d) for d in docs],
        "total":   len(docs),
    }


# ── GET /bill/manual-reviews  (admin) ────────────────────────────────────────
@router.get("/manual-reviews")
async def admin_list_reviews(
    status: Optional[str] = Query(None),
    _: None = Depends(_require_admin),
):
    """Admin: list all manual review submissions with bill image."""
    db    = get_db()
    query = {} if not status or status == "all" else {"status": status}
    cursor = db.bill_manual_reviews.find(query).sort("created_at", -1)
    docs   = await cursor.to_list(length=500)
    result = []
    for d in docs:
        s = serialize_doc(d)
        s.setdefault("submitted_at", s.get("created_at"))
        s3_key = s.pop("image_s3_key", None)
        s["image_url"] = await generate_presigned_url(s3_key) if s3_key else None
        result.append(s)
    return result


# ── POST /bill/manual-reviews/{id}/action  (admin) ───────────────────────────
@router.post("/manual-reviews/{review_id}/action")
async def admin_action_review(
    review_id: str,
    data: ReviewActionRequest,
    _: None = Depends(_require_admin),
):
    """
    Admin approves or rejects a manual bill review.
    On approve: credits user_wallets, creates bill_scans entry, pushes notification.
    On reject:  updates status, pushes notification.
    """
    db = get_db()

    try:
        oid = ObjectId(review_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid review ID")

    review = await db.bill_manual_reviews.find_one({"_id": oid})
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    if review.get("status") != "pending":
        raise HTTPException(status_code=409,
                            detail=f"Review already {review.get('status')}")

    now       = datetime.now(timezone.utc)
    uid       = review.get("user_id", "")
    amount    = float(review.get("total_amount", 0))
    shop_name = review.get("shop_name") or "Shop"

    pts = data.reward_points if data.reward_points is not None else max(1, int(amount * 0.1))
    cb  = data.cashback      if data.cashback      is not None else round(amount * 0.01, 2)

    # Update review document
    update_fields: dict = {
        "status":      data.action,
        "reviewed_at": now,
        "review_note": data.admin_note or "",
    }
    if data.action == "approve":
        update_fields["reward_points"] = pts
        update_fields["cashback"]      = cb

    await db.bill_manual_reviews.update_one({"_id": oid}, {"$set": update_fields})

    if data.action == "approve":
        # Credit user_wallets
        wallet = await db.user_wallets.find_one({"user_id": uid})
        if wallet:
            await db.user_wallets.update_one(
                {"user_id": uid},
                {"$inc": {
                    "reward_points":     pts,
                    "cashback_wallet":   cb,
                    "lifetime_cashback": cb,
                }},
            )
        else:
            # Create wallet if first time
            await db.user_wallets.insert_one({
                "user_id":           uid,
                "reward_points":     NEW_USER_BONUS + pts,
                "cashback_wallet":   cb,
                "lifetime_cashback": cb,
                "total_scans":       0,
                "created_at":        now,
            })

        # Create bill_scans entry so it shows in history
        await db.bill_scans.insert_one({
            "user_id":         uid,
            "dup_key":         f"review|{review_id}",
            "shop_name":       shop_name,
            "total_amount":    round(amount, 2),
            "earned_cashback": cb,
            "earned_points":   pts,
            "bill_number":     review.get("bill_number"),
            "bill_date":       review.get("bill_date", ""),
            "bill_time":       review.get("bill_time"),
            "source":          "manual_review",
            "review_id":       str(oid),
            "scanned_at":      now,
        })

    # Push in-app notification
    if data.action == "approve":
        title   = "🎉 Bill Approved — Rewards Added!"
        message = (f"Your bill from {shop_name} (₹{int(amount)}) has been verified. "
                   f"₹{cb:.0f} cashback and {pts} reward points have been added to your wallet.")
        ntype   = "bill_review_approved"
    else:
        title   = "Bill Review Update"
        message = (f"Your bill submission from {shop_name} (₹{int(amount)}) could not be verified"
                   + (f": {data.admin_note}" if data.admin_note else "."))
        ntype   = "bill_review_rejected"

    await db.notifications.insert_one({
        "user_id":    uid,
        "title":      title,
        "message":    message,
        "type":       ntype,
        "is_read":    False,
        "review_id":  str(oid),
        "created_at": now,
    })

    return {
        "success":       True,
        "action":        data.action,
        "review_id":     review_id,
        "reward_points": pts if data.action == "approve" else 0,
        "cashback":      cb  if data.action == "approve" else 0,
        "message": (
            f"Approved. {pts} pts and ₹{cb:.2f} cashback credited."
            if data.action == "approve"
            else "Rejected. User notified."
        ),
    }


# ── GET /bill/history ─────────────────────────────────────────────────────────
@router.get("/history")
async def bill_history(current_user: dict = Depends(get_current_user)):
    db  = get_db()
    uid = str(current_user["_id"])
    scans = await db.bill_scans.find({"user_id": uid}).sort("scanned_at", -1).to_list(100)
    return [
        {
            "id":              str(s["_id"]),
            "shop_name":       s.get("shop_name", ""),
            "total_amount":    s.get("total_amount", 0),
            "earned_cashback": s.get("earned_cashback", 0),
            "earned_points":   s.get("earned_points", 0),
            "bill_number":     s.get("bill_number"),
            "bill_date":       s.get("bill_date"),
            "bill_time":       s.get("bill_time"),
            "scanned_at":      s["scanned_at"].isoformat() if s.get("scanned_at") else "",
        }
        for s in scans
    ]


# ── POST /bill/manual-review ──────────────────────────────────────────────────
@router.post("/manual-review", status_code=201)
async def submit_manual_review(data: ManualReviewRequest, request: Request):
    uid = await _resolve_user(request, data.user_id)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")
    db = get_db()
    parsed_date = None
    if data.bill_date:
        try:
            parsed_date = datetime.strptime(data.bill_date, "%Y-%m-%d")
        except ValueError:
            pass
    doc = {
        "user_id":       uid,
        "total_amount":  round(data.total_amount, 2),
        "shop_name":     (data.shop_name or "").strip() or None,
        "bill_number":   (data.bill_number or "").strip() or None,
        "bill_date":     parsed_date,
        "bill_time":     (data.bill_time or "").strip() or None,
        "manual_reason": data.manual_reason or "missing_fields",
        "has_image":     bool(data.image_base64),
        "image_s3_key":  (await upload_base64(data.image_base64, "bill-reviews")
                  if data.image_base64 else None),
        "status":        "pending",
        "created_at":    datetime.now(timezone.utc),
        "reviewed_at":   None,
        "review_note":   None,
    }
    result = await db.bill_manual_reviews.insert_one(doc)
    return {
        "success":   True,
        "review_id": str(result.inserted_id),
        "message":   "Your bill has been submitted for review. Our team will verify it within 24 hours.",
    }


# ── GET /bill/my-reviews ──────────────────────────────────────────────────────
@router.get("/my-reviews")
async def my_reviews(current_user: dict = Depends(get_current_user)):
    db  = get_db()
    uid = str(current_user["_id"])
    cursor = db.bill_manual_reviews.find({"user_id": uid}, {"image_s3_key": 0}).sort("created_at", -1)
    docs = await cursor.to_list(length=100)
    return {"success": True, "reviews": [serialize_doc(d) for d in docs], "total": len(docs)}


# ── GET /bill/manual-reviews  (admin) ────────────────────────────────────────
@router.get("/manual-reviews")
async def admin_list_reviews(status: Optional[str] = Query(None), _: None = Depends(_require_admin)):
    db    = get_db()
    query = {} if not status or status == "all" else {"status": status}
    cursor = db.bill_manual_reviews.find(query).sort("created_at", -1)
    docs   = await cursor.to_list(length=500)
    result = []
    for d in docs:
        s = serialize_doc(d)
        s.setdefault("submitted_at", s.get("created_at"))
        s3_key = s.pop("image_s3_key", None)
        s["image_url"] = await generate_presigned_url(s3_key) if s3_key else None
        result.append(s)
    return result


# ── POST /bill/manual-reviews/{id}/action  (admin) ───────────────────────────
@router.post("/manual-reviews/{review_id}/action")
async def admin_action_review(review_id: str, data: ReviewActionRequest, _: None = Depends(_require_admin)):
    db = get_db()
    try:
        oid = ObjectId(review_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid review ID")
    review = await db.bill_manual_reviews.find_one({"_id": oid})
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    if review.get("status") != "pending":
        raise HTTPException(status_code=409, detail=f"Review already {review.get('status')}")

    now       = datetime.now(timezone.utc)
    uid       = review.get("user_id", "")
    amount    = float(review.get("total_amount", 0))
    shop_name = review.get("shop_name") or "Shop"
    pts = data.reward_points if data.reward_points is not None else max(1, int(amount * 0.1))
    cb  = data.cashback      if data.cashback      is not None else round(amount * 0.01, 2)

    update_fields = {"status": data.action, "reviewed_at": now, "review_note": data.admin_note or ""}
    if data.action == "approve":
        update_fields["reward_points"] = pts
        update_fields["cashback"]      = cb
    await db.bill_manual_reviews.update_one({"_id": oid}, {"$set": update_fields})

    if data.action == "approve":
        wallet = await _get_or_create_wallet(db, uid)
        await db.user_wallets.update_one(
            {"user_id": uid},
            {"$set": {
                "reward_points":     wallet["reward_points"]     + pts,
                "cashback_wallet":   wallet["cashback_wallet"]   + cb,
                "lifetime_cashback": wallet["lifetime_cashback"] + cb,
                "updated_at":        now,
            }},
        )
        await db.bill_scans.insert_one({
            "user_id": uid, "dup_key": f"review|{review_id}",
            "shop_name": shop_name, "total_amount": round(amount, 2),
            "earned_cashback": cb, "earned_points": pts,
            "bill_number": review.get("bill_number"),
            "bill_date":   str(review.get("bill_date", "")),
            "bill_time":   review.get("bill_time"),
            "source": "manual_review", "review_id": review_id, "scanned_at": now,
        })

    await db.notifications.insert_one({
        "user_id":    uid,
        "title":      "🎉 Bill Approved — Rewards Added!" if data.action == "approve" else "Bill Review Update",
        "message":    (
            f"Your bill from {shop_name} (Rs.{int(amount)}) has been verified. "
            f"Rs.{cb:.0f} cashback and {pts} reward points added to your wallet."
        ) if data.action == "approve" else (
            f"Your bill from {shop_name} (Rs.{int(amount)}) could not be verified"
            + (f": {data.admin_note}" if data.admin_note else ".")
        ),
        "type":    "bill_review_approved" if data.action == "approve" else "bill_review_rejected",
        "is_read": False, "review_id": review_id, "created_at": now,
    })

    return {
        "success": True, "action": data.action, "review_id": review_id,
        "reward_points": pts if data.action == "approve" else 0,
        "cashback":      cb  if data.action == "approve" else 0,
    }
