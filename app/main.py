import logging, asyncio, os, json, io, re, csv
from typing import List, Dict, Any, Tuple, Optional
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

# ---------- Parsing helpers ----------
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(s).strip().lower())

_SKU_KEYS = {"sku", "itemsku", "customlabel", "customlabels", "id"}
_COND_KEYS = {"condition", "itemcondition", "cond", "conditionid"}
_CONDDESC_KEYS = {"conditiondescription", "condition_desc", "conditiontext", "conddesc"}

def _pick(mapper: Dict[str, str], cand: Dict[str, str], keys: set) -> str:
    for k in keys:
        if k in mapper:
            v = cand.get(mapper[k], "")
            if v:
                return v
    for k,v in cand.items():
        if k in keys and v:
            return v
    return ""

def _detect_header_row(rows: List[List[str]]) -> Tuple[int, Dict[str, str]]:
    """
    Find the header row index and a mapping from canonical keys to actual column keys.
    Returns (header_index, mapping). mapping maps canonical 'sku','condition','conditiondescription'
    to actual normalized column names found.
    """
    for i, r in enumerate(rows[:10]):  # scan first 10 rows
        headers = [_norm(c) for c in r]
        if not any(headers):
            continue
        hset = set(headers)
        mapping: Dict[str, str] = {}
        # find matches
        def _find(one_of: set) -> Optional[str]:
            for key in one_of:
                if key in hset:
                    return key
            return None
        sku_col = _find(_SKU_KEYS)
        cond_col = _find(_COND_KEYS)
        cdesc_col = _find(_CONDDESC_KEYS)
        if sku_col:
            if cond_col:  mapping["condition"] = cond_col
            if cdesc_col: mapping["conditiondescription"] = cdesc_col
            mapping["sku"] = sku_col
            return i, mapping
    # fallback: assume first row is header
    headers = [_norm(c) for c in rows[0]] if rows else []
    mapping = {}
    for k in _SKU_KEYS:
        if k in headers:
            mapping["sku"] = k
            break
    for k in _COND_KEYS:
        if k in headers:
            mapping["condition"] = k
            break
    for k in _CONDDESC_KEYS:
        if k in headers:
            mapping["conditiondescription"] = k
            break
    return 0, mapping

def _rows_from_xlsx(binary: bytes) -> Dict[str, Any]:
    wb = load_workbook(io.BytesIO(binary), read_only=True, data_only=True)
    sheetnames = wb.sheetnames
    # collect first 20 rows from each sheet
    raw_preview: Dict[str, List[List[str]]] = {}
    parsed: List[Dict[str, Any]] = []
    for name in sheetnames:
        ws = wb[name]
        rows = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            vals = [("" if v is None else str(v)) for v in row]
            rows.append(vals)
            if i >= 50:
                break
        raw_preview[name] = rows
        if not rows:
            continue
        head_idx, mapping = _detect_header_row(rows)
        data_rows = rows[head_idx+1:]
        for vals in data_rows:
            normrow = {_norm(k): (vals[j].strip() if j < len(vals) else "") for j,k in enumerate(rows[head_idx])}
            sku = _pick(mapping, normrow, _SKU_KEYS)
            cond = _pick(mapping, normrow, _COND_KEYS)
            cdesc = _pick(mapping, normrow, _CONDDESC_KEYS)
            if sku and str(sku).strip():
                parsed.append({
                    "sku": str(sku).strip(),
                    "condition": str(cond or "").strip(),
                    "conditionDescription": str(cdesc or "").strip()
                })
    return {"sheetnames": sheetnames, "raw_preview": raw_preview, "parsed": parsed}

def _rows_from_csv(binary: bytes) -> Dict[str, Any]:
    text = binary.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = [[c for c in r] for r in reader]
    preview = rows[:50]
    if not rows:
        return {"sheetnames": ["CSV"], "raw_preview": {"CSV": preview}, "parsed": []}
    head_idx, mapping = _detect_header_row(rows)
    parsed: List[Dict[str, Any]] = []
    for vals in rows[head_idx+1:]:
        normrow = {_norm(k): (vals[j].strip() if j < len(vals) else "") for j,k in enumerate(rows[head_idx])}
        sku = _pick(mapping, normrow, _SKU_KEYS)
        cond = _pick(mapping, normrow, _COND_KEYS)
        cdesc = _pick(mapping, normrow, _CONDDESC_KEYS)
        if sku and str(sku).strip():
            parsed.append({
                "sku": str(sku).strip(),
                "condition": str(cond or "").strip(),
                "conditionDescription": str(cdesc or "").strip()
            })
    return {"sheetnames": ["CSV"], "raw_preview": {"CSV": preview}, "parsed": parsed}

def parse_file(binary: bytes, filename: str) -> Dict[str, Any]:
    name = (filename or "").lower()
    if name.endswith(".csv"):
        return _rows_from_csv(binary)
    # default .xlsx
    return _rows_from_xlsx(binary)

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

# ---------- Inventory: test ----------
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

# ---------- Inventory: single JSON ----------
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

# ---------- Inventory: preview & run ----------
@app.post("/inventory/condition/preview")
async def inventory_condition_preview(file: UploadFile = File(...)):
    content = await file.read()
    parsed = parse_file(content, file.filename)
    # show a compact preview to help diagnose headers
    sample = parsed["parsed"][:20]
    meta = {
        "sheetnames": parsed["sheetnames"],
        "header_row_preview": {name: (parsed["raw_preview"][name][0] if parsed["raw_preview"][name] else []) for name in parsed["sheetnames"]}
    }
    return {"parsed_count": len(parsed["parsed"]), "sample": sample, "meta": meta}

@app.get("/inventory/condition/update-batch/form", response_class=HTMLResponse)
def inventory_condition_update_batch_form():
    return """
    <html><body>
      <h3>Upload XLSX/CSV for Condition Update</h3>
      <form action="/inventory/condition/preview" method="post" enctype="multipart/form-data" style="margin-bottom:12px;">
        <input type="file" name="file" accept=".xlsx,.csv" required />
        <button type="submit">Preview parsed rows</button>
      </form>
      <form action="/inventory/condition/update-batch" method="post" enctype="multipart/form-data">
        <input type="file" name="file" accept=".xlsx,.csv" required />
        <button type="submit">Upload & Run</button>
      </form>
      <p>Header variants accepted: <code>sku | itemsku | customlabel</code>, <code>condition</code>, <code>conditionDescription | condition_description</code>.</p>
    </body></html>
    """

@app.post("/inventory/condition/update-batch")
async def inventory_condition_update_batch(file: UploadFile = File(...)):
    try:
        content = await file.read()
        parsed = parse_file(content, file.filename)
        rows: List[Dict[str, Any]] = parsed["parsed"]
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
        return {
            "updated": ok_count,
            "total": len(results),
            "results": results,
            "diagnostics": {
                "parsed_count": len(rows),
                "sheetnames": parsed["sheetnames"]
            }
        }
    except Exception as e:
        logger.exception("Batch update failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Batch update failed: {e}")
