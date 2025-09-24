from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from . import __init__ as _pkg
from app.services.bulk_downloader import run_bulk_download
from app.oauth import auto_refresh_if_needed, build_auth_url

router = APIRouter(prefix="/bulk", tags=["bulk"])

class BulkReq(BaseModel):
    start_prefix: str = Field(min_length=1, max_length=10)
    count: int = Field(gt=0, le=500)

@router.post("/photos/run")
def bulk_photos_run(req: BulkReq, request: Request):
    try:
        try:
            iaf = auto_refresh_if_needed(request)
        except Exception:
            iaf = None
        if not iaf:
            raise HTTPException(status_code=401, detail="signin_required")
        batch_zip = run_bulk_download(req.start_prefix, req.count, iaf)
        filename = f"bulk_photos_{req.start_prefix}_{req.count}.zip"
        return StreamingResponse(iter([batch_zip]), media_type="application/zip",
                                 headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    except HTTPException as he:
        raise he
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": "bulk_error", "detail": str(e)})

@router.get("/ui")
def bulk_ui(request: Request):
    signin = build_auth_url(request)
    html = """
<!doctype html><html><head><meta charset="utf-8"><title>Bulk Photos</title>
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;padding:24px;max-width:680px;margin:0 auto;">
<h2>Bulk Photos</h2>
<div style="margin:12px 0;">
<a href="%s" style="display:inline-block;padding:10px 14px;background:#2563eb;color:#fff;border-radius:8px;text-decoration:none;">Sign in with eBay</a>
</div>
<div style="margin:16px 0;">
<label>Start SKU prefix</label><br>
<input id="sp" style="padding:8px;width:240px;border:1px solid #ccc;border-radius:8px;">
</div>
<div style="margin:16px 0;">
<label>Count</label><br>
<input id="ct" type="number" min="1" max="500" value="5" style="padding:8px;width:120px;border:1px solid #ccc;border-radius:8px;">
</div>
<button id="go" style="padding:10px 14px;background:#16a34a;color:#fff;border:0;border-radius:8px;">Download ZIP</button>
<p id="msg" style="color:#dc2626;"></p>
<script>
const btn = document.getElementById('go');
btn.onclick = async () => {
  const sp = document.getElementById('sp').value.trim();
  const ct = parseInt(document.getElementById('ct').value, 10);
  const msg = document.getElementById('msg');
  msg.textContent = '';
  try {
    const r = await fetch('/bulk/photos/run', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({start_prefix: sp, count: ct})
    });
    if (r.status === 401) { msg.textContent = 'Please sign in with eBay first.'; return; }
    if (!r.ok) { const t = await r.text(); msg.textContent = 'Error: ' + t; return; }
    const blob = await r.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'bulk_photos_'+sp+'_'+ct+'.zip';
    document.body.appendChild(a); a.click(); a.remove();
    window.URL.revokeObjectURL(url);
  } catch(e) { msg.textContent = 'Network error.'; }
};
</script>
</body></html>
""" % signin
    return HTMLResponse(content=html, status_code=200)

@router.get("/session/check")
def bulk_session_check(request: Request):
    try:
        try:
            iaf = auto_refresh_if_needed(request)
        except Exception:
            iaf = None
        return {"signed_in": bool(iaf)}
    except Exception:
        return {"signed_in": False}
