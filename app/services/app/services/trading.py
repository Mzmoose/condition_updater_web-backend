# app/services/trading.py
from __future__ import annotations

import datetime as dt
from typing import Dict, Tuple, Optional
import httpx
import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from ..oauth import TokenStore

EBAY_TRADING_ENDPOINT = "https://api.ebay.com/ws/api.dll"
COMPAT_LEVEL = "1149"        # safe modern compatibility level
SITE_ID = "0"                # 0 = US

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
        # OAuth for Trading API:
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
    """
    Returns {normalized_sku: (itemId, title)} for SCHEDULED listings only.
    We use GetMyeBaySelling ScheduledList (no Sort to avoid invalid values).
    """
    page = 1
    per_page = 200
    result: Dict[str, Tuple[str, str]] = {}

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
        items = root.findall(".//e:ScheduledList//e:Item", NS)
        if not items:
            break
        for it in items:
            item_id = it.findtext("./e:ItemID", namespaces=NS) or ""
            title = it.findtext("./e:Title", namespaces=NS) or ""
            sku = it.findtext("./e:SKU", namespaces=NS) or ""
            key = _sku_key(sku)
            if key and item_id:
                result[key] = (item_id, title)
        # pagination check
        total_pages = int(root.findtext(".//e:ScheduledList//e:PaginationResult//e:TotalNumberOfPages", NS) or "1")
        if page >= total_pages:
            break
        page += 1

    return result

def get_item_description(item_id: str) -> str:
    body = f"""<?xml version="1.0" encoding="utf-8"?>
<GetItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <DetailLevel>ReturnAll</DetailLevel>
  <ItemID>{escape(item_id)}</ItemID>
</GetItemRequest>"""
    root = _post_trading("GetItem", body)
    desc = root.findtext(".//e:Item//e:Description", namespaces=NS) or ""
    return desc

def extract_condition_sentence_after_label(description_html: str) -> Optional[str]:
    """
    Pull the first sentence after 'Condition:' from the description HTML.
    Falls back to None if not found.
    """
    # Crude HTML strip for our specific need
    text = re.sub(r"<[^>]+>", " ", description_html or "", flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()

    # Look for 'Condition:' label
    m = re.search(r"condition\s*:\s*(.+)$", text, flags=re.IGNORECASE)
    if not m:
        return None

    tail = m.group(1).strip()
    # first sentence (end at period or line break-ish punctuation)
    m2 = re.match(r"(.+?[\.!?])(\s|$)", tail)
    sentence = (m2.group(1) if m2 else tail).strip()

    # ensure single trailing period per your rule
    if not sentence.endswith("."):
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
