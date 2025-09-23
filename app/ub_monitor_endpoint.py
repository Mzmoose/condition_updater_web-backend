from __future__ import annotations
import os, json, re
from typing import List, Optional, Dict, Any
from pathlib import Path
from urllib.parse import quote_plus, urljoin

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException, Form
from fastapi.responses import HTMLResponse

STATE_PATH = Path(os.getenv("UB_STATE_PATH", "/data/ub_seen.json"))
BASE = "https://www.unclaimedbaggage.com"

router = APIRouter()

def _normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def _parse_price(txt: str | None) -> Optional[float]:
    if not txt:
        return None
    m = re.search(r"(\d+(?:\.\d{1,2})?)", txt.replace(",", ""))
    return float(m.group(1)) if m else None

def _build_urls(keywords: List[str], pages: List[int]) -> List[str]:
    urls: List[str] = []
    for kw in keywords:
        q = quote_plus(kw)
        for p in pages:
            page_qs = f"&page={p}" if int(p) > 1 else ""
            urls.append(f"{BASE}/search?q={q}{page_qs}")
    out: List[str] = []
    seen = set()
    for u in urls:
        if u not in seen:
            out.append(u)
            seen.add(u)
    return out

def _scrape_items(html: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    items = []
    for a in soup.select('a[href*="/products/"]'):
        href = a.get("href") or ""
        title = _normalize_text(a.get_text())
        if not title:
            h = a.select_one("h2,h3,h4")
            title = _normalize_text(h.get_text()) if h else title
        if not href or not title:
            continue
        url = href if href.startswith("http") else urljoin(BASE, href)
        price_txt = ""
        price_el = None
        for sel in [".price", ".price__current", "[class*=price]", ".money"]:
            price_el = a.select_one(sel) or a.find_next(class_=re.compile("price", re.I))
            if price_el:
                break
        if price_el:
            price_txt = _normalize_text(price_el.get_text())
        price = _parse_price(price_txt)
        items.append({"title": title, "url": url, "price": price})
    uniq, seen_urls = [], set()
    for it in items:
        if it["url"] in seen_urls:
            continue
        seen_urls.add(it["url"])
        uniq.append(it)
    return uniq

def _load_state() -> Dict[str, Any]:
    try:
        if STATE_PATH.exists():
            return json.loads(STATE_PATH.read_text())
    except Exception:
        pass
    return {}

def _save_state(state: Dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))

def _match_keywords(title: str, includes: List[str], excludes: List[str]) -> bool:
    t = title.lower()
    if includes and not any(k.lower() in t for k in includes):
        return False
    if excludes and any(x.lower() in t for x in excludes):
        return False
    return True

def _within_price(price: Optional[float], min_price: Optional[float], max_price: Optional[float]) -> bool:
    if price is None:
        return True
    if min_price is not None and price < min_price:
        return False
    if max_price is not None and price > max_price:
        return False
    return True

async def _scan_once(
    keywords: List[str],
    exclude: List[str],
    pages: List[int],
    min_price: Optional[float],
    max_price: Optional[float],
    max_items: int = 100,
    remember: bool = True,
) -> Dict[str, Any]:
    urls = _build_urls(keywords, pages)
    state = _load_state()
    new_hits: List[Dict[str, Any]] = []
    total_scanned = 0

    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    }) as client:
        for url in urls:
            r = await client.get(url)
            r.raise_for_status()
            items = _scrape_items(r.text)
            for it in items:
                total_scanned += 1
                if total_scanned > max_items:
                    break
                title = it["title"]
                url_i = it["url"]
                price = it["price"]
                key = f"{title}|{url_i}"
                if key in state:
                    continue
                if not _match_keywords(title, keywords, exclude):
                    continue
                if not _within_price(price, min_price, max_price):
                    continue
                new_hits.append(it)
                if remember:
                    state[key] = {"first_seen": __import__("time").time(), "price": price}
            if total_scanned > max_items:
                break

    if remember:
        _save_state(state)

    return {"total_scanned": total_scanned, "new_hits": new_hits, "urls": urls}

@router.get("/form", response_class=HTMLResponse)
def form():
    return """
<html><body>
  <h3>UnclaimedBaggage Monitor (on-demand)</h3>
  <form action="/ub/scan/form" method="post">
    <label>Keywords (comma-separated): <input name="keywords" required></label><br>
    <label>Exclude (comma-separated): <input name="exclude"></label><br>
    <label>Min price: <input name="min_price" type="number" step="0.01"></label><br>
    <label>Max price: <input name="max_price" type="number" step="0.01"></label><br>
    <label>Pages (comma-separated, default 1): <input name="pages" value="1"></label><br>
    <label>Max items to scan: <input name="max_items" type="number" value="100"></label><br>
    <label>Remember (dedupe across runs): <input name="remember" type="checkbox" checked></label><br><br>
    <button type="submit">Scan Now</button>
  </form>
  <p>Or POST JSON to <code>/ub/scan</code>.</p>
</body></html>
"""

@router.post("/scan/form")
async def scan_form(
    keywords: str = Form(...),
    exclude: str = Form(""),
    min_price: str = Form(""),
    max_price: str = Form(""),
    pages: str = Form("1"),
    max_items: int = Form(100),
    remember: Optional[bool] = Form(False),
):
    kw = [s.strip() for s in keywords.split(",") if s.strip()]
    ex = [s.strip() for s in exclude.split(",") if s.strip()]
    pg = [int(s.strip()) for s in pages.split(",") if s.strip()]
    min_p = float(min_price) if min_price.strip() else None
    max_p = float(max_price) if max_price.strip() else None
    rem = bool(remember)
    out = await _scan_once(kw, ex, pg, min_p, max_p, max_items=max_items, remember=rem)
    return out

from pydantic import BaseModel, Field

class UBScanRequest(BaseModel):
    keywords: List[str] = Field(..., description="Phrases to include (case-insensitive)")
    exclude: List[str] = Field(default_factory=list, description="Phrases to exclude")
    pages: List[int] = Field(default_factory=lambda: [1])
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    max_items: int = 100
    remember: bool = True

@router.post("/scan")
async def scan_json(req: UBScanRequest):
    return await _scan_once(
        req.keywords, req.exclude, req.pages, req.min_price, req.max_price, max_items=req.max_items, remember=req.remember
    )

@router.get("/state")
def get_state():
    return _load_state()

@router.post("/state/reset")
def reset_state():
    if STATE_PATH.exists():
        STATE_PATH.unlink()
    return {"reset": True}
