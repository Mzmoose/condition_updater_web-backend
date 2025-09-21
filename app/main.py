import logging, asyncio, os, json
from fastapi import FastAPI, Request, HTTPException, UploadFile, File
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from .oauth import (
    build_auth_url,
    exchange_code_for_tokens,
    refresh_tokens,
    auto_refresh_if_needed,
    TokenStore,
)
from .utils import parse_skus_from_xlsx

logger = logging.getLogger("uvicorn.error")

app = FastAPI(title="Condition Updater Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def _start_refresher():
    async def loop():
        while True:
            try:
                refreshed = await auto_refresh_if_needed()
                if refreshed:
                    logger.info("Access token auto-refreshed")
            except Exception as e:
                logger.exception("Auto-refresh failed: %s", e)
            await asyncio.sleep(300)
    asyncio.create_task(loop())

@app.get("/")
def root():
    return RedirectResponse("/docs")

@app.get("/healthz")
def healthz():
    return {"ok": True}

@app.get("/oauth/login")
def oauth_login():
    url = build_auth_url()
    logger.info("Redirecting to eBay auth: %s", url)
    return RedirectResponse(url)

@app.get("/oauth/callback")
async def oauth_callback(request: Request):
    q = dict(request.query_params)
    if "error" in q:
        return JSONResponse({"status": "error", "error": q.get("error"), "error_description": q.get("error_description")})
    code = q.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="Missing 'code' in callback")
    token_data = await exchange_code_for_tokens(code)
    return {"status": "ok", "message": "Tokens stored"}

@app.get("/oauth/token/status")
def token_status():
    data = TokenStore.get() or {}
    return {
        "has_token": bool(data),
        "token_type": data.get("token_type"),
        "expires_in": data.get("expires_in"),
        "access_expires_at": data.get("access_expires_at"),
        "refresh_token": bool(data.get("refresh_token")),
        "refresh_expires_at": data.get("refresh_expires_at"),
        "scope": data.get("scope"),
    }

@app.post("/oauth/refresh")
async def oauth_refresh():
    tokens = await refresh_tokens()
    return {"status": "ok", "refreshed": True, "expires_in": tokens.get("expires_in")}

@app.post("/condition/update")
async def condition_update(file: UploadFile = File(...)):
    content = await file.read()
    skus = parse_skus_from_xlsx(content)
    return {"received_skus": len(skus)}

# ---- debug helper to inspect the token file on disk ----
@app.get("/debug/token-file")
def debug_token_file():
    path = os.getenv("TOKEN_PATH", "/data/tokens.json")
    exists = os.path.exists(path)
    size = os.path.getsize(path) if exists else None
    # auto-load tokens from disk if memory is empty
    if exists and not TokenStore.get():
        with open(path) as f:
            TokenStore.save(json.load(f))
    return {"path": path, "exists": exists, "size": size}
