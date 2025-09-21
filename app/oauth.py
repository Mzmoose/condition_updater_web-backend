import os
import base64
import time
from typing import Optional, Dict, Any
from urllib.parse import urlencode, quote
import httpx

# ---------- env & endpoints ----------
def _env(name: str, required: bool = True) -> str:
    v = os.getenv(name, "").strip()
    if required and not v:
        raise RuntimeError(f"Missing env: {name}")
    return v

def _auth_base() -> str:
    return "https://auth.ebay.com/oauth2/authorize" if os.getenv("EBAY_ENV", "PRODUCTION").upper() == "PRODUCTION" \
        else "https://auth.sandbox.ebay.com/oauth2/authorize"

def _token_base() -> str:
    return "https://api.ebay.com/identity/v1/oauth2/token" if os.getenv("EBAY_ENV", "PRODUCTION").upper() == "PRODUCTION" \
        else "https://api.sandbox.ebay.com/identity/v1/oauth2/token"

def _basic_header() -> str:
    cid = _env("EBAY_CLIENT_ID")
    csec = _env("EBAY_CLIENT_SECRET")
    return "Basic " + base64.b64encode(f"{cid}:{csec}".encode()).decode()

# ---------- public API ----------
def build_auth_url() -> str:
    client_id = _env("EBAY_CLIENT_ID")
    runame = _env("EBAY_REDIRECT_URI")  # RuName string
    scopes = os.getenv("EBAY_SCOPES", "https://api.ebay.com/oauth/api_scope").strip()
    params = {
        "client_id": client_id,
        "redirect_uri": runame,
        "response_type": "code",
        "scope": scopes,
        "state": "cond-updater",
    }
    return _auth_base() + "?" + urlencode(params, quote_via=quote)

async def exchange_code_for_tokens(code: str) -> Dict[str, Any]:
    runame = _env("EBAY_REDIRECT_URI")
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": runame,
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": _basic_header(),
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(_token_base(), data=data, headers=headers)
        r.raise_for_status()
        tokens = r.json()
        _annotate_expiry(tokens)
        TokenStore.save(tokens)
        return tokens

async def refresh_tokens() -> Dict[str, Any]:
    cur = TokenStore.get()
    if not cur or not cur.get("refresh_token"):
        raise RuntimeError("No refresh_token available. Re-run /oauth/login to obtain new consent.")
    data = {
        "grant_type": "refresh_token",
        "refresh_token": cur["refresh_token"],
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": _basic_header(),
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(_token_base(), data=data, headers=headers)
        r.raise_for_status()
        tokens = r.json()
        # Preserve existing refresh_token if eBay doesn't return a new one
        if "refresh_token" not in tokens:
            tokens["refresh_token"] = cur.get("refresh_token")
        _annotate_expiry(tokens)
        TokenStore.save(tokens)
        return tokens

# ---------- helpers ----------
def _annotate_expiry(tok: Dict[str, Any]) -> None:
    now = int(time.time())
    if "expires_in" in tok:
        tok["access_expires_at"] = now + int(tok["expires_in"])
    if "refresh_token_expires_in" in tok:
        tok["refresh_expires_at"] = now + int(tok["refresh_token_expires_in"])

class TokenStore:
    _data: Optional[Dict[str, Any]] = None

    @classmethod
    def save(cls, token_json: Dict[str, Any]) -> None:
        cls._data = token_json

    @classmethod
    def get(cls) -> Optional[Dict[str, Any]]:
        return cls._data
