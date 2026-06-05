"""
S3 image/file storage utility for Claimit.

All image uploads across the app (bill reviews, avatars, claim docs,
classified photos, shop images, ad creatives) go through this module.

MongoDB stores only the lightweight s3_key string.
Presigned URLs (valid 1 h) are generated on demand for viewing.

AWS credentials are read from environment variables:
  AWS_ACCESS_KEY_ID
  AWS_SECRET_ACCESS_KEY
  AWS_REGION
  AWS_STORAGE_BUCKET_NAME
"""

import os
import uuid
import asyncio
import mimetypes
from functools import partial
from typing import Optional

import boto3
from botocore.exceptions import ClientError, BotoCoreError

# ── Config ────────────────────────────────────────────────────────────────────
_AWS_KEY    = os.getenv("AWS_ACCESS_KEY_ID",       "")
_AWS_SECRET = os.getenv("AWS_SECRET_ACCESS_KEY",   "")
_AWS_REGION = os.getenv("AWS_REGION",              "eu-north-1")
_BUCKET     = os.getenv("AWS_STORAGE_BUCKET_NAME", "claimit-image-bucket")
_URL_EXPIRY = 3600   # presigned URL valid for 1 hour


def _s3_client():
    """Return a boto3 S3 client (created per-call for serverless safety)."""
    return boto3.client(
        "s3",
        region_name=_AWS_REGION,
        aws_access_key_id=_AWS_KEY,
        aws_secret_access_key=_AWS_SECRET,
    )


# ── Upload ────────────────────────────────────────────────────────────────────

def _sync_upload(data: bytes, key: str, content_type: str) -> str:
    """Sync upload — runs in a thread executor."""
    _s3_client().put_object(
        Bucket=_BUCKET,
        Key=key,
        Body=data,
        ContentType=content_type,
    )
    return key


async def upload_bytes(
    data: bytes,
    folder: str,
    filename: Optional[str] = None,
    content_type: str = "image/jpeg",
) -> str:
    """
    Upload raw bytes to S3.

    Args:
        data:         File bytes to upload.
        folder:       S3 folder prefix, e.g. "bill-reviews", "avatars".
        filename:     Original filename (used for extension only).
                      If None, a UUID is used.
        content_type: MIME type; defaults to image/jpeg.

    Returns:
        s3_key — the unique object key stored in MongoDB.
    """
    ext = ""
    if filename:
        _, ext = os.path.splitext(filename)
    if not ext:
        ext = mimetypes.guess_extension(content_type) or ".bin"

    key = f"{folder}/{uuid.uuid4().hex}{ext}"

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, partial(_sync_upload, data, key, content_type))
    return key


async def upload_base64(
    b64_string: str,
    folder: str,
    content_type: str = "image/jpeg",
) -> str:
    """
    Upload a base64-encoded image string to S3.
    Strips the data-URL prefix if present (e.g. 'data:image/jpeg;base64,...').

    Returns:
        s3_key
    """
    import base64
    if "," in b64_string:
        header, b64_string = b64_string.split(",", 1)
        # extract content type from header if available
        if "data:" in header and ";" in header:
            content_type = header.split("data:")[1].split(";")[0]
    data = base64.b64decode(b64_string)
    return await upload_bytes(data, folder, content_type=content_type)


# ── Presigned URL ─────────────────────────────────────────────────────────────

def _sync_presign(key: str) -> str:
    return _s3_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": _BUCKET, "Key": key},
        ExpiresIn=_URL_EXPIRY,
    )


async def generate_presigned_url(s3_key: str) -> Optional[str]:
    """
    Generate a temporary presigned GET URL for a private S3 object.

    Returns None if the key is empty/None (graceful fallback).
    """
    if not s3_key:
        return None
    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(_sync_presign, s3_key))
    except (ClientError, BotoCoreError):
        return None


def generate_presigned_url_sync(s3_key: str) -> Optional[str]:
    """Synchronous version for use outside async context."""
    if not s3_key:
        return None
    try:
        return _sync_presign(s3_key)
    except (ClientError, BotoCoreError):
        return None
