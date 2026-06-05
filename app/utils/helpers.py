import random
import string
from datetime import datetime
from bson import ObjectId


def generate_claim_number() -> str:
    """Generate a unique claim number."""
    year = datetime.utcnow().year
    random_part = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    return f"CLM-{year}-{random_part}"


def serialize_doc(doc: dict) -> dict:
    """Convert MongoDB document to JSON-serializable format."""
    if doc is None:
        return None
    result = {}
    for key, value in doc.items():
        if isinstance(value, ObjectId):
            result[key] = str(value)
        elif isinstance(value, datetime):
            result[key] = value.isoformat()
        elif isinstance(value, list):
            result[key] = [
                serialize_doc(item) if isinstance(item, dict) else
                str(item) if isinstance(item, ObjectId) else
                item.isoformat() if isinstance(item, datetime) else item
                for item in value
            ]
        elif isinstance(value, dict):
            result[key] = serialize_doc(value)
        else:
            result[key] = value
    # Rename _id to id
    if "_id" in result:
        result["id"] = result.pop("_id")
    return result


def paginate(page: int = 1, page_size: int = 10) -> dict:
    """Calculate skip and limit for pagination."""
    skip = (page - 1) * page_size
    return {"skip": skip, "limit": page_size}
