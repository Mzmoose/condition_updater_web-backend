import logging, asyncio
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

from .oauth import (
    build_auth_url,
    exchange_code_for_tokens,
    refresh_tokens,
    auto_refresh_if_needed,
    TokenStore,
)

from .tools.condition_updater import router as condition_router
from .tools.bulk_downloader import router as bulk_router

logger = logging.getLogger("uvicorn.error")

app = FastAPI(title="Seller Tools Hub")

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
                if await auto_refresh_if_needed():
                    logger.info("Access token auto-refreshed")
            except Exception:
                logger.exception("Auto-refresh failed")
            await asyncio.sleep(300)
    asyncio.create_task(loop())

@app.get("/")
def root():
    return RedirectResponse("/tools")

@app.get("/tools", response_class=HTMLResponse)
def tools_home():
    return """
    <html><body>
      <h2>Tool Hub</h2>
      <ul>
        <li><a href="/trading/condition/update-batch/form">Condition Updater (Scheduled)</a></li>
        <li><a href="/bulk-download/form">Bulk Downloader</a></li>
      </ul>
    </body></html>
    """

@app.get("/healthz")
def healthz():
    return {"ok": True}

@app.get("/oauth/login")
def oauth_login():
    return RedirectResponse(build_auth_url())

@app.get("/oauth/login/url")
def oauth_login_url():
    return {"url": build_auth_url()}

@app.get("/oauth/callback")
async def oauth_callback(request: Request):
    q = dict(request.query_params)
    if "error" in q:
        return JSONResponse({"status": "error", "error": q.get("error"), "error_description": q.get("error_description")})
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

@app.get("/debug/token-file")
def debug_token_file():
    path = "/data/tokens.json"
    import os
    return {"path": path, "exists": os.path.exists(path), "size": (os.path.getsize(path) if os.path.exists(path) else None)}

app.include_router(condition_router)
app.include_router(bulk_router)
