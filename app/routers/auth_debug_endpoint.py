import os, time
from fastapi import APIRouter
from app.services.auth import auto_refresh_if_needed, get_last_error

router = APIRouter(prefix="/auth", tags=["auth"])

@router.get("/debug")
def auth_debug():
    access = auto_refresh_if_needed()
    return {
        "access_ok": bool(access),
        "last_error": get_last_error(),
        "env": {
            "EBAY_CLIENT_ID": bool(os.getenv("EBAY_CLIENT_ID")),
            "EBAY_CLIENT_SECRET": bool(os.getenv("EBAY_CLIENT_SECRET")),
            "EBAY_REFRESH_TOKEN": bool(os.getenv("EBAY_REFRESH_TOKEN")),
            "EBAY_SCOPES": bool(os.getenv("EBAY_SCOPES")),
            "EBAY_SCOPES_value": os.getenv("EBAY_SCOPES", ""),
            "TOKENS_FILE": os.getenv("TOKENS_FILE", ""),
        },
        "ts": int(time.time()),
    }
