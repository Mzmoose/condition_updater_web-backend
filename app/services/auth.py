import os, json, time, base64, urllib.request, urllib.parse, urllib.error

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
TOKENS_FILE = os.getenv("TOKENS_FILE", "app/data/tokens.json")
CLIENT_ID = os.getenv("EBAY_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET", "")
DEFAULT_SCOPES = os.getenv("EBAY_SCOPES", "https://api.ebay.com/oauth/api_scope")

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
    env_rt = os.getenv("EBAY_REFRESH_TOKEN", "")
    if env_rt:
        tokens["refresh_token"] = env_rt
        _write_tokens(tokens)
        return env_rt
    return ""

def auto_refresh_if_needed():
    try:
        tokens = _read_tokens()
        access = tokens.get("access_token")
        exp = int(tokens.get("access_expires_at", 0) or 0)
        if access and exp > _now() + 300:
            _set_error("")
            return access
        rt = _get_refresh_token()
        if not rt or not CLIENT_ID or not CLIENT_SECRET:
            _set_error("missing_env_or_refresh_token")
            return None
        return _refresh_access_token(rt)
    except Exception:
        return None

def get_last_error():
    return LAST_ERROR
