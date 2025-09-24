import os, time, urllib.parse, urllib.request
from fastapi import APIRouter, Query

router = APIRouter(prefix="/monitor", tags=["monitor"])

def _pushover_send(title: str, message: str) -> bool:
    token = os.getenv("PUSHOVER_APP_TOKEN", "")
    user = os.getenv("PUSHOVER_USER_KEY", "")
    if not token or not user:
        return False
    data = urllib.parse.urlencode({
        "token": token,
        "user": user,
        "title": title,
        "message": message,
        "priority": "0",
    }).encode("utf-8")
    req = urllib.request.Request("https://api.pushover.net/1/messages.json", data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.getcode() < 300
    except Exception:
        return False

@router.get("/ub/check")
def ub_check(notify_q: str = Query(default="none"), reset_cache_q: str = Query(default="false")):
    ts = int(time.time())
    notified = False
    if notify_q.lower() == "pushover":
        notified = _pushover_send(os.getenv("UB_ALERT_PREFIX", "UBMON") + " check", f"ok {ts}")
    return {
        "ok": True,
        "ts": ts,
        "notified": notified,
        "reset_cache": reset_cache_q.lower() in ("true","1","yes")
    }
