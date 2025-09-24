import os, time
from fastapi import APIRouter
from app.services.auth import auto_refresh_if_needed, get_last_error, get_debug_info

router = APIRouter(prefix="/auth", tags=["auth"])

@router.get("/debug")
def auth_debug():
    access = auto_refresh_if_needed()
    return {
        "access_ok": bool(access),
        "last_error": get_last_error(),
        "debug": get_debug_info(),
        "ts": int(time.time()),
    }
