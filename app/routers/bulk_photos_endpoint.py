from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse, HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel, Field
import os
import httpx

from app.oauth import auto_refresh_if_needed
from app.services.bulk_downloader import run_bulk_download

router = APIRouter(prefix="/bulk", tags=["bulk"])

# --- Config via env vars ---
DENY_EBAY_USERS = {u.strip().lower() for u in os.environ.get("DENY_EBAY_USERS", "").split(",") if u.strip()}
BULK_DISABLED = os.environ.get("BULK_DISABLED", "").strip().lower() in {"1","true","yes","on"}

class BulkReq(BaseModel):
    start_prefix: str = Field(min_length=1, max_length=20)
    count: int = Field(gt=0, le=500)

def _signin_url(request: Request) -> str:
    base = "/oauth/login"
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}next=/bulk/ui"

def _whoami(access_token: str) -> dict:
    # eBay Identity API – return best-effort identity
    url = "https://apiz.ebay.com/identity/v1/user"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=10) as c:
            r = c.get(url, headers=headers)
            if r.status_code == 200:
                data = r.json()
                # Prefer username, else userId, else placeholder
                username = data.get("username") or data.get("userId") or "(unknown)"
                return {"username": username, "raw": data}
    except Exception:
        pass
    return {"username": "(unknown)", "raw": {}}

def _identity_for_request(request: Request):
    tok = auto_refresh_if_needed(request)
    if not tok:
        return None, None
    me = _whoami(tok["access_token"])
    return tok, me

def _deny_message_html(reason: str) -> str:
    return f"""
    <div style="max-width:860px;margin:40px auto;font-family:system-ui,Segoe UI,Arial">
      <div style="background:#fee;border:1px solid #f8bcbc;color:#b00020;padding:14px 16px;border-radius:8px;">
        <strong>Access disabled</strong><br>{reason}
      </div>
      <div style="margin-top:14px">
        <a href="/bulk/logout">Sign out</a>
      </div>
    </div>
    """

@router.get("/ui")
def bulk_ui(request: Request):
    if BULK_DISABLED:
        return HTMLResponse(_deny_message_html("The Bulk Photos tool is temporarily unavailable."), status_code=200)

    tok, me = _identity_for_request(request)
    topbar = ""
    body = ""

    if tok and me:
        uname = me["username"]
        # Denylist check (blocks running + shows message)
        denied = uname.lower() in DENY_EBAY_USERS
        topbar = f"""
        <div style="background:#0f172a;color:#fff;padding:10px 14px;font-family:system-ui,Segoe UI,Arial;display:flex;justify-content:space-between;align-items:center;">
          <div>Signed in as <strong>{uname}</strong></div>
          <div><a href="/bulk/logout" style="color:#c7d2fe;text-decoration:none">Sign out</a></div>
        </div>
        """
        if denied:
            return HTMLResponse(topbar + _deny_message_html("Your account has been disabled for testing."), status_code=200)

        body = """
        <div style="max-width:860px;margin:24px auto;font-family:system-ui,Segoe UI,Arial">
          <h2>Bulk Photos Downloader</h2>
          <div style="margin:12px 0;color:#334155">Enter a starting SKU prefix and how many SKUs to fetch.</div>
          <form id="f" onsubmit="return false" style="display:grid;grid-template-columns:1fr 140px;gap:10px;align-items:end">
            <div>
              <label>Start SKU prefix</label>
              <input id="pfx" style="width:100%;padding:10px;border:1px solid #cbd5e1;border-radius:8px" placeholder="e.g. 1000">
            </div>
            <div>
              <label>Count</label>
              <input id="cnt" type="number" min="1" max="500" value="2" style="width:100%;padding:10px;border:1px solid #cbd5e1;border-radius:8px">
            </div>
            <div></div>
            <button id="go" style="padding:10px 14px;border-radius:8px;border:1px solid #334155;background:#0ea5e9;color:#fff">Download ZIP</button>
          </form>
          <div id="msg" style="margin-top:16px;color:#0f172a"></div>
        </div>
        <script>
        const el = id => document.getElementById(id);
        el('go').addEventListener('click', async () => {
          const pfx = el('pfx').value.trim();
          const cnt = parseInt(el('cnt').value, 10);
          if (!pfx || !cnt || cnt < 1) { el('msg').textContent = 'Please enter a prefix and a valid count.'; return; }
          el('msg').textContent = 'Working…';
          try {
            const res = await fetch('/bulk/photos/run', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({ start_prefix: pfx, count: cnt })
            });
            if (!res.ok) {
              const t = await res.text();
              el('msg').textContent = 'Error: ' + t;
              return;
            }
            const blob = await res.blob();
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = `bulk_photos_${pfx}_${cnt}.zip`;
            a.click();
            URL.revokeObjectURL(a.href);
            el('msg').textContent = 'Downloaded.';
          } catch (e) {
            el('msg').textContent = 'Error: ' + e;
          }
        });
        </script>
        """
        return HTMLResponse(topbar + body, status_code=200)

    # Not signed in
    topbar = """
    <div style="background:#0f172a;color:#fff;padding:10px 14px;font-family:system-ui,Segoe UI,Arial;">
      Not signed in
    </div>
    """
    if BULK_DISABLED:
        return HTMLResponse(topbar + _deny_message_html("The Bulk Photos tool is temporarily unavailable."), status_code=200)

    body = f"""
    <div style="max-width:860px;margin:40px auto;font-family:system-ui,Segoe UI,Arial">
      <div style="background:#fff8f0;border:1px solid #ffd7a8;color:#7a3e0e;padding:14px 16px;border-radius:8px;">
        Please sign in with eBay to continue.
      </div>
      <div style="margin-top:16px">
        <a href="{_signin_url(request)}" style="display:inline-block;padding:10px 14px;border-radius:8px;background:#2563eb;color:#fff;text-decoration:none">Sign in with eBay</a>
      </div>
    </div>
    """
    return HTMLResponse(topbar + body, status_code=200)

@router.get("/logout")
def bulk_logout(request: Request):
    # Clear all cookies we can see; this effectively signs out the session.
    resp = RedirectResponse(url="/bulk/ui", status_code=303)
    for name in list(request.cookies.keys()):
        resp.delete_cookie(key=name, path="/")
    return resp

@router.post("/photos/run")
def bulk_photos_run(req: BulkReq, request: Request):
    if BULK_DISABLED:
        raise HTTPException(status_code=403, detail="disabled")

    tok, me = _identity_for_request(request)
    if not tok:
        raise HTTPException(status_code=401, detail="signin_required")

    uname = (me or {}).get("username") or ""
    if uname.lower() in DENY_EBAY_USERS:
        raise HTTPException(status_code=403, detail="access_denied")

    batch_zip = run_bulk_download(req.start_prefix, req.count, tok)
    filename = f"bulk_photos_{req.start_prefix}_{req.count}.zip"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(iter([batch_zip]), media_type="application/zip", headers=headers)
