import os, re, html
from typing import Dict, List, Any
from datetime import datetime, timedelta, timezone
import httpx
import xml.etree.ElementTree as ET

from ..oauth import TokenStore
from .files import first4

TRADING_ENDPOINT = "https://api.ebay.com/ws/api.dll"
SITE_ID = os.getenv("EBAY_SITE_ID", "0")
COMPAT_LEVEL = os.getenv("EBAY_COMPAT_LEVEL", "1231")

def _oauth_token() -> str:
    data = TokenStore.get() or {}
    tok = data.get("access_token")
    if not tok:
        raise ValueError("No access_token")
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
    root = ET.fromstring(r.text)
    ack = (root.findtext(".//{urn:ebay:apis:eBLBaseComponents}Ack") or "").upper()
    if ack not in {"SUCCESS", "WARNING"}:
        short = root.findtext(".//{urn:ebay:apis:eBLBaseComponents}ShortMessage") or "Error"
        long = root.findtext(".//{urn:ebay:apis:eBLBaseComponents}LongMessage") or ""
        raise RuntimeError(f"{call_name}: {short} {long}".strip())
    return root

def _ebay_time(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

async def _get_scheduled_via_getsellerlist() -> List[Dict[str,str]]:
    now = datetime.now(timezone.utc)
    to  = now + timedelta(days=120)
    page = 1
    items: List[Dict[str,str]] = []
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

async def _get_scheduled_via_getmyebayselling() -> List[Dict[str,str]]:
    items: List[Dict[str,str]] = []
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
        if not arr: break
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

async def get_scheduled_index() -> Dict[str, Dict[str,str]]:
    primary = await _get_scheduled_via_getsellerlist()
    fallback = await _get_scheduled_via_getmyebayselling()
    all_items = primary + fallback
    index: Dict[str, Dict[str,str]] = {}
    for it in all_items:
        for key in (it.get("sku",""), it.get("customLabel","")):
            if not key or not key.strip(): continue
            full = key.strip().lower()
            f4 = first4(key)
            payload = {
                "itemId": it.get("itemId",""),
                "title": it.get("title",""),
                "sku": it.get("sku",""),
                "customLabel": it.get("customLabel",""),
            }
            index[full] = payload
            if f4 and f4 not in index:
                index[f4] = payload
    return index

def _html_to_text_preserve_breaks(desc_html: str) -> str:
    if not desc_html:
        return ""
    t = desc_html
    t = re.sub(r"(?is)<\s*br\s*/?\s*>", "\n", t)
    t = re.sub(r"(?is)</\s*p\s*>", "\n", t)
    t = re.sub(r"(?is)</\s*div\s*>", "\n", t)
    t = re.sub(r"(?is)</\s*li\s*>", "\n", t)
    t = re.sub(r"(?is)<\s*p[^>]*>", "\n", t)
    t = re.sub(r"(?is)<\s*div[^>]*>", "\n", t)
    t = re.sub(r"(?is)<[^>]+>", "", t)
    t = html.unescape(t)
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return t.strip()

LABEL_AFTER_RE = re.compile(r"\b[A-Z][A-Za-z]+:", re.MULTILINE)
COND_LABEL_RE = re.compile(r"(?i)condition\s*:\s*")

def extract_condition_sentence_after_label(description_html: str, max_len: int = 600) -> str:
    if not description_html:
        return ""
    text = _html_to_text_preserve_breaks(description_html)
    m = COND_LABEL_RE.search(text)
    if not m:
        return ""
    after = text[m.end():].lstrip()
    nl = after.find("\n")
    candidates = []
    if nl >= 0: candidates.append(nl)
    lm = LABEL_AFTER_RE.search(after)
    if lm: candidates.append(lm.start())
    pm = re.search(r"[\.!\?]", after)
    if pm: candidates.append(pm.end())
    end = min([c for c in candidates if c > 0], default=len(after))
    snippet = after[:end].strip(" \t-•–—")
    snippet = re.sub(r"[ \t]{2,}", " ", snippet)
    if snippet and snippet[-1] not in ".!?":
        snippet += "."
    if len(snippet) > max_len:
        snippet = snippet[: max_len].rstrip()
        if snippet and snippet[-1] not in ".!?":
            snippet += "."
    return snippet

async def get_item_description(item_id: str) -> str:
    body = f"""
        <RequesterCredentials/>
        <ItemID>{item_id}</ItemID>
        <DetailLevel>ReturnAll</DetailLevel>
    """
    root = await trading_call("GetItem", body)
    ns = {"e": "urn:ebay:apis:eBLBaseComponents"}
    return root.findtext(".//e:Item/e:Description", default="", namespaces=ns) or ""

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
