"""
Firebase Cloud Messaging (push notifications) helper.

Lazily initializes the Firebase Admin SDK from a service-account credential
(see app/config.py: FIREBASE_SERVICE_ACCOUNT_PATH or FIREBASE_SERVICE_ACCOUNT_JSON)
and exposes simple async-friendly helpers to push to one user's devices or a
raw list of FCM tokens.

Design notes:
  - A user can be logged in on multiple devices, so tokens are stored in their
    own `fcm_tokens` collection: {user_id, token, platform, updated_at}.
  - FCM tokens rotate/expire. When `send_multicast` reports a token as
    invalid/unregistered, we prune it from the DB so the list stays clean and
    we don't keep paying the cost of pushing to dead devices.
  - All sends are best-effort: a push failure must NEVER break the calling
    request (e.g. login should still succeed even if FCM is unreachable).
"""

import asyncio
import json
import os
from typing import Iterable, Optional

from ..config import get_settings

settings = get_settings()

_firebase_app = None
_init_attempted = False
_init_lock = asyncio.Lock()


def _build_credentials():
    """Build a firebase_admin Certificate credential from path or raw JSON."""
    from firebase_admin import credentials

    if settings.firebase_service_account_json:
        info = json.loads(settings.firebase_service_account_json)
        return credentials.Certificate(info)

    if settings.firebase_service_account_path:
        path = settings.firebase_service_account_path
        if not os.path.isabs(path):
            # Resolve relative to the backend root (fastapi/backend/backend_ec2/),
            # i.e. two levels up from this file (app/utils/fcm.py -> backend_ec2/).
            backend_root = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "..")
            )
            path = os.path.join(backend_root, path)
        if os.path.isfile(path):
            return credentials.Certificate(path)
        print(f"⚠️  FCM: service account file not found at {path}")
        return None

    return None


async def _ensure_initialized():
    """Initialize the Firebase Admin app exactly once (thread/async-safe)."""
    global _firebase_app, _init_attempted

    if _firebase_app is not None or _init_attempted:
        return _firebase_app

    async with _init_lock:
        if _firebase_app is not None or _init_attempted:
            return _firebase_app

        _init_attempted = True
        try:
            import firebase_admin

            cred = _build_credentials()
            if cred is None:
                print("⚠️  FCM: no service account configured — push notifications disabled "
                      "(set FIREBASE_SERVICE_ACCOUNT_JSON or FIREBASE_SERVICE_ACCOUNT_PATH in .env)")
                return None

            options = {"projectId": settings.firebase_project_id} if settings.firebase_project_id else None
            _firebase_app = firebase_admin.initialize_app(cred, options) if options else firebase_admin.initialize_app(cred)
            print("✅ Firebase Admin SDK initialized — push notifications enabled")
        except ImportError:
            print("⚠️  firebase-admin package not installed. Add it to requirements.txt")
        except Exception as exc:  # noqa: BLE001 — never let push setup crash the API
            print(f"⚠️  FCM initialization failed: {exc}")

    return _firebase_app


async def send_push_to_tokens(
    tokens: Iterable[str],
    title: str,
    body: str,
    data: Optional[dict] = None,
) -> list[str]:
    """
    Send a push to a raw list of FCM device tokens.

    Returns the subset of tokens that were rejected as invalid/unregistered —
    callers should remove these from storage.
    Safe to call even if Firebase isn't configured (no-ops, returns []).
    """
    tokens = [t for t in tokens if t]
    if not tokens:
        return []

    app = await _ensure_initialized()
    if app is None:
        return []

    try:
        from firebase_admin import messaging
    except ImportError:
        return []

    # Stringify data values — FCM requires a Map<String, String>.
    str_data = {str(k): str(v) for k, v in (data or {}).items()}

    message = messaging.MulticastMessage(
        notification=messaging.Notification(title=title, body=body),
        data=str_data,
        tokens=tokens,
        android=messaging.AndroidConfig(
            priority="high",
            notification=messaging.AndroidNotification(channel_id="claimit_default_channel"),
        ),
        apns=messaging.APNSConfig(
            payload=messaging.APNSPayload(aps=messaging.Aps(sound="default"))
        ),
    )

    def _send():
        return messaging.send_each_for_multicast(message)

    try:
        response = await asyncio.to_thread(_send)
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️  FCM send failed: {exc}")
        return []

    invalid_tokens: list[str] = []
    for idx, result in enumerate(response.responses):
        if result.success:
            continue
        code = getattr(getattr(result, "exception", None), "code", "")
        # Prune tokens Firebase says are dead — anything else (rate limits,
        # transient network errors) we leave alone and just retry next time.
        if code in ("UNREGISTERED", "INVALID_ARGUMENT", "NOT_FOUND"):
            invalid_tokens.append(tokens[idx])

    if invalid_tokens:
        print(f"🧹 FCM: pruning {len(invalid_tokens)} invalid token(s)")

    return invalid_tokens


async def send_push_to_user(
    db,
    user_id: str,
    title: str,
    body: str,
    data: Optional[dict] = None,
) -> None:
    """
    Look up every device token registered for `user_id`, push to all of them,
    and prune any the SDK reports as dead. Fully best-effort / non-throwing.
    """
    try:
        cursor = db.fcm_tokens.find({"user_id": user_id})
        docs = await cursor.to_list(length=50)
        tokens = [d["token"] for d in docs if d.get("token")]
        if not tokens:
            return

        invalid = await send_push_to_tokens(tokens, title, body, data)
        if invalid:
            await db.fcm_tokens.delete_many({"user_id": user_id, "token": {"$in": invalid}})
    except Exception as exc:  # noqa: BLE001 — pushing must never break the caller
        print(f"⚠️  send_push_to_user failed for user {user_id}: {exc}")
