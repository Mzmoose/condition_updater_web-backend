from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
import logging

# Reuse your existing downloader
from app.services.bulk_downloader import run_bulk_download

router = APIRouter(prefix="/bulk", tags=["bulk"])

class BulkReq(BaseModel):
    start_prefix: str = Field(min_length=1, max_length=32)
    count: int = Field(gt=0, le=500)

def _signin_url(request: Request) -> str:
    # Build a login URL that bounces back to /bulk/ui
    try:
        from app.oauth import build_auth_url
        u = build_auth_url(request)
    except Exception:
        u = "/oauth/login"
    sep = "&" if "?" in u else "?"
    return f"{u}{sep}next=/bulk/ui"

def _has_session(request: Request) -> bool:
    try:
        from app.oauth import auto_refresh_if_needed
        return bool(auto_refresh_if_needed(request))
    except Exception as e:
        logging.warning("bulk_ui: session check failed: %s", e)
        return False

@router.get("/ui", response_class=HTMLResponse)
async def bulk_ui(request: Request):
    # Never raise here; render regardless of auth state
    signed_in = _has_session(request)
    signin = _signin_url(request)

    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Bulk Photos</title>
  <style>
    body {{ font-family: -apple-system, system-ui, Segoe UI, Roboto, Helvetica, Arial; margin: 24px; }}
    .btn {{ display:inline-block; padding:10px 14px; border-radius:8px; border:0; cursor:pointer; }}
    .primary {{ background:#3b82f6; color:#fff; }}
    .success {{ background:#22c55e; color:#fff; }}
    .muted {{ color:#6b7280; }}
    input {{ padding:10px; border:1px solid #d1d5db; border-radius:8px; width:220px; }}
    label {{ font-weight:600; display:block; margin-top:18px; margin-bottom:6px; }}
    .row {{ margin-top:10px; }}
    #msg {{ margin-top:14px; }}
    .hidden {{ display:none; }}
  </style>
</head>
<body>
  <h1>Bulk Photos</h1>

  <div class="row">
    <a class="btn primary" href="{signin}" id="signinBtn">Sign in with eBay</a>
    <span id="authState" class="muted">{'Signed in' if signed_in else 'Not signed in'}</span>
  </div>

  <label>Start SKU prefix</label>
  <input id="start" value="1000" />

  <label>Count</label>
  <input id="count" type="number" value="2" min="1" max="500" />

  <div class="row">
    <button class="btn success" id="runBtn">Download ZIP</button>
  </div>

  <div id="msg" class="muted"></div>

<script>
async function refreshAuthState() {{
  try {{
    const r = await fetch('/bulk/auth/status', {{ credentials: 'include' }});
    const j = await r.json();
    document.getElementById('authState').textContent = j.signed_in ? 'Signed in' : 'Not signed in';
  }} catch(e) {{
    document.getElementById('authState').textContent = 'Not signed in';
  }}
}}

async function run() {{
  const start = document.getElementById('start').value.trim();
  const count = parseInt(document.getElementById('count').value, 10);
  const msg = document.getElementById('msg');
  msg.textContent = 'Starting…';

  const resp = await fetch('/bulk/photos/run', {{
    method:'POST',
    headers: {{ 'Content-Type':'application/json' }},
    credentials: 'include',
    body: JSON.stringify({{ start_prefix:start, count:count }})
  }});

  if (resp.status === 401) {{
    msg.textContent = 'Please sign in with eBay first.';
    return;
  }}

  if (!resp.ok) {{
    const t = await resp.text();
    msg.textContent = 'Error: ' + t;
    return;
  }}

  // Download the zip
  const blob = await resp.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  const fname = resp.headers.get('content-disposition')?.match(/filename="([^"]+)"/)?.[1] || 'photos.zip';
  a.download = fname;
  document.body.appendChild(a);
  a.click();
  a.remove();
  msg.textContent = 'Downloaded ' + fname;
}}

document.getElementById('runBtn').addEventListener('click', run);
window.addEventListener('load', refreshAuthState);
</script>
</body>
</html>"""
    return HTMLResponse(html)

@router.get("/auth/status")
def auth_status(request: Request):
    return {"signed_in": _has_session(request)}

@router.post("/photos/run")
def photos_run(req: BulkReq, request: Request):
    # Require a valid session HERE (not in the UI)
    try:
        from app.oauth import auto_refresh_if_needed
        iaf = auto_refresh_if_needed(request)
        if not iaf:
            raise HTTPException(status_code=401, detail="signin_required")
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("bulk_ui: token retrieval failed: %s", e)
        raise HTTPException(status_code=401, detail="signin_required")

    batch_zip = run_bulk_download(req.start_prefix, req.count, iaf)
    filename = f"bulk_photos_{req.start_prefix}_{req.count}.zip"
    return StreamingResponse(iter([batch_zip]),
                             media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})
