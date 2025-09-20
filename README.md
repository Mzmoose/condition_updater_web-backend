# ConditionUpdater-Web (Backend MVP)

FastAPI backend for **Condition Updater** as a hosted web service.

## What it does (MVP)
- OAuth flow with eBay (stubs ready).
- `POST /condition/update` accepts your `condition_update_input.xlsx` (same format you use now).
- Parses SKUs, *placeholder* call to eBay Trading API `ReviseItem` using OAuth access token.
- `GET /health` for uptime checks.

> This is an MVP skeleton: the eBay calls are implemented as functions you can wire to production quickly.
> It’s structured so we can add a job queue (Redis RQ) later without changing the API surface.

## Endpoints
- `GET /health` — simple health check.
- `GET /oauth/ebay/login` — redirects user to eBay for consent.
- `GET /oauth/ebay/callback` — receives `code`, exchanges for `refresh_token` and stores it (in-memory demo).
- `POST /condition/update` — multipart file upload (`file` field). Returns a JSON result per SKU.

## Environment
Copy `.env.example` to `.env` and fill in:

```
EBAY_CLIENT_ID=
EBAY_CLIENT_SECRET=
EBAY_REDIRECT_URI=https://api.sallymuse.com/oauth/ebay/callback
# Optional storage/DB for tokens in production
ENCRYPTION_KEY=change-me-long-random
```

## Run locally
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open http://localhost:8000/docs to try it.

## Deploy on Render (no Docker)
1. Create a new **Web Service** from this repo.
2. Runtime: Python 3.11+
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `uvicorn app.main:app --host 0.0.0.0 --port 10000`
5. Add env vars from `.env.example` (set `EBAY_REDIRECT_URI` to your Render URL + `/oauth/ebay/callback` until DNS is live).
6. After deploy, update Porkbun CNAME for `api.sallymuse.com` to your Render host (e.g., `yourapp.onrender.com`).

> Later we’ll add a Redis-backed job queue and a worker service. The API will stay the same.
