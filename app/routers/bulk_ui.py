from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
import os, json

router = APIRouter()

def _login_url(request: Request, next_path: str="/bulk/ui") -> str:
    base = "/oauth/login"
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}next={next_path}"

@router.get("/bulk/ui", response_class=HTMLResponse)
def bulk_ui(request: Request):
    if os.getenv("BULK_UI_DISABLED") in ("1", "true", "True", "yes"):
        return HTMLResponse("<h1>Temporarily disabled</h1>", status_code=403)
    html = """
<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>Bulk Photo Downloader</title>
<style>
  body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;max-width:720px;margin:40px auto;padding:0 16px}
  h1{font-size:1.4rem;margin-bottom:8px}
  .card{border:1px solid #e5e7eb;border-radius:12px;padding:16px;margin:16px 0}
  label{display:block;margin:8px 0 4px}
  input{width:100%;padding:10px;border:1px solid #cbd5e1;border-radius:8px}
  button{padding:10px 14px;border-radius:10px;border:1px solid #0ea5e9;background:#0ea5e9;color:white;cursor:pointer}
  button.secondary{border-color:#e5e7eb;background:white;color:#111827}
  .row{display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap}
  .row .col{flex:1 1 200px}
  .muted{color:#6b7280;font-size:.92rem}
  #out{white-space:pre-wrap;font-family:ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;font-size:.9rem}
</style>
</head>
<body>
  <h1>Bulk Photo Downloader</h1>
  <p class="muted" id="who"></p>

  <div class="card">
    <div class="row">
      <div class="col">
        <label for="start_prefix">Beginning SKU</label>
        <input id="start_prefix" placeholder="e.g. ABC123" />
      </div>
      <div class="col">
        <label for="count">How many SKUs</label>
        <input id="count" type="number" min="1" max="500" value="10" />
      </div>
    </div>
    <div style="margin-top:12px;display:flex;gap:8px">
      <button id="runBtn">Download ZIP</button>
      <button id="loginBtn" class="secondary" style="display:none">Sign in with eBay</button>
      <button id="logoutBtn" class="secondary" style="display:none">Sign out</button>
    </div>
    <div id="out" style="margin-top:12px"></div>
  </div>

<script>
async function session() {
  try {
    const r = await fetch("/oauth/session", {credentials:"include"});
    if (!r.ok) return {signed_in:false};
    return await r.json();
  } catch { return {signed_in:false}; }
}

async function ensureLogin() {
  const s = await session();
  const who = document.getElementById("who");
  const loginBtn = document.getElementById("loginBtn");
  const logoutBtn = document.getElementById("logoutBtn");
  if (s.signed_in) {
    who.textContent = "Signed in. Tokens active.";
    loginBtn.style.display = "none";
    logoutBtn.style.display = "inline-block";
  } else {
    who.textContent = "Not signed in. Click “Sign in with eBay”.";
    loginBtn.style.display = "inline-block";
    logoutBtn.style.display = "none";
  }
  return s.signed_in;
}

document.getElementById("loginBtn").onclick = () => {
  const next = encodeURIComponent("/bulk/ui");
  window.location.href = `/oauth/login?next=${next}`;
};

document.getElementById("logoutBtn").onclick = async () => {
  try { await fetch("/oauth/logout", {method:"POST", credentials:"include"}); } catch {}
  location.reload();
};

document.getElementById("runBtn").onclick = async () => {
  const out = document.getElementById("out");
  out.textContent = "";
  const ok = await ensureLogin();
  if (!ok) {
    out.textContent = "Please sign in first.";
    return;
  }
  const start_prefix = document.getElementById("start_prefix").value.trim();
  const count = parseInt(document.getElementById("count").value, 10);
  if (!start_prefix || !count || count < 1) {
    out.textContent = "Enter a beginning SKU and a positive count.";
    return;
  }
  try {
    const r = await fetch("/bulk/photos/run", {
      method: "POST",
      headers: {"Content-Type": "application/json", "Accept": "application/zip"},
      credentials: "include",
      body: JSON.stringify({ start_prefix, count })
    });
    if (r.status === 401) {
      document.getElementById("who").textContent = "Session expired. Please sign in.";
      return;
    }
    if (!r.ok) {
      const txt = await r.text();
      out.textContent = "Error: " + txt;
      return;
    }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `bulk_photos_${start_prefix}_${count}.zip`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    out.textContent = "Download started.";
  } catch (e) {
    out.textContent = "Network error.";
  }
};

ensureLogin();
</script>
</body>
</html>
"""
    return HTMLResponse(html)

@router.get("/bulk/ui/ping")
def bulk_ui_ping():
    if os.getenv("BULK_UI_DISABLED") in ("1", "true", "True", "yes"):
        raise HTTPException(status_code=403, detail="disabled")
    return JSONResponse({"ok": True})
