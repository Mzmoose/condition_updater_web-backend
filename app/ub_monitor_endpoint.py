# app/ub_monitor_endpoint.py
from __future__ import annotations

import re
import time
from typing import List

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, AnyHttpUrl

router = APIRouter(prefix="/monitor/ub", tags=["UB Monitor"])

class MonitorReq(BaseModel):
    url: AnyHttpUrl
    keywords: List[str]
    case_insensitive: bool = True
    timeout: float = 15.0  # seconds

@router.get("/ping")
def ping():
    return {"ok": True}

def _textify(html: str) -> str:
    # Strip scripts/styles and tags, collapse whitespace
    txt = re.sub(r"(?s)<script.*?>.*?</script>", " ", html, flags=re.I)
    txt = re.sub(r"(?s)<style.*?>.*?</style>", " ", txt, flags=re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    return re.sub(r"\s+", " ", txt).strip()

@router.post("/check")
async def check(req: MonitorReq):
    # Fetch page
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=req.timeout) as client:
            resp = await client.get(str(req.url))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Fetch failed: {e}")

    # Normalize text
    text = _textify(resp.text or "")
    haystack = text.lower() if req.case_insensitive else text

    # Find keywords
    hits = []
    misses = []
    for kw in req.keywords:
        needle = kw.lower() if req.case_insensitive else kw
        (hits if needle in haystack else misses).append(kw)

    return {
        "ok": True,
        "url": str(req.url),
        "status_code": resp.status_code,
        "matched": hits,
        "unmatched": misses,
        "fetched_len": len(resp.text or ""),
        "timestamp": int(time.time()),
    }
