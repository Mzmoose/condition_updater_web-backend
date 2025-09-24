import os, time
from fastapi import APIRouter
from app.services.auth import auto_refresh_if_needed, get_last_error

router = APIRouter(prefix="/auth", tags=["auth"])

def _first_key(*keys):
    for k in keys:
        v = os.getenv(k, "")
        if v:
            return k
    return ""

@router.get("/debug")
def auth_debug():
    access = auto_refresh_if_needed()
    k_tokens = _first_key("TOKENS_FILE", "TOKEN_PATH")
    k_id = _first_key("EBAY_CLIENT_ID", "EBAY_APP_ID", "APP_ID")
    k_secret = _first_key("EBAY_CLIENT_SECRET", "EBAY_CERT_ID", "CERT_ID")
    k_refresh = _first_key("EBAY_REFRESH_TOKEN", "EBAY_REFRESH_TOKEN_PROD", "REFRESH_TOKEN")
    k_scopes = _first_key("EBAY_SCOPES")
    return {
        "access_ok": bool(access),
        "last_error": get_last_error(),
        "debug": {
            "chosen_keys": {
                "TOKENS_FILE": k_tokens or "default",
                "EBAY_CLIENT_ID": k_id,
                "EBAY_CLIENT_SECRET": k_secret,
                "EBAY_REFRESH_TOKEN": k_refresh,
                "EBAY_SCOPES": k_scopes or "EBAY_SCOPES",
            },
            "present": {
                "TOKENS_FILE": bool(os.getenv(k_tokens) if k_tokens else True),
                "EBAY_CLIENT_ID": bool(os.getenv(k_id)),
                "EBAY_CLIENT_SECRET": bool(os.getenv(k_secret)),
                "EBAY_REFRESH_TOKEN": bool(os.getenv(k_refresh)),
                "EBAY_SCOPES": bool(os.getenv("EBAY_SCOPES", "")),
            },
        },
        "ts": int(time.time()),
    }
