# app/ub_monitor_endpoint.py
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import List, Optional, Tuple, Set
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import APIRouter, Query
from pydantic import BaseModel, AnyUrl

router = APIRouter(prefix="/monitor/ub", tags=["UB Monitor"])

PUSHOVER_APP_TOKEN = os.getenv("PUSHOVER_APP_TOKEN")
PUSHOVER_USER_KEY = os.getenv("PUSHOVER_USER_KEY")
UB_ALERT_PREFIX = os.getenv("UB_ALERT_PREFIX", "HIT")
# where we persist “seen” items so we only notify on new ones
UB_MONITOR_CACHE_PATH = os.getenv("UB_MONITOR_CACHE_PATH", "/data/ub_monitor_cache.json")

# -------------- helpers: cache --------------

def _load_cache() -> dict:
    try:
        with open(UB_MONITOR_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_cache(cache: dict) -> None:
    os.makedirs(os.path.dirname(UB_MONITOR_CACHE_PATH), exist_ok=True)
    tmp = UB_MONITOR_CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, sort_keys=True)
    os.replace(tmp, UB_MONITOR_CACHE_PATH)

def _cache_key(url: str, keywords: List[str], case_insensitive: bool) -> str:
    norm_kws = [kw.lower() for kw in keywords] if case_insensitive else keywords
    blob = f"{url}||{'|'.join(sorted(norm_kws))}||{case_insensitive}"
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()

# -------------- helpers: fetch & parse --------------

_A_TAG_RE = re.compile(r'<a[^>]+href="([^"]*?/products/[^"]+)"[^>]*>(.*?)</a>',
                       re.IGNORECASE | re.DOTALL)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")

def _strip_tags(html: str) -> str:
    txt = _TAG_STRIP_RE.sub(" ", html)
    return re.sub(r"\s+", " ", txt).strip()

def _extract_items(base_url: str, html: str) -> Set[Tuple[str, str]]:
    """
    Return a set of (absolute_product_url, anchor_text) tuples for product links.
    """
    items: Set[Tuple[str, str]] = set()
    for href, inner in _A_TAG_RE.findall(html):
        abs_url = urljoin(base_url, href)
        title = _strip_tags(inner)
        items.add((abs_url, title))
    return items

def _filter_by_keywords(items: Set[Tuple[str, str]],
                        keywords: List[str],
                        case_insensitive: bool) -> Set[Tuple[str, str]]:
    if not keywords:
        return items
    out: Set[Tuple[str, str]] = set()
    for u, t in items:
        hay = f"{u} {t}"
        if case_insensitive:
            hay = hay.lower()
            kws = [k.lower() for k in keywords]
        else:
            kws = keywords
        if any(k in hay for k in kws):
            out.add((u, t))
    return out

# -------------- notification: pushover --------------

async def _notify_pushover(title: str, message: str, url: Optional[str] = None) -> Optional[str]:
    if not PUSHOVER_APP_TOKEN or not PUSHOVER_USER_KEY:
        return "PUSHOVER_APP_TOKEN or PUSHOVER_USER_KEY not set"
    # Pushover message size is limited; keep it concise
    payload = {
        "token": PUSHOVER_APP_TOKEN,
        "user": PUSHOVER_USER_KEY,
        "title": title,
        "message": message[:1024],
    }
    if url:
        payload["url"] = url

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post("https://api.pushover.net/1/messages.json", data=payload)
        if r.status_code != 200:
            return f"pushover {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return f"pushover error: {e}"
    return None

# -------------- models & endpoints --------------

class MonitorReq(BaseModel):
    url: AnyUrl
    keywords: List[str] = []
    case_insensitive: bool = True
    timeout: int = 15
    notify: Optional[str] = None               # "pushover" (or "slack" if you add it back)
    diff_only: bool = True                      # <— NEW: only alert on new items
    reset_cache: bool = False                   # optionally clear cache for this key

@router.get("/ping", summary="Ping")
def ping():
    return {"ok": True, "ts": time.time()}

@router.post("/check", summary="Check")
async def check(
    body: MonitorReq,
    notify_q: Optional[str] = Query(None, pattern="^(slack|pushover)$"),
    reset_cache_q: Optional[bool] = Query(None)
):
    url = str(body.url)
    timeout = max(5, min(60, body.timeout))
    notify = notify_q or body.notify
    diff_only = body.diff_only
    reset_cache = (reset_cache_q is True) or body.reset_cache

    # Fetch page
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers={"User-Agent": "UB-Monitor/1.0"})
        status = resp.status_code
        html = resp.text if status == 200 else ""
    except Exception as e:
        return {
            "ok": False,
            "url": url,
            "status_code": None,
            "error": f"fetch error: {e}",
        }

    matched_kws: List[str] = []
    if body.keywords:
        hay = html.lower() if body.case_insensitive else html
        for kw in (k.lower() for k in body.keywords) if body.case_insensitive else body.keywords:
            if kw in hay:
                original = kw if not body.case_insensitive else next(
                    orig for orig in body.keywords if orig.lower() == kw
                )
                matched_kws.append(original)

    # Extract product links & titles, then filter by keywords for relevance
    items = _extract_items(url, html)
    filt_items = _filter_by_keywords(items, body.keywords, body.case_insensitive)

    # Cache compare
    ck = _cache_key(url, body.keywords, body.case_insensitive)
    cache = _load_cache()
    if reset_cache:
        cache.pop(ck, None)

    seen: Set[str] = set(cache.get(ck, []))
    current: Set[str] = set(u for (u, _t) in filt_items)
    new_urls = sorted(list(current - seen))

    notified = None
    notify_error = None

    # Decide whether to notify
    should_notify = (notify == "pushover") and (
        (not diff_only and matched_kws) or (diff_only and len(new_urls) > 0)
    )

    if should_notify:
        # Construct a small message with new items (up to a handful)
        preview_lines = []
        by_url = {u: t for (u, t) in filt_items}
        for u in new_urls[:5]:
            preview_lines.append(f"- {by_url.get(u,'(no title)')}\n{u}")
        more = ""
        if len(new_urls) > 5:
            more = f"\n(+{len(new_urls)-5} more)"

        title = f"{UB_ALERT_PREFIX}: {len(new_urls)} new match(es)"
        msg = "\n".join(preview_lines) + more
        notify_error = await _notify_pushover(title, msg, url=url)
        notified = "pushover" if notify_error is None else None

    # Update cache with current set (only after a successful fetch)
    cache[ck] = sorted(list(current))
    _save_cache(cache)

    return {
        "ok": True,
        "url": url,
        "status_code": status,
        "matched": matched_kws,
        "unmatched": [kw for kw in body.keywords if kw not in matched_kws],
        "new_count": len(new_urls),
        "new_items": new_urls[:10],  # sample
        "fetched_len": len(html),
        "timestamp": int(time.time()),
        "notified": notified,
        "notify_error": notify_error,
    }
