from __future__ import annotations
import re
import httpx
import xml.etree.ElementTree as ET
from typing import Dict, Tuple, Optional
from xml.sax.saxutils import escape
from ..oauth import TokenStore

EBAY_TRADING_ENDPOINT = "https://api.ebay.com/ws/api.dll"
COMPAT_LEVEL = "1149"
SITE_ID = "0"
NS = {"e": "urn:ebay:apis:eBLBaseComponents"}
FOUR = re.compile(r"^(\d{4})")

def _sku_key(s: Optional[str]) -> str:
    s = (s or "").strip()
    m = FOUR.match(s)
    return m.group(1) if m else s

def _post_trading(call_name: str, xml_body: str) -> ET.Element:
    data = TokenStore.get() or {}
    token = data.get("access_token")
    if not token:
        raise RuntimeError("No OAuth token in TokenStore. Sign in at /oauth/login first.")
    headers = {
        "Content-Type": "text/xml",
        "X-EBAY-API-CALL-NAME": call_name,
        "X-EBAY-API-SITEID": SITE_ID,
        "X-EBAY-API-COMPATIBILITY-LEVEL": COMPAT_LEVEL,
        "X-EBAY-API-IAF-TOKEN": token,
    }
    resp = httpx.post(EBAY_TRADING_ENDPOINT, headers=headers, content=xml_body, timeout=60.0)
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    ack = root.findtext(".//e:Ack", namespaces=NS)
    if ack not in ("Success", "Warning"):
        err = root.findtext(".//e:Errors/e:LongMessage", namespaces=NS) or "Trading API error"
        raise RuntimeError(f"{call_name} failed: {err}")
    return root

def get_scheduled_index() -> Dict[str, Tuple[str, str]]:
    page = 1
    per_page = 200
    out: Dict[str, Tuple[str, str]] = {}
    while True:
        body = f"""<?xml version="1.0" encoding="utf-8"?>
<GetMyeBaySellingRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <ErrorLanguage>en_US</ErrorLanguage>
  <WarningLevel>High</WarningLevel>
  <ScheduledList>
    <Include>true</Include>
    <Pagination>
      <EntriesPerPage>{per_page}</EntriesPerPage>
      <PageNumber>{page}</PageNumber>
    </Pagination>
  </ScheduledList>
</GetMyeBaySellingRequest>"""
        root = _post_trading("GetMyeBaySelling", body)
        items = root.findall(".//e:ScheduledList//e:Item", namespaces=NS)
        if not items:
            break
        for it in items:
            item_id = it.findtext("./e:ItemID", namespaces=NS) or ""
            title = it.findtext("./e:Title", namespaces=NS) or ""
            sku = it.findtext("./e:SKU", namespaces=NS) or ""
            key = _sku_key(sku)
            if key and item_id:
                out[key] = (item_id, title)
        total_pages = int(
            root.findtext(
                ".//e:ScheduledList//e:PaginationResult//e:TotalNumberOfPages",
                namespaces=NS,
            ) or "1"
        )
        if page >= total_pages:
            break
        page += 1
    return out

def get_item_description(item_id: str) -> str:
    body = f"""<?xml version="1.0" encoding="utf-8"?>
<GetItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <DetailLevel>ReturnAll</DetailLevel>
  <ItemID>{escape(item_id)}</ItemID>
</GetItemRequest>"""
    root = _post_trading("GetItem", body)
    return root.findtext(".//e:Item//e:Description", namespaces=NS) or ""

def extract_condition_sentence_after_label(description_html: str) -> Optional[str]:
    if not description_html:
        return None
    s = re.sub(r"(?is)</?(br|p|div|li|ul|ol|tr|td|table)[^>]*>", "\n", description_html or "")
    s = re.sub(r"(?is)<[^>]+>", " ", s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s).strip()
    m = re.search(r"(?im)^\s*condition\s*:\s*(.+)$", s)
    if not m:
        return None
    line = m.group(1).strip()
    line = re.split(r"\s+[A-Z][A-Za-z/&\-\s]{0,24}:\s*", line)[0].strip()
    m2 = re.match(r"(.+?[\.!?])(\s|$)", line)
    sentence = (m2.group(1) if m2 else line).strip()
    if not sentence.endswith((".", "!", "?")):
        sentence += "."
    return sentence

def revise_condition_description(item_id: str, condition_text: str) -> None:
    body = f"""<?xml version="1.0" encoding="utf-8"?>
<ReviseItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <Item>
    <ItemID>{escape(item_id)}</ItemID>
    <ConditionDescription>{escape(condition_text)}</ConditionDescription>
  </Item>
</ReviseItemRequest>"""
    _post_trading("ReviseItem", body)

def verify_condition(item_id: str, expected: str) -> bool:
    body = f"""<?xml version="1.0" encoding="utf-8"?>
<GetItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <DetailLevel>ReturnAll</DetailLevel>
  <ItemID>{escape(item_id)}</ItemID>
</GetItemRequest>"""
    root = _post_trading("GetItem", body)
    actual = root.findtext(".//e:Item//e:ConditionDescription", namespaces=NS) or ""
    return actual.strip() == expected.strip()
