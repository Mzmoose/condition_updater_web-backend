import logging, asyncio, os, io, re, csv, json, html
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any
from fastapi import FastAPI, Request, HTTPException, UploadFile, File
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
from openpyxl import load_workbook
import xml.etree.ElementTree as ET

from .oauth import (
    build_auth_url,
    exchange_code_for_tokens,
    refresh_tokens,
    auto_refresh_if_needed,
    TokenStore,
)

logger = logging.getLogger("uvicorn.error")

# ────────────────────────────── App ──────────────────────────────
app = FastAPI(title="Condition Updater (Scheduled, Trading API)")

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
    return RedirectResponse("/docs")

@app.get("/healthz")
def healthz():
    return {"ok": True}

# ─────────────────────── OAuth (unchanged) ───────────────────────
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

# ─────────────────────── File → SKUs helper ──────────────────────
def parse_file_to_skus(binary: bytes, filename: str) -> List[str]:
    name = (filename or "").lower()
    if name.endswith(".xlsx"):
        wb = load_workbook(io.BytesIO(binary), read_only=True, data_only=True)
        skus: List[str] = []
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                if not row:
                    continue
                v = row[0]
                if v is None:
                    continue
                s = str(v).strip()
                if s:
                    skus.append(s)
        seen, out = set(), []
        for s in skus:
            if s not in seen:
                seen.add(s)
                out.append(s)
        return out
    if name.endswith(".csv"):
        text = binary.decode("utf-8", errors="replace")
        rdr = csv.reader(io.StringIO(text))
        skus: List[str] = []
        for i, row in enumerate(rdr):
            if not row:
                continue
            s = str(row[0]).strip()
            if not s:
                continue
            if i == 0 and s.lower() in {"sku", "customlabel"} and len(row) == 1:
                continue
            skus.append(s)
        return skus
    text = binary.decode("utf-8", errors="replace")
    return [ln.strip() for ln in text.splitlines() if ln.strip()]

# ───────────────────── Trading API client (OAuth) ─────────────────────
TRADING_ENDPOINT = "https://api.ebay.com/ws/api.dll"
SITE_ID = os.getenv("EBAY_SITE_ID", "0")             # 0 = US
COMPAT_LEVEL = os.getenv("EBAY_COMPAT_LEVEL", "1231")

def _oauth_token() -> str:
    data = TokenStore.get() or {}
    tok = data.get("access_token")
    if not tok:
        raise HTTPException(status_code=400, detail="No access_token; run /oauth/login")
    return tok

def _headers(call_name: str) -> Dict[str, str]:
    return {
        "X-EBAY-API-CALL-NAME": call_name,
        "X-EBAY-API-SITEID": SITE_ID,
        "X-EBAY-API-COMPATIBILITY-LEVEL": COMPAT_LEVEL,
        "X-EBAY-API-IAF-TOKEN": _oauth_token(),
        "Content-Type": "text/xml",
        "Accept": "text/xml",
    }

def _wrap(call: str, inner_xml: str) -> str:
    return f'<?xml version="1.0" encoding="utf-8"?><{call}Request xmlns="urn:ebay:apis:eBLBaseComponents">{inner_xml}</{call}Request>'

async def trading_call(call_name: str, body_xml: str) -> ET.Element:
    xml = _wrap(call_name, body_xml)
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(TRADING_ENDPOINT, headers=_headers(call_name), content=xml)
    try:
        root = ET.fromstring(r.text)
    except Exception:
        logger.error("Trading parse error (%s): %s", call_name, r.text[:1000])
        raise HTTPException(status_code=502, detail=f"Trading parse error: {call_name}")
    ack = (root.findtext(".//{urn:ebay:apis:eBLBaseComponents}Ack") or "").upper()
    if ack not in {"SUCCESS", "WARNING"}:
        short = root.findtext(".//{urn:ebay:apis:eBLBaseComponents}ShortMessage") or "Error"
        long = root.findtext(".//{urn:ebay:apis:eBLBaseComponents}LongMessage") or ""
        raise HTTPException(status_code=400, detail=f"{call_name}: {short} {long}".strip())
    return root

# ───────────── Utilities ─────────────
def _ebay_time(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

TAG_RE = re.compile(r"<[^>]+>")
COND_LABEL_RE = re.compile(r"condition\s*:\s*", re.IGNORECASE)

# ───────────── Scheduled lookup (GetSellerList w/ ReturnAll + pagination) ─────────────
async def _get_scheduled_via_getsellerlist() -> List[Dict[str, str]]:
    now = datetime.now(timezone.utc)
    to  = now + timedelta(days=120)
    page = 1
    items: List[Dict[str, str]] = []

    while True:
        body = f"""
            <RequesterCredentials/>
            <StartTimeFrom>{_ebay_time(now)}</StartTimeFrom>
            <StartTimeTo>{_ebay_time(to)}</StartTimeTo>
            <DetailLevel>ReturnAll</DetailLevel>
            <Pagination>
                <EntriesPerPage>200</EntriesPerPage>
                <PageNumber>{page}</PageNumber>
            </Pagination>
        """
        root = await trading_call("GetSellerList", body)
        ns = {"e": "urn:ebay:apis:eBLBaseComponents"}

        for it in root.findall(".//e:ItemArray/e:Item", ns):
            status = (it.findtext("e:ListingStatus", default="", namespaces=ns) or "").lower()
            if status != "scheduled":
                continue
            items.append({
                "itemId": it.findtext("e:ItemID", default="", namespaces=ns) or "",
                "title": it.findtext("e:Title", default="", namespaces=ns) or "",
                "sku": it.findtext("e:SKU", default="", namespaces=ns) or "",
                "customLabel": it.findtext("e:SellingManagerProductDetails/e:CustomLabel", default="", namespaces=ns) or "",
            })

        has_more = (root.findtext(".//e:HasMoreItems", default="false", namespaces=ns) or "").lower() == "true"
        page += 1
        if not has_more or page > 50:
            break

    return items

# ───────────── Fallback: GetMyeBaySelling ScheduledList (ReturnAll) ─────────────
async def _get_scheduled_via_getmyebayselling() -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    page = 1
    while True:
        body = f"""
            <RequesterCredentials/>
            <DetailLevel>ReturnAll</DetailLevel>
            <ScheduledList>
                <Include>true</Include>
                <Pagination>
                    <EntriesPerPage>200</EntriesPerPage>
                    <PageNumber>{page}</PageNumber>
                </Pagination>
            </ScheduledList>
            <ActiveList><Include>false</Include></ActiveList>
            <UnsoldList><Include>false</Include></UnsoldList>
        """
        root = await trading_call("GetMyeBaySelling", body)
        ns = {"e": "urn:ebay:apis:eBLBaseComponents"}

        arr = root.findall(".//e:ScheduledList/e:ItemArray/e:Item", ns)
        if not arr:
            break
        for it in arr:
            items.append({
                "itemId": it.findtext("e:ItemID", default="", namespaces=ns) or "",
                "title": it.findtext("e:Title", default="", namespaces=ns) or "",
                "sku": it.findtext("e:SKU", default="", namespaces=ns) or "",
                "customLabel": it.findtext("e:SellingManagerProductDetails/e:CustomLabel", default="", namespaces=ns) or "",
            })

        total_pages = int(root.findtext(".//e:ScheduledList/e:PaginationResult/e:TotalNumberOfPages", default="1", namespaces=ns) or "1")
        page += 1
        if page > total_pages or page > 50:
            break

    return items

# ───────────── Build index from both sources ─────────────
async def get_scheduled_index() -> Dict[str, Dict[str, str]]:
    primary = await _get_scheduled_via_getsellerlist()
    fallback = await _get_scheduled_via_getmyebayselling()
    all_items = primary + fallback

    index: Dict[str, Dict[str, str]] = {}
    for it in all_items:
        for key in (it.get("sku",""), it.get("customLabel","")):
            if key and key.strip():
                k = key.strip().lower()
                index[k] = {
                    "itemId": it.get("itemId",""),
                    "title": it.get("title",""),
                    "sku": it.get("sku",""),
                    "customLabel": it.get("customLabel",""),
                }
    return index

# ───────────── Get/Revise Item ─────────────
async def get_item_description(item_id: str) -> str:
    body = f"""
        <RequesterCredentials/>
        <ItemID>{item_id}</ItemID>
        <DetailLevel>ReturnAll</DetailLevel>
    """
    root = await trading_call("GetItem", body)
    ns = {"e": "urn:ebay:apis:eBLBaseComponents"}
    return root.findtext(".//e:Item/e:Description", default="", namespaces=ns) or ""

def extract_condition_sentence_after_label(description_html: str, max_len: int = 600) -> str:
    if not description_html:
        return ""
    text = TAG_RE.sub("", description_html)
    m = COND_LABEL_RE.search(text)
    if not m:
        return ""
    after = text[m.end():].strip()
    if not after:
        return ""
    m2 = re.search(r"[\.!\?]", after)
    sent = after if not m2 else after[: m2.end()]
    sent = sent.strip()
    if not sent:
        return ""
    if sent[-1] not in ".!?":
        sent += "."
    if len(sent) > max_len:
        sent = sent[: max_len - 1]
        if sent[-1] != ".":
            sent = sent.rstrip() + "."
    return sent

async def revise_condition_description(item_id: str, cond_desc: str) -> None:
    body = f"""
        <RequesterCredentials/>
        <Item>
            <ItemID>{item_id}</ItemID>
            <ConditionDescription>{html.escape(cond_desc)}</ConditionDescription>
        </Item>
    """
    await trading_call("ReviseItem", body)

async def verify_condition(item_id: str, expected: str) -> bool:
    body = f"""
        <RequesterCredentials/>
        <ItemID>{item_id}</ItemID>
        <DetailLevel>ReturnAll</DetailLevel>
    """
    root = await trading_call("GetItem", body)
    ns = {"e": "urn:ebay:apis:eBLBaseComponents"}
    actual = root.findtext(".//e:Item/e:ConditionDescription", default="", namespaces=ns) or ""
    return actual.strip() == expected.strip()

# ───────────── UI Forms ─────────────
@app.get("/trading/condition/update-batch/form", response_class=HTMLResponse)
def form():
    return """
    <html><body>
      <h3>Scheduled Listings — Condition Updater</h3>
      <p>Upload SKUs only (.xlsx, .csv, or .txt). One SKU per row/line.</p>
      <form action="/trading/condition/preview" method="post" enctype="multipart/form-data" style="margin-bottom:12px;">
        <input type="file" name="file" accept=".xlsx,.csv,.txt" required />
        <button type="submit">Preview (match SKUs to Scheduled)</button>
      </form>
      <form action="/trading/condition/update-batch" method="post" enctype="multipart/form-data">
        <input type="file" name="file" accept=".xlsx,.csv,.txt" required />
        <button type="submit">Run Update</button>
      </form>
      <p><i>Logic: find 'Condition:' in Description, take first sentence after it, ensure it ends with a period, then revise Condition Description.</i></p>
      <p>Debug: <a href="/trading/scheduled/sample">/trading/scheduled/sample</a></p>
    </body></html>
    """

# ───────────── Debug: peek at what eBay returns ─────────────
@app.get("/trading/scheduled/sample")
async def scheduled_sample():
    idx = await get_scheduled_index()
    sample = []
    for i, (k, v) in enumerate(idx.items()):
        if i >= 50: break
        sample.append({"key": k, **v})
    return {"count": len(idx), "sample": sample}

# ───────────── Preview: which SKUs match Scheduled ─────────────
@app.post("/trading/condition/preview")
async def preview(file: UploadFile = File(...)):
    content = await file.read()
    skus = parse_file_to_skus(content, file.filename)
    index = await get_scheduled_index()
    items = []
    for s in skus:
        m = index.get(s.strip().lower())
        items.append({"sku": s, "matched": bool(m), "itemId": (m or {}).get("itemId"), "title": (m or {}).get("title")})
    return {"input_count": len(skus), "matched": sum(1 for x in items if x["matched"]), "items": items[:200]}

# ───────────── Batch Update ─────────────
@app.post("/trading/condition/update-batch")
async def update_batch(file: UploadFile = File(...)):
    content = await file.read()
    skus = parse_file_to_skus(content, file.filename)
    index = await get_scheduled_index()

    results: List[Dict[str, Any]] = []
    for s in skus:
        key = s.strip().lower()
        info = index.get(key)
        if not info:
            results.append({"sku": s, "ok": False, "error": "SKU not found in Scheduled list"})
            continue

        item_id = info["itemId"]
        try:
            desc_html = await get_item_description(item_id)
            cond_text = extract_condition_sentence_after_label(desc_html)
            if not cond_text:
                results.append({"sku": s, "itemId": item_id, "ok": False, "error": "No 'Condition:' label or sentence not found"})
                continue

            await revise_condition_description(item_id, cond_text)
            ok = await verify_condition(item_id, cond_text)

            results.append({
                "sku": s,
                "itemId": item_id,
                "ok": bool(ok),
                "applied": cond_text if ok else None,
                "verify": "passed" if ok else "failed"
            })
        except HTTPException as e:
            results.append({"sku": s, "itemId": item_id, "ok": False, "status": e.status_code, "error": e.detail})
        except Exception as e:
            results.append({"sku": s, "itemId": item_id, "ok": False, "itemId": item_id, "error": str(e)})

    return {"updated": sum(1 for r in results if r.get("ok")), "total": len(results), "results": results}

# ───────────── Debug (optional) ─────────────
@app.get("/debug/token-file")
def debug_token_file():
    path = os.getenv("TOKEN_PATH", "/data/tokens.json")
    return {"path": path, "exists": os.path.exists(path), "size": (os.path.getsize(path) if os.path.exists(path) else None)}
