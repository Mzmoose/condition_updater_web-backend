# app/condition_update_endpoint.py

from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from fastapi.responses import HTMLResponse
from .oauth import TokenStore
from .services.files import parse_file_to_skus
from .services.trading import (
    get_scheduled_index,
    get_item_description,
    extract_condition_sentence_after_label,
    revise_condition_description,
    verify_condition,
)

router = APIRouter()


def require_ebay_token():
    data = TokenStore.get() or {}
    if not data.get("access_token"):
        raise HTTPException(status_code=401, detail="Sign in to eBay first at /oauth/login")
    return True


@router.get("/trading/condition/update-batch/form", response_class=HTMLResponse)
def form():
    return """
<!doctype html>
<html>
  <body style="font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial; padding: 24px; max-width: 720px;">
    <h2>Scheduled Listings — Condition Updater</h2>
    <p>Upload a file with SKUs (first 4 digits used). Supports .xlsx, .csv, or .txt (one per line).</p>

    <h3>1) Preview matches</h3>
    <form action="/trading/condition/preview" method="post" enctype="multipart/form-data" style="margin-bottom:16px;">
      <input type="file" name="file" accept=".xlsx,.csv,.txt" required />
      <button type="submit">Preview (match SKUs to Scheduled)</button>
    </form>

    <h3>2) Run update</h3>
    <form action="/trading/condition/update-batch" method="post" enctype="multipart/form-data">
      <input type="file" name="file" accept=".xlsx,.csv,.txt" required />
      <button type="submit">Upload & Run</button>
    </form>

    <p style="margin-top:24px;color:#666;">Tip: If you get 401, sign in at <a href="/oauth/login">/oauth/login</a> first.</p>
  </body>
</html>
    """


@router.post("/trading/condition/preview", dependencies=[Depends(require_ebay_token)])
async def preview(file: UploadFile = File(...)):
    content = await file.read()
    skus = parse_file_to_skus(content, file.filename)
    index = get_scheduled_index()  # {sku4: (itemId, title)}

    items = []
    for s in skus:
        key = s.strip()
        pair = index.get(key)
        items.append({
            "sku": s,
            "matched": bool(pair),
            "itemId": pair[0] if pair else None,
            "title": pair[1] if pair else None,
        })
    return {
        "input_count": len(skus),
        "matched": sum(1 for x in items if x["matched"]),
        "items": items[:200],
    }


@router.post("/trading/condition/update-batch", dependencies=[Depends(require_ebay_token)])
async def update_batch(file: UploadFile = File(...)):
    content = await file.read()
    skus = parse_file_to_skus(content, file.filename)
    index = get_scheduled_index()  # {sku4: (itemId, title)}

    results = []
    for s in skus:
        key = s.strip()
        pair = index.get(key)
        if not pair:
            results.append({"sku": s, "ok": False, "error": "SKU not found in Scheduled list"})
            continue

        item_id, title = pair
        try:
            desc_html = get_item_description(item_id)
            cond_text = extract_condition_sentence_after_label(desc_html)
            if not cond_text:
                results.append({"sku": s, "itemId": item_id, "ok": False, "error": "No 'Condition:' sentence found"})
                continue

            revise_condition_description(item_id, cond_text)
            ok = verify_condition(item_id, cond_text)
            results.append({
                "sku": s,
                "itemId": item_id,
                "title": title,
                "ok": bool(ok),
                "applied": cond_text if ok else None,
                "verify": "passed" if ok else "failed",
            })
        except Exception as e:
            results.append({"sku": s, "itemId": item_id, "title": title, "ok": False, "error": str(e)})

    return {"updated": sum(1 for r in results if r.get("ok")), "total": len(results), "results": results}
