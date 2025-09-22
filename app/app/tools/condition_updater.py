from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from fastapi.responses import HTMLResponse
from ..oauth import TokenStore
from ..services.files import parse_file_to_skus
from ..services.trading import (
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
    <html><body>
      <h3>Scheduled Listings — Condition Updater</h3>
      <p>Upload SKUs only (.xlsx, .csv, or .txt). One SKU per row/line. Only the first 4 digits on each row are used.</p>
      <form action="/trading/condition/preview" method="post" enctype="multipart/form-data" style="margin-bottom:12px;">
        <input type="file" name="file" accept=".xlsx,.csv,.txt" required />
        <button type="submit">Preview (match SKUs to Scheduled)</button>
      </form>
      <form action="/trading/condition/update-batch" method="post" enctype="multipart/form-data">
        <input type="file" name="file" accept=".xlsx,.csv,.txt" required />
        <button type="submit">Run Update</button>
      </form>
      <p>Debug: <a href="/trading/scheduled/sample">/trading/scheduled/sample</a></p>
    </body></html>
    """

@router.get("/trading/scheduled/sample", dependencies=[Depends(require_ebay_token)])
async def scheduled_sample():
    idx = await get_scheduled_index()
    sample = []
    for i, (k, v) in enumerate(idx.items()):
        if i >= 50: break
        sample.append({"key": k, **v})
    return {"count": len(idx), "sample": sample}

@router.post("/trading/condition/preview", dependencies=[Depends(require_ebay_token)])
async def preview(file: UploadFile = File(...)):
    content = await file.read()
    skus = parse_file_to_skus(content, file.filename)
    index = await get_scheduled_index()
    items = []
    for s in skus:
        m = index.get(s.strip().lower())
        items.append({"sku": s, "matched": bool(m), "itemId": (m or {}).get("itemId"), "title": (m or {}).get("title")})
    return {"input_count": len(skus), "matched": sum(1 for x in items if x["matched"]), "items": items[:200]}

@router.post("/trading/condition/update-batch", dependencies=[Depends(require_ebay_token)])
async def update_batch(file: UploadFile = File(...)):
    content = await file.read()
    skus = parse_file_to_skus(content, file.filename)
    index = await get_scheduled_index()

    results = []
    for s in skus:
        key = s.strip().lower()
        info = index.get(key)
        if not info:
            results.append({"sku": s, "ok": False, "error": "SKU not found in Scheduled list"})
            continue

        item_id = info["itemId"]
        try:
            desc_html = await get_item_description(item_id)
            cond_text = extract_condition_sentence_after_label(desc_html)
            if not cond_text:
                results.append({"sku": s, "itemId": item_id, "ok": False, "error": "No 'Condition:' label or sentence not found"})
                continue

            await revise_condition_description(item_id, cond_text)
            ok = await verify_condition(item_id, cond_text)

            results.append({
                "sku": s,
                "itemId": item_id,
                "ok": bool(ok),
                "applied": cond_text if ok else None,
                "verify": "passed" if ok else "failed"
            })
        except Exception as e:
            results.append({"sku": s, "itemId": item_id, "ok": False, "error": str(e)})

    return {"updated": sum(1 for r in results if r.get("ok")), "total": len(results), "results": results}
