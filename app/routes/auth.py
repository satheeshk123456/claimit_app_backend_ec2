from datetime import datetime
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..database import get_db
from ..utils.auth import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
)
from ..utils.helpers import serialize_doc
from ..utils.otp import generate_otp, send_otp_sms, store_otp, verify_otp

router = APIRouter(prefix="/auth", tags=["Authentication"])


# ── Request / Response models ────────────────────────────────────────────────

class SendOtpRequest(BaseModel):
    # Field kept as "phone" for backward-compat with Flutter client.
    # Accepts mobile number OR email address.
    phone: str
    # "login"    → account MUST already exist; 404 if not found
    # "register" → account MUST NOT exist; 409 if already registered
    mode: str = "login"


class VerifyOtpRequest(BaseModel):
    phone: str   # mobile number OR email – same as above
    otp: str
    mode: str = "login"  # same semantics as SendOtpRequest.mode


class RefreshTokenRequest(BaseModel):
    refresh_token: str


# ── Helpers ──────────────────────────────────────────────────────────────────

def _is_email(identifier: str) -> bool:
    return "@" in identifier


async def _find_user(db, identifier: str):
    """Look up a user by phone number or email."""
    if _is_email(identifier):
        return await db.users.find_one({"email": identifier})
    return await db.users.find_one({"phone": identifier})


async def _auto_create_user(db, identifier: str) -> dict:
    """
    Create a new user with a generated display name.
    The user can update their name later from the Profile screen.

    IMPORTANT: We deliberately omit the phone/email field when the user
    didn't provide it, rather than storing None/null.  MongoDB sparse
    indexes only skip documents where the field is *absent* from the
    document — documents with the field set to null or "" are still
    indexed and would cause E11000 duplicate-key errors when a second
    email-only (or phone-only) user registers.
    """
    count = await db.users.count_documents({})
    default_name = f"User{count + 1}"

    user_doc: dict = {
        "full_name": default_name,
        "is_verified": True,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }
    # Only set the credential that was actually provided.
    if _is_email(identifier):
        user_doc["email"] = identifier
    else:
        user_doc["phone"] = identifier

    result = await db.users.insert_one(user_doc)
    user_doc["_id"] = result.inserted_id
    return user_doc


# ── Routes ───────────────────────────────────────────────────────────────────

@router.post("/send-otp")
async def send_otp(request: SendOtpRequest):
    """
    Send OTP to a mobile number or email address.

    mode="login"    → the account must already exist; returns 404 if not found.
    mode="register" → the account must NOT exist; returns 409 if already registered.
    """
    identifier = request.phone.strip()
    if not identifier:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mobile number or email is required",
        )

    db = get_db()
    existing_user = await _find_user(db, identifier)

    if request.mode == "login" and not existing_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No account found with this mobile number or email. Please register first.",
        )

    if request.mode == "register" and existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account already exists with this mobile number or email. Please login instead.",
        )

    otp = generate_otp()
    await store_otp(identifier, otp)
    try:
        await send_otp_sms(identifier, otp)
    except Exception as exc:
        # Delivery failure must never return 500 — OTP is stored; user can retry.
        print(f"⚠️  OTP delivery error for {identifier}: {exc}")

    return {
        "success": True,
        "message": f"OTP sent to {identifier}",
        "phone": identifier,
    }


@router.post("/verify-otp")
async def verify_otp_endpoint(request: VerifyOtpRequest):
    """
    Verify OTP and return auth tokens.

    - If OTP is valid and user already exists  → login.
    - If OTP is valid and user does NOT exist  → auto-create account,
      then login.  The user can fill in their name / details later
      from the Profile screen.
    """
    identifier = request.phone.strip()
    otp = request.otp.strip()

    # ── 1. Validate OTP ──────────────────────────────────────────────────────
    is_valid = await verify_otp(identifier, otp)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OTP",
        )

    # ── 2. Find or create user ───────────────────────────────────────────────
    db = get_db()
    user = await _find_user(db, identifier)

    if not user:
        if request.mode == "login":
            # Login attempted with an unregistered identifier
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No account found with this mobile number or email. Please register first.",
            )
        # Register mode — create account
        user = await _auto_create_user(db, identifier)
    else:
        if request.mode == "register":
            # Registration attempted with an already-registered identifier
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An account already exists with this mobile number or email. Please login instead.",
            )
        # Existing user — update last-login timestamp
        await db.users.update_one(
            {"_id": user["_id"]},
            {"$set": {"last_login": datetime.utcnow(), "is_verified": True}},
        )

    # ── 3. Issue tokens ──────────────────────────────────────────────────────
    user_id = str(user["_id"])
    access_token = create_access_token({"sub": user_id})
    refresh_token = create_refresh_token({"sub": user_id})

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": serialize_doc(user),
    }


@router.post("/refresh")
async def refresh_token(request: RefreshTokenRequest):
    """Refresh access token."""
    payload = decode_token(request.refresh_token)

    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    user_id = payload.get("sub")
    db = get_db()
    user = await db.users.find_one({"_id": ObjectId(user_id)})

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    access_token = create_access_token({"sub": user_id})
    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/logout")
async def logout(current_user: dict = Depends(get_current_user)):
    """Logout user (client should delete tokens)."""
    return {"success": True, "message": "Logged out successfully"}
