from fastapi import APIRouter, Depends
from ..database import get_db
from ..utils.auth import get_current_user

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("")
async def get_dashboard(current_user: dict = Depends(get_current_user)):
    db = get_db()
    user_id = current_user.get("_id") or current_user.get("id")

    pipeline = [
        {"$match": {"user_id": user_id}},
        {
            "$group": {
                "_id": None,
                "total_claims": {"$sum": 1},
                "total_claim_amount": {"$sum": "$claim_amount"},
                "approved_amount": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$status", "approved"]},
                            {"$ifNull": ["$approved_amount", "$claim_amount"]},
                            0,
                        ]
                    }
                },
                "pending_claims": {
                    "$sum": {"$cond": [{"$eq": ["$status", "pending"]}, 1, 0]}
                },
                "under_review_claims": {
                    "$sum": {"$cond": [{"$eq": ["$status", "under_review"]}, 1, 0]}
                },
                "approved_claims": {
                    "$sum": {"$cond": [{"$eq": ["$status", "approved"]}, 1, 0]}
                },
                "rejected_claims": {
                    "$sum": {"$cond": [{"$eq": ["$status", "rejected"]}, 1, 0]}
                },
                "settled_claims": {
                    "$sum": {"$cond": [{"$eq": ["$status", "settled"]}, 1, 0]}
                },
            }
        },
    ]

    result = await db.claims.aggregate(pipeline).to_list(length=1)

    if result:
        stats = result[0]
        stats.pop("_id", None)
    else:
        stats = {
            "total_claims": 0,
            "total_claim_amount": 0,
            "approved_amount": 0,
            "pending_claims": 0,
            "under_review_claims": 0,
            "approved_claims": 0,
            "rejected_claims": 0,
            "settled_claims": 0,
        }

    recent_cursor = db.claims.find({"user_id": user_id}).sort(
        "submitted_at", -1
    ).limit(5)
    recent_claims = await recent_cursor.to_list(length=5)

    from ..utils.helpers import serialize_doc
    stats["recent_claims"] = [serialize_doc(c) for c in recent_claims]

    return stats
