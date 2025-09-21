import logging, asyncio, os, json, io, re
from typing import List, Dict, Any
from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Body
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
from openpyxl import load_workbook

from .oauth import (
    build_auth_url,
    exchange_code_for_tokens,
    refresh_tokens,
    auto_refresh_if_needed,
    TokenStore,
)

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

# ---------- XLSX parser (robust) ----------
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(s).strip().lower())

def _parse_rows_from_ws(ws) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    headers = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        vals = [("" if v is None else str(v)) for v in row]
        if i == 0:
            headers = [_norm(h) for h in vals]
            continue
        if not any(v.strip() for v in vals):
            continue
        rec = {headers[j] if j < len(headers) else f"col{j}": vals[j].strip() if j < len(vals) else "" for j in range(max(len(headers), len(vals)))}
        # map flexible header variants
        sku = rec.get("sku") or rec.get("itemsku") or rec.get("customlabel") or rec.get("id")
        cond = rec.get("condition") or rec.get("itemcondition") or rec.get("cond") or rec.get("conditionid")
        conddesc = rec.get("conditiondescription") or rec.get("condition_desc") or rec.get("conditiontext") or rec.get("conddesc")
        if sku and sku.strip():
            rows.append({"sku": sku.strip(), "condition": (cond or "").strip(), "conditionDescription": (conddesc or "").strip()})
    return rows

def parse_skus_from_xlsx(binary: bytes) -> List[Dict[str, Any]]:
    wb = load_workbook(io.BytesIO(binary), read_only=True, data_only=True)
    rows: List[Dict[str, Any]] = []
    # parse active sheet first; if empty, try all sheets
    try:
        rows = _parse_rows_from_ws(wb.active)
    except Exception:
        rows = []
    if not rows:
        for name in wb.sheetnames:
            try:
                rs = _parse_rows_from_ws(wb[name])
                if rs:
                    rows.extend(rs)
            except Exception:
                continue
    return rows

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

# ---------- Inventory: quick ping ----------
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

# ---------- Inventory: update condition (single JSON) ----------
@app.post("/inventory/condition/update")
async def inventory_condition_update(payload: Dict[str, Any] = Body(...)):
    sku = str(payload.get("sku", "")).strip()
    condition = str(payload.get("condition", "")).strip() or None
    condition_desc = str(payload.get("conditionDescription", "")).strip() or None
    if not sku:
        raise HTTPException(status_code=400, detail="Missing sku")
    current = await _get_inventory_item(sku)
    body = current
    if condition is not None:
        body["condition"] = condition
    if condition_desc is not None:
        body["conditionDescription"] = condition_desc
    updated = await _put_inventory_item(sku, body)
    return {"status": "ok", "sku": sku, "condition": body.get("condition"), "conditionDescription": body.get("conditionDescription"), "result": updated}

# ---------- Inventory: batch (preview & run) ----------
@app.post("/inventory/condition/preview")
async def inventory_condition_preview(file: UploadFile = File(...)):
    content = await file.read()
    rows = parse_skus_from_xlsx(content)
    # return the first 20 parsed rows to confirm headers
    return {"parsed": len(rows), "sample": rows[:20]}

@app.get("/inventory/condition/update-batch/form", response_class=HTMLResponse)
def inventory_condition_update_batch_form():
    return """
    <html><body>
      <h3>Upload XLSX for Condition Update</h3>
      <form action="/inventory/condition/preview" method="post" enctype="multipart/form-data" style="margin-bottom:12px;">
        <input type="file" name="file" accept=".xlsx" required />
        <button type="submit">Preview parsed rows</button>
      </form>
      <form action="/inventory/condition/update-batch" method="post" enctype="multipart/form-data">
        <input type="file" name="file" accept=".xlsx" required />
        <button type="submit">Upload & Run</button>
      </form>
      <p>Accepted header variants:
        <code>sku</code> (or <code>customlabel</code>/<code>itemsku</code>),
        <code>condition</code>,
        <code>conditionDescription</code> (or <code>condition_description</code>).
      </p>
    </body></html>
    """

@app.post("/inventory/condition/update-batch")
async def inventory_condition_update_batch(file: UploadFile = File(...)):
    try:
        content = await file.read()
        rows = parse_skus_from_xlsx(content)
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
    except Exception as e:
        logger.exception("Batch update failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Batch update failed: {e}")

