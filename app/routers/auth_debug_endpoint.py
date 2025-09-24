import os, time
from fastapi import APIRouter
from app.services.auth import auto_refresh_if_needed

router = APIRouter(prefix="/auth", tags=["auth"])

@router.get("/debug")
def auth_debug():
    access = auto_refresh_if_needed()
    return {
        "access_ok": bool(access),
        "env": {
            "EBAY_CLIENT_ID": bool(os.getenv("EBAY_CLIENT_ID")),
            "EBAY_CLIENT_SECRET": bool(os.getenv("EBAY_CLIENT_SECRET")),
            "EBAY_REFRESH_TOKEN": bool(os.getenv("EBAY_REFRESH_TOKEN")),
            "EBAY_SCOPES": bool(os.getenv("EBAY_SCOPES")),
        },
        "ts": int(time.time()),
    }
