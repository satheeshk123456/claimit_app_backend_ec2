"""
Vercel-ready FastAPI entry point.

Key differences from the local dev version:
  • No StaticFiles mount  — Vercel has no persistent filesystem.
    Avatars/images are stored as base64 in MongoDB (classifieds already do this).
    For claim documents / shop images, use an external service (Cloudinary, S3)
    or store base64 in MongoDB — swap out the relevant routes when needed.
  • No os.makedirs calls.
  • CORS allow_origins is locked to your Flutter app's production domain.
    During development keep ["*"] or add your local IP.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .database import connect_db, disconnect_db, get_db
from .routes import (
    auth, users, claims, notifications, dashboard,
    policies, locations, deals, shops, rewards,
    redeem, reels, classifieds, admin, bill, banners,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db()
    yield
    await disconnect_db()


app = FastAPI(
    title="Claimit API",
    description="Claimit — loyalty, classifieds & claims backend",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)


# ── Vercel cold-start guard ───────────────────────────────────────────────────
# Vercel serverless may skip the lifespan startup hook on cold starts.
# This middleware ensures the DB is always connected before any request.
@app.middleware("http")
async def ensure_db_connected(request: Request, call_next):
    if get_db() is None:
        await connect_db()
    return await call_next(request)

# ── CORS ─────────────────────────────────────────────────────────────────────
# Flutter mobile apps send Bearer tokens (no cookies), so credentials=False
# is correct.  Replace "*" with your production domain when you have one,
# e.g. ["https://claimit.vercel.app"] or your custom domain.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(claims.router)
app.include_router(notifications.router)
app.include_router(dashboard.router)
app.include_router(policies.router)
app.include_router(locations.router)
app.include_router(deals.router)
app.include_router(shops.router)
app.include_router(rewards.router)
app.include_router(redeem.router)
app.include_router(reels.router)
app.include_router(classifieds.router)
app.include_router(admin.router)
app.include_router(bill.router)
app.include_router(banners.router)


# ── Root / health ─────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {
        "app": "Claimit API",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    return {"status": "healthy"}


# ── Global error handler ──────────────────────────────────────────────────────
@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal server error: {str(exc)}"},
    )
