from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from .oauth import build_auth_url, exchange_code_for_tokens, TokenStore
from .ebay_api import revise_condition_description
from .utils import parse_skus_from_xlsx

app = FastAPI(title="ConditionUpdater-Web API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

store = TokenStore()

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/oauth/ebay/login")
def oauth_login():
    url = build_auth_url()
    return RedirectResponse(url)

@app.get("/oauth/ebay/callback")
async def oauth_callback(code: str):
    try:
        tokens = await exchange_code_for_tokens(code)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"OAuth code exchange failed: {e}")
    store.set_refresh_token("demo_user", tokens.get("refresh_token", ""))
    return JSONResponse({"ok": True, "message": "OAuth complete. You can now call /condition/update."})

@app.post("/condition/update")
async def condition_update(file: UploadFile = File(...)):
    user_id = "demo_user"
    refresh_token = store.get_refresh_token(user_id)
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Not authorized. Complete /oauth/ebay/login first.")

    content = await file.read()
    try:
        skus = parse_skus_from_xlsx(content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read Excel: {e}")
    if not skus:
        raise HTTPException(status_code=400, detail="No SKUs found in uploaded file.")

    try:
        access_token = await store.get_access_token_from_refresh(refresh_token)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"eBay token refresh failed: {e}")

    results = []
    for sku in skus:
        new_condition_text = "Condition updated via ConditionUpdater-Web MVP."
        try:
            outcome = await revise_condition_description(access_token, sku=sku, condition_text=new_condition_text)
            results.append({"sku": sku, "updated": True, "message": outcome})
        except Exception as e:
            results.append({"sku": sku, "updated": False, "message": f"eBay call failed: {e}"})

    return {"count": len(results), "results": results}

from app.condition_update_endpoint import router as condition_router
app.include_router(condition_router)
