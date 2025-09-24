# app/main.py
import logging
import asyncio
import os
import json

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from .oauth import (
    build_auth_url,
    exchange_code_for_tokens,
    refresh_tokens,
    auto_refresh_if_needed,
    TokenStore,
)
from .condition_update_endpoint import router as condition_router
from .ub_monitor_endpoint import router as ub_monitor_router
from .bulk_ui import router as bulk_ui_router

logger = logging.getLogger("uvicorn.error")

app = FastAPI(title="Condition Updater Backend")
app.include_router(bulk_ui_router)
from .routers.bulk_photos_endpoint import router as bulk_router
app.include_router(bulk_router)
from .routers.pushover_test import router as pushover_test_router
app.include_router(pushover_test_router)

# CORS (open while you iterate; tighten later if needed)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Background auto-refresh of OAuth tokens
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
            await asyncio.sleep(300)  # 5 minutes
    asyncio.create_task(loop())

@app.get("/")
def root():
    return RedirectResponse("/docs")

@app.get("/healthz")
def healthz():
    return {"ok": True}

# ---------- OAuth flow ----------
@app.get("/oauth/login")
def oauth_login():
    url = build_auth_url()
    logger.info("Redirecting to eBay auth: %s", url)
    return RedirectResponse(url)

@app.get("/oauth/callback")
async def oauth_callback(request: Request):
    q = dict(request.query_params)
    if "error" in q:
        return JSONResponse(
            {"status": "error", "error": q.get("error"), "error_description": q.get("error_description")}
        )
    code = q.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="Missing 'code' in callback")
    await exchange_code_for_tokens(code)
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

# Debug helper to confirm the disk token file wiring
@app.get("/debug/token-file")
def debug_token_file():
    path = os.getenv("TOKEN_PATH", "/data/tokens.json")
    exists = os.path.exists(path)
    size = os.path.getsize(path) if exists else None
    # If file exists but in-memory store is empty, load it
    if exists and not TokenStore.get():
        with open(path) as f:
            TokenStore.save(json.load(f))
    return {"path": path, "exists": exists, "size": size}

# ---------- Feature routers ----------
# Trading / condition updater endpoints
app.include_router(condition_router)

# UB Monitor endpoints
app.include_router(ub_monitor_router)
