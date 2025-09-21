import logging
from fastapi import FastAPI, Request, HTTPException, UploadFile, File
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from .oauth import build_auth_url, exchange_code_for_tokens, TokenStore
from .utils import parse_skus_from_xlsx

logger = logging.getLogger("uvicorn.error")

app = FastAPI(title="Condition Updater Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return RedirectResponse("/docs")

@app.get("/healthz")
def healthz():
    return {"ok": True}

@app.get("/oauth/login")
def oauth_login():
    url = build_auth_url()
    logger.info("Redirecting to eBay auth: %s", url)
    return RedirectResponse(url)

@app.get("/oauth/callback")
async def oauth_callback(request: Request):
    q = dict(request.query_params)
    if "error" in q:
        return JSONResponse({"status": "error", "error": q.get("error"), "error_description": q.get("error_description")}, status_code=400)
    code = q.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="Missing 'code' in callback")
    token_data = await exchange_code_for_tokens(code)
    TokenStore.save(token_data)
    return {"status": "ok", "message": "Tokens stored"}

@app.post("/condition/update")
async def condition_update(file: UploadFile = File(...)):
    content = await file.read()
    skus = parse_skus_from_xlsx(content)
    return {"received_skus": len(skus)}
    @app.get("/oauth/login/url")
def oauth_login_url():
    return {"url": build_auth_url()}

