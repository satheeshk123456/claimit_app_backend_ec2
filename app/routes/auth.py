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
from ..utils.notify import notify_user

router = APIRouter(prefix="/auth", tags=["Authentication"])


# ── Request / Response models ────────────────────────────────────────────────

class SendOtpRequest(BaseModel):
    phone: str
    mode: str = "login"


class VerifyOtpRequest(BaseModel):
    phone: str
    otp: str
    mode: str = "login"
    fcm_token: Optional[str] = None
    fcm_platform: Optional[str] = None


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    fcm_token: Optional[str] = None


class SocialLoginRequest(BaseModel):
    provider: str
    name: str = ""
    email: Optional[str] = None
    provider_id: str = ""


# ── Helpers ──────────────────────────────────────────────────────────────────

def _is_email(identifier: str) -> bool:
    return "@" in identifier


async def _find_user(db, identifier: str):
    if _is_email(identifier):
        return await db.users.find_one({"email": identifier})
    return await db.users.find_one({"phone": identifier})


async def _auto_create_user(db, identifier: str) -> dict:
    count = await db.users.count_documents({})
    default_name = f"User{count + 1}"

    user_doc: dict = {
        "full_name": default_name,
        "is_verified": True,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }
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
        print(f"⚠️  OTP delivery error for {identifier}: {exc}")

    return {
        "success": True,
        "message": f"OTP sent to {identifier}",
        "phone": identifier,
    }


@router.post("/verify-otp")
async def verify_otp_endpoint(request: VerifyOtpRequest):
    identifier = request.phone.strip()
    otp = request.otp.strip()

    is_valid = await verify_otp(identifier, otp)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OTP",
        )

    db = get_db()
    user = await _find_user(db, identifier)

    if not user:
        if request.mode == "login":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No account found with this mobile number or email. Please register first.",
            )
        user = await _auto_create_user(db, identifier)
    else:
        if request.mode == "register":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An account already exists with this mobile number or email. Please login instead.",
            )
        await db.users.update_one(
            {"_id": user["_id"]},
            {"$set": {"last_login": datetime.utcnow(), "is_verified": True}},
        )

    user_id = str(user["_id"])
    access_token = create_access_token({"sub": user_id})
    refresh_token = create_refresh_token({"sub": user_id})

    if request.fcm_token:
        token = request.fcm_token.strip()
        if token:
            await db.fcm_tokens.update_one(
                {"token": token},
                {
                    "$set": {
                        "user_id": user_id,
                        "platform": request.fcm_platform,
                        "updated_at": datetime.utcnow(),
                    },
                    "$setOnInsert": {"created_at": datetime.utcnow()},
                },
                upsert=True,
            )

    display_name = user.get("name") or identifier
    await notify_user(
        db,
        user_id,
        title="✅ Login Successful",
        message=f"Welcome back, {display_name}! You're now logged in to Claimit.",
        type="success",
        data={"event": "login_success"},
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": serialize_doc(user),
    }


@router.post("/refresh")
async def refresh_token(request: RefreshTokenRequest):
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


@router.post("/social/login")
async def social_login(request: SocialLoginRequest):
    db = get_db()
    provider = request.provider.strip().lower()
    email = request.email.strip() if request.email else None
    provider_id = request.provider_id.strip()
    name = request.name.strip()

    user = None
    if email:
        user = await db.users.find_one({"email": email})
    if not user and provider_id:
        user = await db.users.find_one({f"{provider}_id": provider_id})

    if not user:
        count = await db.users.count_documents({})
        user_doc: dict = {
            "full_name": name or f"User{count + 1}",
            "is_verified": True,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
            f"{provider}_id": provider_id,
        }
        if email:
            user_doc["email"] = email
        result = await db.users.insert_one(user_doc)
        user_doc["_id"] = result.inserted_id
        user = user_doc
    else:
        updates: dict = {"updated_at": datetime.utcnow()}
        if provider_id and not user.get(f"{provider}_id"):
            updates[f"{provider}_id"] = provider_id
        if name and not user.get("full_name"):
            updates["full_name"] = name
        await db.users.update_one({"_id": user["_id"]}, {"$set": updates})

    user_id = str(user["_id"])
    access_token = create_access_token({"sub": user_id})
    refresh_token = create_refresh_token({"sub": user_id})

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": serialize_doc(user),
    }


@router.post("/logout")
async def logout(
    request: LogoutRequest = LogoutRequest(),
    current_user: dict = Depends(get_current_user),
):
    if request.fcm_token:
        db = get_db()
        user_id = str(current_user.get("_id") or current_user.get("id"))
        await db.fcm_tokens.delete_one({"token": request.fcm_token.strip(), "user_id": user_id})

    return {"success": True, "message": "Logged out successfully"}
