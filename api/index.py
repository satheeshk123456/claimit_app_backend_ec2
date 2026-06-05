"""
Vercel serverless entry point.

Vercel looks for a callable named `app` (or any ASGI app) in api/index.py.
We simply re-export the FastAPI app from our app package.
"""

import sys
import os

# Make sure `app/` is importable from this file's location
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app  # noqa: F401  — Vercel picks this up automatically
