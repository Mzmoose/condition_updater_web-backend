import os, base64, json
from typing import Dict
from dotenv import load_dotenv
import httpx
from cryptography.fernet import Fernet

load_dotenv()
EBAY_CLIENT_ID = os.getenv("EBAY_CLIENT_ID", "")
EBAY_CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET", "")
EBAY_REDIRECT_URI = os.getenv("EBAY_REDIRECT_URI", "")

# Production endpoints
EBAY_AUTH_URL = "https://auth.ebay.com/oauth2/authorize"
EBAY_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"

SCOPE = "https://api.ebay.com/oauth/api_scope/sell.inventory https://api.ebay.com/oauth/api_scope/sell.account"

def build_auth_url() -> str:
    params = {
        "client_id": EBAY_CLIENT_ID,
        "redirect_uri": EBAY_REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "prompt": "login",
    }
    q = "&".join(f"{k}={httpx.QueryParams({k:v})[k]}" for k,v in params.items())
    return f"{EBAY_AUTH_URL}?{q}"

async def exchange_code_for_tokens(code: str) -> Dict[str, str]:
    auth = base64.b64encode(f"{EBAY_CLIENT_ID}:{EBAY_CLIENT_SECRET}".encode()).decode()
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": EBAY_REDIRECT_URI,
    }
    headers = {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(EBAY_TOKEN_URL, data=data, headers=headers)
        resp.raise_for_status()
        return resp.json()

class TokenStore:
    """Demo token store. Replace with DB in production."""
    def __init__(self):
        key_raw = os.getenv("ENCRYPTION_KEY", "change-me"*4).encode()
        import base64 as b64
        key = b64.urlsafe_b64encode(key_raw[:32].ljust(32, b'0'))
        self.f = Fernet(key)
        self._rt = {}

    def set_refresh_token(self, user_id: str, refresh_token: str):
        self._rt[user_id] = self.f.encrypt(refresh_token.encode())

    def get_refresh_token(self, user_id: str):
        token = self._rt.get(user_id)
        return self.f.decrypt(token).decode() if token else None

    async def get_access_token_from_refresh(self, refresh_token: str) -> str:
        import base64 as b64
        auth = b64.b64encode(f"{EBAY_CLIENT_ID}:{EBAY_CLIENT_SECRET}".encode()).decode()
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": SCOPE,
        }
        headers = {
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(EBAY_TOKEN_URL, data=data, headers=headers)
            resp.raise_for_status()
            j = resp.json()
            return j.get("access_token", "")
