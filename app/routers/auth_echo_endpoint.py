import os, hashlib, time
from fastapi import APIRouter

router = APIRouter(prefix="/auth", tags=["auth"])

def _h(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def _mask(s: str) -> str:
    if not s: return ""
    return f"{s[:6]}…{s[-6:]}"

@router.get("/echo")
def auth_echo():
    cid = os.getenv("EBAY_CLIENT_ID", "")
    sec = os.getenv("EBAY_CLIENT_SECRET", "")
    rtk = os.getenv("EBAY_REFRESH_TOKEN", "")
    scopes = os.getenv("EBAY_SCOPES", "")
    tf = os.getenv("TOKENS_FILE", "")
    return {
        "client_id_mask": _mask(cid),
        "client_secret_mask": _mask(sec),
        "refresh_token_mask": _mask(rtk),
        "pair_sha256": _h(f"{cid}:{sec}") if cid and sec else "",
        "token_sha256": _h(rtk) if rtk else "",
        "scopes": scopes,
        "tokens_file": tf,
        "has_trailing_space": {
            "client_id": cid != cid.strip(),
            "client_secret": sec != sec.strip(),
            "refresh_token": rtk != rtk.strip(),
        },
        "ts": int(time.time()),
    }
