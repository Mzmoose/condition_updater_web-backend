# app/services/notifiers.py
from __future__ import annotations
import os
from typing import List, Dict, Any, Optional
import httpx

# ---------- Pushover (recommended) ----------
async def _pushover_send(user_key: str, app_token: str, text: str,
                         url: Optional[str] = None, url_title: Optional[str] = None) -> Dict[str, Any]:
    data = {"token": app_token, "user": user_key, "message": text}
    if url:
        data["url"] = url
        if url_title:
            data["url_title"] = url_title
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post("https://api.pushover.net/1/messages.json", data=data)
        ok = 200 <= r.status_code < 300 and r.json().get("status") == 1
        return {"channel": "pushover", "status_code": r.status_code, "ok": ok, "resp": r.text}

# ---------- Slack (optional) ----------
async def _slack_send(webhook_url: str, text: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(webhook_url, json={"text": text})
        ok = 200 <= r.status_code < 300
        return {"channel": "slack", "status_code": r.status_code, "ok": ok}

# ---------- Twilio SMS (optional) ----------
async def _twilio_sms(account_sid: str, auth_token: str, from_num: str, to_num: str, text: str) -> Dict[str, Any]:
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    data = {"To": to_num, "From": from_num, "Body": text}
    async with httpx.AsyncClient(timeout=15, auth=(account_sid, auth_token)) as client:
        r = await client.post(url, data=data)
        ok = 200 <= r.status_code < 300
        return {"channel": "twilio_sms", "status_code": r.status_code, "ok": ok, "resp": r.text}

# ---------- Public helper ----------
async def notify(text: str, link_url: Optional[str] = None, link_title: Optional[str] = None) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []

    # Pushover first (preferred)
    po_user = os.getenv("PUSHOVER_USER_KEY", "").strip()
    po_token = os.getenv("PUSHOVER_APP_TOKEN", "").strip()
    if po_user and po_token:
        results.append(await _pushover_send(po_user, po_token, text, url=link_url, url_title=link_title))

    # Slack (optional)
    slack_hook = os.getenv("SLACK_WEBHOOK_URL", "").strip()
    if slack_hook:
        results.append(await _slack_send(slack_hook, text if not link_url else f"{text}\n{link_url}"))

    # Twilio SMS (optional)
    sid   = os.getenv("TWILIO_SID", "").strip()
    auth  = os.getenv("TWILIO_AUTH", "").strip()
    frm   = os.getenv("TWILIO_FROM", "").strip()
    to    = os.getenv("TWILIO_TO", "").strip()
    if sid and auth and frm and to:
        sms_text = text if not link_url else f"{text} {link_url}"
        results.append(await _twilio_sms(sid, auth, frm, to, sms_text))

    if not results:
        results.append({"channel": "none", "status_code": 0, "ok": False, "error": "No notifier env vars set"})
    return results
