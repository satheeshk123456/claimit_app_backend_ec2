from fastapi import APIRouter

router = APIRouter(prefix="/locations", tags=["Locations"])

_POPULAR_LOCATIONS = [
    {"name": "Anna Nagar", "count": 24},
    {"name": "Thoraipakkam", "count": 23},
    {"name": "Solinganallur", "count": 41},
    {"name": "OMR", "count": 12},
    {"name": "Guindy", "count": 2},
    {"name": "Padi", "count": 30},
    {"name": "Nungambakkam", "count": 27},
    {"name": "Kotturpuram", "count": 25},
    {"name": "Velachery", "count": 22},
    {"name": "Besant Nagar", "count": 30},
    {"name": "Kodambakkam", "count": 29},
    {"name": "Thiruvanmiyur", "count": 28},
    {"name": "Mylapore", "count": 35},
    {"name": "Choolaimedu", "count": 21},
    {"name": "Sholinganallur", "count": 24},
]


@router.get("/popular")
async def get_popular_locations():
    return {"locations": _POPULAR_LOCATIONS}
