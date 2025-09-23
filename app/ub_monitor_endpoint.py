# app/ub_monitor_endpoint.py
from __future__ import annotations

import os
import re
import time
from typing import List, Optional, Tuple

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, AnyHttpUrl

router = APIRouter(prefix="/monitor/ub", tags=["UB Monitor"])

# ---------- Models ----------
class MonitorReq(BaseModel):
    url: AnyHttpUrl = Field(..., description="Page to fetch and scan")
    keywords: List[str] = Field(..., description="List of keywords to search for")
    case_insensitive: bool = Field(True, description="Case-insensitive matching")
    timeout: int = Field(15, ge=1, le=120, description="HTTP timeout (seconds)")
    # Optional body field for notify; query param is preferred but body is accepted too
    notify: Optional[str] = Field(
        None,
        description="Optional notifier: 'slack' or 'pushover'. "
                    "If omitted here, query parameter 'notify' can be used."
    )

class MonitorResp(BaseModel):
    ok: bool
    url: str
    status_code: int
    matched: List[str]
    unmatched: List[str]
    fetched_len: int
    timestamp: int
    notified: Optional[str] = None
    notify_error: Optional[str] = None


# ---------- Helpers ----------
async def _fetch_text(url: str, timeout: int) -> Tuple[int, str]:
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "UB-Monitor/1.0"})
        return r.status_code, r.text or ""
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Fetch failed: {e}")

def _scan(text: str, keywords: List[str], case_insensitive: bool) -> Tuple[List[str], List[str]]:
    flags = re.IGNORECASE if case_insensitive else 0
    matched, unmatched = [], []
    for kw in keywords:
        if not kw:
            continue
        pattern = re.compile(re.escape(kw), flags)
        if pattern.search(text):
            matched.append(kw)
        else:
            unmatched.append(kw)
    return matched, unmatched

async def _notify_slack(message: str) -> None:
    hook = os.getenv("SLACK_WEBHOOK_URL")
    if not hook:
        raise RuntimeError("SLACK_WEBHOOK_URL not set")
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(hook, json={"text": message})
    resp.raise_for_status()

async def _notify_pushover(title: str, message: str) -> None:
    token = os.getenv("PUSHOVER_API_TOKEN")
    user = os.getenv("PUSHOVER_USER_KEY")
    if not token or not user:
        raise RuntimeError("PUSHOVER_API_TOKEN or PUSHOVER_USER_KEY not set")
    data = {
        "token": token,
        "user": user,
        "title": title,
        "message": message,
        "priority": 0,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post("https://api.pushover.net/1/messages.json", data=data)
    resp.raise_for_status()


# ---------- Endpoints ----------
@router.get("/ping")
def ping():
    return {"ok": True}

@router.post("/check", response_model=MonitorResp)
async def check(
    body: MonitorReq,
    notify_q: Optional[str] = Query(
        default=None,
        pattern="^(slack|pushover)$",
        description="Optional notifier: slack or pushover. "
                    "If provided, overrides body.notify."
    ),
):
    notify = notify_q or body.notify  # query takes precedence, then body, else None

    status_code, text = await _fetch_text(str(body.url), timeout=body.timeout)
    matched, unmatched = _scan(text, body.keywords, body.case_insensitive)

    resp = MonitorResp(
        ok=True,
        url=str(body.url),
        status_code=status_code,
        matched=matched,
        unmatched=unmatched,
        fetched_len=len(text),
        timestamp=int(time.time()),
        notified=None,
        notify_error=None,
    )

    # Only notify if at least one match
    if matched and notify:
        msg = (
            f"UB Monitor hit on {body.url}\n"
            f"Matched: {', '.join(matched)}"
        )
        try:
            if notify == "slack":
                await _notify_slack(msg)
            elif notify == "pushover":
                await _notify_pushover("UB Monitor", msg)
            resp.notified = notify
        except Exception as e:
            resp.notify_error = str(e)

    return resp
