from fastapi import APIRouter, HTTPException, status, Depends, UploadFile, File
from datetime import datetime
from bson import ObjectId
import os
from typing import Optional
from ..database import get_db
from ..utils.auth import get_current_user
from ..utils.helpers import serialize_doc, generate_claim_number
from ..models.claim import ClaimCreate, ClaimUpdate, ClaimTimelineEvent
from ..config import get_settings
from ..utils.s3 import generate_presigned_url as _s3_presign

router = APIRouter(prefix="/claims", tags=["Claims"])
settings = get_settings()


async def _resolve_document_urls(claim_doc: dict) -> list:
    """Return presigned URLs for all documents. Handles both s3_key and legacy https:// formats."""
    urls = []
    for entry in claim_doc.get("documents", []):
        if not entry:
            continue
        if entry.startswith("https://") or entry.startswith("http://"):
            try:
                from urllib.parse import urlparse
                s3_key = urlparse(entry).path.lstrip("/")
                presigned = await _s3_presign(s3_key) if s3_key else None
                urls.append(presigned or entry)
            except Exception:
                urls.append(entry)
        else:
            presigned = await _s3_presign(entry)
            if presigned:
                urls.append(presigned)
    return urls


async def create_notification(db, user_id: str, title: str, message: str,
                               notification_type: str = "info", claim_id: str = None):
    await db.notifications.insert_one({
        "user_id": user_id,
        "title": title,
        "message": message,
        "type": notification_type,
        "is_read": False,
        "claim_id": claim_id,
        "created_at": datetime.utcnow(),
    })


@router.get("")
async def get_claims(
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 10,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    user_id = current_user.get("_id") or current_user.get("id")
    query = {"user_id": user_id}
    if status:
        query["status"] = status
    skip = (page - 1) * page_size
    cursor = db.claims.find(query).sort("submitted_at", -1).skip(skip).limit(page_size)
    claims = await cursor.to_list(length=page_size)
    result = []
    for claim in claims:
        doc = serialize_doc(claim)
        doc["documents"] = await _resolve_document_urls(doc)
        result.append(doc)
    return result


@router.post("/create", status_code=status.HTTP_201_CREATED)
async def create_claim(
    claim_data: ClaimCreate,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    user_id = current_user.get("_id") or current_user.get("id")
    claim_number = generate_claim_number()
    timeline_event = {
        "status": "pending",
        "message": "Claim submitted successfully. Under initial review.",
        "timestamp": datetime.utcnow(),
        "updated_by": "system",
    }
    claim_doc = {
        "user_id": user_id,
        "claim_number": claim_number,
        "claim_type": claim_data.claim_type,
        "policy_number": claim_data.policy_number,
        "description": claim_data.description,
        "claim_amount": claim_data.claim_amount,
        "status": "pending",
        "documents": [],
        "timeline": [timeline_event],
        "incident_date": claim_data.incident_date,
        "submitted_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }
    result = await db.claims.insert_one(claim_doc)
    claim_doc["_id"] = str(result.inserted_id)
    await create_notification(
        db, user_id, "Claim Submitted",
        "Your claim " + claim_number + " has been submitted successfully.",
        "success", str(result.inserted_id),
    )
    return serialize_doc(claim_doc)


@router.get("/{claim_id}")
async def get_claim(
    claim_id: str,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    user_id = current_user.get("_id") or current_user.get("id")
    try:
        claim = await db.claims.find_one({"_id": ObjectId(claim_id), "user_id": user_id})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid claim ID")
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    doc = serialize_doc(claim)
    doc["documents"] = await _resolve_document_urls(doc)
    return doc


@router.post("/{claim_id}/documents")
async def upload_document(
    claim_id: str,
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    user_id = current_user.get("_id") or current_user.get("id")
    try:
        claim = await db.claims.find_one({"_id": ObjectId(claim_id), "user_id": user_id})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid claim ID")
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    allowed_types = [
        "application/pdf", "image/jpeg", "image/png", "image/jpg",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ]
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="File type not allowed")
    content = await file.read()
    if len(content) > settings.max_file_size_mb * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File size exceeds " + str(settings.max_file_size_mb) + "MB")
    from ..utils.s3 import upload_bytes as _s3_upload
    s3_key = await _s3_upload(content, folder="claims/" + claim_id,
                              filename=file.filename,
                              content_type=file.content_type or "application/octet-stream")
    doc_url = await _s3_presign(s3_key) or ""
    await db.claims.update_one(
        {"_id": ObjectId(claim_id)},
        {"$push": {"documents": s3_key}, "$set": {"updated_at": datetime.utcnow()}},
    )
    return {"document_url": doc_url, "filename": file.filename}


@router.get("/{claim_id}/timeline")
async def get_claim_timeline(
    claim_id: str,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    user_id = current_user.get("_id") or current_user.get("id")
    try:
        claim = await db.claims.find_one(
            {"_id": ObjectId(claim_id), "user_id": user_id}, {"timeline": 1}
        )
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid claim ID")
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    return claim.get("timeline", [])


@router.patch("/{claim_id}/status")
async def update_claim_status(
    claim_id: str,
    update_data: ClaimUpdate,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    try:
        claim = await db.claims.find_one({"_id": ObjectId(claim_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid claim ID")
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")

    update_dict = {k: v for k, v in update_data.model_dump().items() if v is not None}
    update_dict["updated_at"] = datetime.utcnow()

    if update_data.status:
        approved_amount = update_data.approved_amount or claim.get("claim_amount", "")
        rejection_reason = update_data.rejection_reason or "Not specified"
        status_messages = {
            "under_review": "Your claim is now under review by our team.",
            "approved": "Your claim has been approved. Approved amount: Rs." + str(approved_amount),
            "rejected": "Your claim has been rejected. Reason: " + str(rejection_reason),
            "settled": "Your claim has been settled. Amount will be credited within 3-5 business days.",
        }
        status_label = update_data.status.replace("_", " ").title()
        default_msg = "Status updated to " + update_data.status
        timeline_event = {
            "status": update_data.status,
            "message": status_messages.get(update_data.status, default_msg),
            "timestamp": datetime.utcnow(),
            "updated_by": current_user.get("_id") or current_user.get("id"),
        }
        await db.claims.update_one(
            {"_id": ObjectId(claim_id)},
            {"$set": update_dict, "$push": {"timeline": timeline_event}},
        )
        notification_types = {
            "approved": "success",
            "rejected": "error",
            "under_review": "info",
            "settled": "success",
        }
        claim_number = claim.get("claim_number", "")
        await create_notification(
            db, claim["user_id"],
            "Claim " + claim_number + " - " + status_label,
            timeline_event["message"],
            notification_types.get(update_data.status, "info"),
            claim_id,
        )
    else:
        await db.claims.update_one(
            {"_id": ObjectId(claim_id)}, {"$set": update_dict}
        )

    updated_claim = await db.claims.find_one({"_id": ObjectId(claim_id)})
    return serialize_doc(updated_claim)
