"""
EC2 entry point.
  python run.py
Or:
  uvicorn app.main:app --host 0.0.0.0 --port 8001 --workers 2 --reload
"""
import os
import uvicorn

if __name__ == "__main__":
    host    = os.getenv("HOST", "0.0.0.0")
    port    = int(os.getenv("PORT", "8001"))
    workers = int(os.getenv("WORKERS", "2"))
    reload  = os.getenv("RELOAD", "false").lower() == "true"

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        workers=workers,
        reload=reload,
        log_level="info",
    )
