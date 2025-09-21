import os
import base64
from urllib.parse import urlencode, quote
import httpx

# --- config helpers ---
def _env(name: str, required: bool = True) -> str:
    v = os.getenv(name, "").strip()
    if required and not v:
        raise RuntimeError(f"Missing env: {name}")
    return v

def _auth_base() -> str:
    env = os.getenv("EBAY_ENV", "PRODUCTION").upper()
    return "https://auth.ebay.com/oauth2/authorize" if env == "PRODUCTION" else "https://auth.sandbox.ebay.com/oauth2/authorize"

def _token_base() -> str:
    env = os.getenv("EBAY_ENV", "PRODUCTION").upper()
    return "https://api.ebay.com/identity/v1/oauth2/token" if env == "PRODUCTION" else "https://api.sandbox.ebay.com/identity/v1/oauth2/token"

# --- public API used by main.py ---
def build_auth_url() -> str:
    client_id = _env("EBAY_CLIENT_ID")
    runame = _env("EBAY_REDIRECT_URI")  # this MUST be the RuName string
    scopes = os.getenv("EBAY_SCOPES", "https://api.ebay.com/oauth/api_scope").strip()
    params = {
        "client_id": client_id,
        "redirect_uri": runame,                 # RuName, NOT https URL
        "response_type": "code",
        "scope": scopes,                        # space-delimited allowed
        "state": "cond-updater",
    }
    return _auth_base() + "?" + urlencode(params, quote_via=quote)

async def exchange_code_for_tokens(code: str) -> dict:
    client_id = _env("EBAY_CLIENT_ID")
    client_secret = _env("EBAY_CLIENT_SECRET")
    runame = _env("EBAY_REDIRECT_URI")

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": runame,         # must echo the SAME RuName
    }
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {basic}",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(_token_base(), data=data, headers=headers)
        r.raise_for_status()
        return r.json()

class TokenStore:
    _data: dict | None = None

    @classmethod
    def save(cls, token_json: dict) -> None:
        cls._data = token_json

    @classmethod
    def get(cls) -> dict | None:
        return cls._data
