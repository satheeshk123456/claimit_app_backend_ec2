"""
EC2 / persistent-process MongoDB connection.
The process stays alive so we connect once on startup and reuse the connection.
"""
from motor.motor_asyncio import AsyncIOMotorClient
from .config import get_settings

settings = get_settings()

_client: AsyncIOMotorClient = None
_db = None


async def connect_db() -> None:
    global _client, _db

    _client = AsyncIOMotorClient(settings.mongodb_url)
    _db = _client[settings.database_name]

    # ── Fix legacy empty-string credentials that break sparse unique indexes ──
    await _db.users.update_many(
        {"phone": {"$in": ["", None]}, "email": {"$exists": True, "$nin": ["", None]}},
        {"$unset": {"phone": ""}},
    )
    await _db.users.update_many(
        {"email": {"$in": ["", None]}, "phone": {"$exists": True, "$nin": ["", None]}},
        {"$unset": {"email": ""}},
    )

    # ── Users: phone index sparse+unique ─────────────────────────────────────
    indexes = await _db.users.index_information()
    phone_idx = indexes.get("phone_1")
    if phone_idx is not None and not phone_idx.get("sparse", False):
        await _db.users.drop_index("phone_1")
        phone_idx = None
    if phone_idx is None:
        await _db.users.create_index("phone", unique=True, sparse=True)

    # ── Users: email index sparse (not unique — enforced at app layer) ───────
    await _db.users.create_index("email", sparse=True)

    # ── All other indexes ─────────────────────────────────────────────────────
    await _db.claims.create_index("user_id")
    await _db.claims.create_index("claim_number", unique=True)
    await _db.notifications.create_index("user_id")
    await _db.fcm_tokens.create_index("user_id")
    await _db.fcm_tokens.create_index("token", unique=True)
    await _db.otp_store.create_index("phone")
    await _db.otp_store.create_index("expires_at", expireAfterSeconds=0)
    await _db.shops.create_index("category_ids")
    await _db.shops.create_index("name")
    await _db.deals.create_index("deal_group")
    await _db.deals.create_index("category")
    await _db.rewards.create_index("shop_id")
    await _db.rewards.create_index("is_active")
    await _db.rewards.create_index("expires_at")
    await _db.redeem.create_index("user_id")
    await _db.redeem.create_index("reward_id")
    await _db.redeem.create_index([("user_id", 1), ("reward_id", 1)])
    await _db.reels.create_index("shop_id")
    await _db.banners.create_index("status")
    await _db.banners.create_index("created_at")
    await _db.classifieds.create_index("category")
    await _db.classifieds.create_index("subcategory")
    await _db.classifieds.create_index("pincode")
    await _db.user_wallets.create_index("user_id", unique=True)
    await _db.bill_scans.create_index("user_id")
    await _db.bill_scans.create_index([("user_id", 1), ("dup_key", 1)], unique=True)
    await _db.bill_scans.create_index("scanned_at", expireAfterSeconds=86400)

    print("✅ Connected to MongoDB")


async def disconnect_db() -> None:
    global _client, _db
    if _client:
        _client.close()
        _client = None
        _db = None
        print("❌ Disconnected from MongoDB")


def get_db():
    return _db
