Service URL: https://condition-updater-web-backend-1.onrender.com
Health: GET /health
Bulk UI: GET /bulk/ui
UB Monitor: GET /monitor/ub/check
Notify Test: GET /monitor/ub/check?notify_q=pushover&reset_cache_q=false
Smoke Script: ./scripts/verify_services.sh
Deploy: Render → Manual Deploy → Deploy Latest Commit
Env Vars: EBAY_CLIENT_ID, EBAY_CLIENT_SECRET, EBAY_REFRESH_TOKEN, EBAY_SCOPES, TOKENS_FILE
