"""
notify_user — single entry point for "tell a user something".

Does TWO things together so the in-app notification list and the system push
popup never drift apart:
  1. Inserts a row into `db.notifications` (powers GET /notifications, the
     bell icon / unread badge in the app).
  2. Sends a real FCM push to every device the user is logged in on (powers
     the system popup — works even when the app is backgrounded/killed).

Use this anywhere the backend needs to notify a user: login success, claim
status changes, offers, reminders, etc. — instead of writing to
`db.notifications` directly.
"""

from datetime import datetime
from typing import Optional

from .fcm import send_push_to_user


async def notify_user(
    db,
    user_id: str,
    title: str,
    message: str,
    type: str = "info",  # info | success | warning | error | alert | offer | reminder
    claim_id: Optional[str] = None,
    data: Optional[dict] = None,
) -> dict:
    """Persist an in-app notification AND fire a push to the user's devices."""
    doc = {
        "user_id": user_id,
        "title": title,
        "message": message,
        "type": type,
        "is_read": False,
        "claim_id": claim_id,
        "created_at": datetime.utcnow(),
    }
    result = await db.notifications.insert_one(doc)
    doc["_id"] = result.inserted_id

    push_data = {"type": type, "notification_id": str(result.inserted_id)}
    if claim_id:
        push_data["claim_id"] = claim_id
    if data:
        push_data.update(data)

    # Fire-and-forget-ish: awaited, but internally swallows all errors so a
    # push outage can never fail the calling request (login, claim update, …).
    await send_push_to_user(db, user_id, title, message, push_data)

    return doc
