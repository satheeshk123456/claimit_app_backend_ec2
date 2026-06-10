"""
Bill scan / wallet / history / manual-review endpoints.
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
from ..utils.notify import notify_user


def _require_admin(x_admin_key: Optional[str] = Header(None)):
    secret = os.getenv("ADMIN_SECRET_KEY", "claimit-admin-secret")
    if x_admin_key != secret:
        raise HTTPException(status_code=403, detail="Admin access required")

router = APIRouter(prefix="/bill", tags=["Bill"])


async def _get_new_user_config(db) -> dict:
    default_pts = int(os.getenv("NEW_USER_REWARD_POINTS", "1000"))
    default_cb  = float(os.getenv("NEW_USER_CASHBACK", "10.0"))
    try:
        cfg = await db.app_config.find_one({"key": "new_user_bonus"})
        if cfg:
            return {
                "reward_points": int(cfg.get("reward_points", default_pts)),
                "cashback":      float(cfg.get("cashback", default_cb)),
            }
    except Exception:
        pass
    return {"reward_points": default_pts, "cashback": default_cb}


async def _resolve_user(request: Request, body_uid: Optional[str]) -> Optional[str]:
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
    return body_uid


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


def _dup_key(shop: str, bill_date: str, amount: float,
             bill_time: Optional[str] = None) -> str:
    s   = shop.lower().strip()
    amt = f"{amount:.0f}"
    t   = (bill_time or "").strip()
    if t:
        return f"{s}|{bill_date}|{t}|{amt}"
    return f"{s}|{bill_date}|{amt}"


async def _get_or_create_wallet(db, uid: str) -> tuple:
    wallet = await db.user_wallets.find_one({"user_id": uid})
    if wallet is None:
        bonus = await _get_new_user_config(db)
        wallet = {
            "user_id":           uid,
            "reward_points":     bonus["reward_points"],
            "cashback_wallet":   bonus["cashback"],
            "lifetime_cashback": bonus["cashback"],
            "total_scans":       0,
            "created_at":        datetime.now(timezone.utc),
        }
        await db.user_wallets.insert_one(wallet)
        return wallet, True
    return wallet, False


@router.post("/scan")
async def scan_bill(data: BillScanRequest, request: Request):
    uid = await _resolve_user(request, data.user_id)
    if not uid:
        raise HTTPException(status_code=401,
                            detail="Authentication required — send Bearer token or user_id")

    db    = get_db()
    total = data.total_amount

    wallet, is_new = await _get_or_create_wallet(db, uid)
    is_first = is_new or wallet.get("total_scans", 0) == 0

    bill_date = data.bill_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = _dup_key(
        shop      = data.shop_name or "",
        bill_date = bill_date,
        amount    = total,
        bill_time = data.bill_time,
    )
    if await db.bill_scans.find_one({"user_id": uid, "dup_key": key}):
        raise HTTPException(status_code=409, detail="Bill already scanned")

    earned_cb  = round(total * 0.01, 2)
    earned_pts = round(total * 0.10)

    new_pts       = wallet["reward_points"]     + earned_pts
    new_cb_wallet = wallet["cashback_wallet"]   + earned_cb
    new_lifetime  = wallet["lifetime_cashback"] + earned_cb
    new_scans     = wallet.get("total_scans", 0) + 1

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

    bonus = await _get_new_user_config(db) if is_first else {"reward_points": 0, "cashback": 0.0}

    return {
        "ok":               True,
        "scan_id":          str(res.inserted_id),
        "shop_name":        shop_name,
        "earned_cashback":  earned_cb,
        "earned_points":    earned_pts,
        "is_new_user_bonus": is_first,
        "bonus_points":     bonus["reward_points"] if is_first else 0,
        "bonus_cashback":   bonus["cashback"]      if is_first else 0.0,
        "reward_points":    new_pts,
        "cashback_wallet":  new_cb_wallet,
        "lifetime_cashback": new_lifetime,
    }


@router.get("/wallet")
async def get_wallet(current_user: dict = Depends(get_current_user)):
    db     = get_db()
    uid    = str(current_user["_id"])
    wallet, _ = await _get_or_create_wallet(db, uid)
    return {
        "reward_points":     wallet["reward_points"],
        "cashback_wallet":   wallet["cashback_wallet"],
        "lifetime_cashback": wallet["lifetime_cashback"],
        "total_scans":       wallet.get("total_scans", 0),
    }


@router.get("/history")
async def bill_history(current_user: dict = Depends(get_current_user)):
    db    = get_db()
    uid   = str(current_user["_id"])
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


@router.get("/my-reviews")
async def my_reviews(current_user: dict = Depends(get_current_user)):
    db  = get_db()
    uid = str(current_user["_id"])
    cursor = db.bill_manual_reviews.find({"user_id": uid}, {"image_s3_key": 0}).sort("created_at", -1)
    docs = await cursor.to_list(length=100)
    return {"success": True, "reviews": [serialize_doc(d) for d in docs], "total": len(docs)}


@router.get("/manual-reviews")
async def admin_list_reviews(
    status: Optional[str] = Query(None),
    _: None = Depends(_require_admin),
):
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


@router.post("/manual-reviews/{review_id}/action")
async def admin_action_review(
    review_id: str,
    data: ReviewActionRequest,
    _: None = Depends(_require_admin),
):
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
        wallet, _ = await _get_or_create_wallet(db, uid)
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

    await notify_user(
        db,
        user_id=uid,
        title=(
            "🎉 Bill Approved — Rewards Added!" if data.action == "approve"
            else "Bill Review Update"
        ),
        message=(
            f"Your bill from {shop_name} (Rs.{int(amount)}) has been verified. "
            f"Rs.{cb:.0f} cashback and {pts} reward points added to your wallet."
        ) if data.action == "approve" else (
            f"Your bill from {shop_name} (Rs.{int(amount)}) could not be verified"
            + (f": {data.admin_note}" if data.admin_note else ".")
        ),
        type="bill_review_approved" if data.action == "approve" else "bill_review_rejected",
        data={"review_id": review_id},
    )

    return {
        "success": True, "action": data.action, "review_id": review_id,
        "reward_points": pts if data.action == "approve" else 0,
        "cashback":      cb  if data.action == "approve" else 0,
    }
