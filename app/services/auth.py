import os, json, time, base64, urllib.request, urllib.parse, urllib.error

def env_first(*keys, default=""):
    for k in keys:
        v = os.getenv(k, "")
        if v:
            return v, k
    return default, None

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"

TOKENS_FILE, TOKENS_FILE_KEY = env_first("TOKENS_FILE", "TOKEN_PATH", default="app/data/tokens.json")
CLIENT_ID, CLIENT_ID_KEY = env_first("EBAY_CLIENT_ID", "EBAY_APP_ID", "APP_ID", default="")
CLIENT_SECRET, CLIENT_SECRET_KEY = env_first("EBAY_CLIENT_SECRET", "EBAY_CERT_ID", "CERT_ID", default="")
DEFAULT_SCOPES, SCOPES_KEY = env_first("EBAY_SCOPES", default="https://api.ebay.com/oauth/api_scope")
REFRESH_TOKEN, REFRESH_TOKEN_KEY = env_first("EBAY_REFRESH_TOKEN", "EBAY_REFRESH_TOKEN_PROD", "REFRESH_TOKEN", default="")

LAST_ERROR = ""

def _now():
    return int(time.time())

def _set_error(msg):
    global LAST_ERROR
    LAST_ERROR = str(msg)

def _read_tokens():
    try:
        with open(TOKENS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def _write_tokens(data):
    os.makedirs(os.path.dirname(TOKENS_FILE), exist_ok=True)
    with open(TOKENS_FILE, "w") as f:
        json.dump(data, f)

def _basic_auth_header():
    pair = f"{CLIENT_ID}:{CLIENT_SECRET}".encode("utf-8")
    b64 = base64.b64encode(pair).decode("ascii")
    return {"Authorization": f"Basic {b64}"}

def _post_form(url, form, headers):
    data = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8")
        except Exception:
            body = ""
        _set_error(f"http_error {e.code} {body}")
        raise
    except Exception as e:
        _set_error(f"post_error {e}")
        raise

def _refresh_access_token(refresh_token):
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    headers.update(_basic_auth_header())
    form = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": DEFAULT_SCOPES,
    }
    resp = _post_form(TOKEN_URL, form, headers)
    access_token = resp.get("access_token")
    token_type = resp.get("token_type", "Bearer")
    expires_in = int(resp.get("expires_in", 0))
    if not access_token or expires_in <= 0:
        _set_error("failed_to_refresh_access_token")
        raise RuntimeError("failed_to_refresh_access_token")
    tokens = _read_tokens()
    tokens["access_token"] = access_token
    tokens["token_type"] = token_type
    tokens["access_expires_at"] = _now() + expires_in
    if "refresh_token" not in tokens or not tokens["refresh_token"]:
        tokens["refresh_token"] = refresh_token
    if "refresh_expires_at" not in tokens:
        tokens["refresh_expires_at"] = 0
    _write_tokens(tokens)
    _set_error("")
    return access_token

def _get_refresh_token():
    tokens = _read_tokens()
    rt = tokens.get("refresh_token")
    if rt:
        return rt
    if REFRESH_TOKEN:
        tokens["refresh_token"] = REFRESH_TOKEN
        _write_tokens(tokens)
        return REFRESH_TOKEN
    return ""

def auto_refresh_if_needed():
    try:
        tokens = _read_tokens()
        access = tokens.get("access_token")
        exp = int(tokens.get("access_expires_at", 0) or 0)
        if access and exp > _now() + 300:
            _set_error("")
            return access
        if not CLIENT_ID or not CLIENT_SECRET:
            _set_error("missing_client_creds")
            return None
        rt = _get_refresh_token()
        if not rt:
            _set_error("missing_refresh_token")
            return None
        return _refresh_access_token(rt)
    except Exception:
        return None

def get_last_error():
    return LAST_ERROR

def get_debug_info():
    return {
        "chosen_keys": {
            "TOKENS_FILE": TOKENS_FILE_KEY or "default",
            "EBAY_CLIENT_ID": CLIENT_ID_KEY,
            "EBAY_CLIENT_SECRET": CLIENT_SECRET_KEY,
            "EBAY_REFRESH_TOKEN": REFRESH_TOKEN_KEY,
            "EBAY_SCOPES": SCOPES_KEY,
        },
        "present": {
            "TOKENS_FILE": bool(TOKENS_FILE),
            "EBAY_CLIENT_ID": bool(CLIENT_ID),
            "EBAY_CLIENT_SECRET": bool(CLIENT_SECRET),
            "EBAY_REFRESH_TOKEN": bool(REFRESH_TOKEN),
            "EBAY_SCOPES": bool(DEFAULT_SCOPES),
        }
    }
