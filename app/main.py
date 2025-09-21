import logging, asyncio, os, json
from typing import List, Dict, Any
from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Body
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx

from .oauth import (
    build_auth_url,
    exchange_code_for_tokens,
    refresh_tokens,
    auto_refresh_if_needed,
    TokenStore,
)
from .utils import parse_skus_from_xlsx  # expects a list of dicts with at least {"sku": "...", "condition": "...", "conditionDescription": "..."}

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

# ---------- OAuth ----------
@app.get("/oauth/login")
def oauth_login():
    url = build_auth_url()
    logger.info("Redirecting to eBay auth: %s", url)
    return RedirectResponse(url)

@app.get("/oauth/login/url")
def oauth_login_url():
    url = build_auth_url()
    return {"url": url}

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

# ---------- Inventory helpers ----------
EBAY_INV_BASE = "https://api.ebay.com/sell/inventory/v1"

def _auth_header() -> Dict[str, str]:
    tok = TokenStore.get() or {}
    access = tok.get("access_token")
    if not access:
        raise HTTPException(status_code=400, detail="No access_token; run /oauth/login")
    return {"Authorization": f"Bearer {access}", "Accept": "application/json", "Content-Type": "application/json"}

async def _get_inventory_item(sku: str) -> Dict[str, Any]:
    url = f"{EBAY_INV_BASE}/inventory_item/{sku}"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url, headers=_auth_header())
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail=f"SKU {sku} not found")
    r.raise_for_status()
    return r.json()

async def _put_inventory_item(sku: str, body: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{EBAY_INV_BASE}/inventory_item/{sku}"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.put(url, headers=_auth_header(), content=json.dumps(body))
    try:
        data = r.json()
    except Exception:
        data = {"text": r.text[:1000]}
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=data)
    return data

# ---------- Inventory: ping ----------
@app.get("/inventory/ping")
async def inventory_ping():
    headers = _auth_header()
    url = f"{EBAY_INV_BASE}/inventory_item"
    params = {"limit": "1"}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url, headers=headers, params=params)
    try:
        body = r.json()
    except Exception:
        body = r.text[:1000]
    return {"status": r.status_code, "ok": r.status_code < 400, "data": body}

# ---------- Inventory: update condition (single) ----------
@app.post("/inventory/condition/update")
async def inventory_condition_update(payload: Dict[str, Any] = Body(...)):
    sku = str(payload.get("sku", "")).strip()
    condition = str(payload.get("condition", "")).strip() or None
    condition_desc = str(payload.get("conditionDescription", "")).strip() or None
    if not sku:
        raise HTTPException(status_code=400, detail="Missing sku")

    current = await _get_inventory_item(sku)

    # merge into current body
    body = current
    if condition is not None:
        body["condition"] = condition
    if condition_desc is not None:
        body["conditionDescription"] = condition_desc

    updated = await _put_inventory_item(sku, body)
    return {"status": "ok", "sku": sku, "condition": body.get("condition"), "conditionDescription": body.get("conditionDescription"), "result": updated}

# ---------- Inventory: batch update from XLSX ----------
@app.post("/inventory/condition/update-batch")
async def inventory_condition_update_batch(file: UploadFile = File(...)):
    content = await file.read()
    rows = parse_skus_from_xlsx(content)  # expected keys: sku, condition, conditionDescription (case-insensitive ok if your parser normalizes)
    results: List[Dict[str, Any]] = []
    for row in rows:
        sku = str(row.get("sku", "")).strip()
        if not sku:
            results.append({"sku": None, "ok": False, "error": "missing sku"})
            continue
        try:
            cur = await _get_inventory_item(sku)
            body = cur
            if "condition" in row and str(row["condition"]).strip():
                body["condition"] = str(row["condition"]).strip()
            if "conditionDescription" in row and str(row["conditionDescription"]).strip():
                body["conditionDescription"] = str(row["conditionDescription"]).strip()
            put = await _put_inventory_item(sku, body)
            results.append({"sku": sku, "ok": True, "condition": body.get("condition"), "conditionDescription": body.get("conditionDescription"), "result": put})
        except HTTPException as e:
            results.append({"sku": sku, "ok": False, "status": e.status_code, "error": e.detail})
        except Exception as e:
            results.append({"sku": sku, "ok": False, "error": str(e)})
    ok_count = sum(1 for r in results if r.get("ok"))
    return {"updated": ok_count, "total": len(results), "results": results}

# ---------- Debug ----------
@app.get("/debug/token-file")
def debug_token_file():
    path = os.getenv("TOKEN_PATH", "/data/tokens.json")
    exists = os.path.exists(path)
    size = os.path.getsize(path) if exists else None
    if exists and not TokenStore.get():
        with open(path) as f:
            TokenStore.save(json.load(f))
    return {"path": path, "exists": exists, "size": size}
