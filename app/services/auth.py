import os, json, time, base64, urllib.request, urllib.parse, urllib.error

def env_first(*keys, default=""):
    for k in keys:
        v = os.getenv(k, "")
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return default

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"

TOKENS_FILE   = env_first("TOKENS_FILE", "TOKEN_PATH", default="app/data/tokens.json")
CLIENT_ID     = env_first("EBAY_CLIENT_ID", "EBAY_APP_ID", "APP_ID", default="")
CLIENT_SECRET = env_first("EBAY_CLIENT_SECRET", "EBAY_CERT_ID", "CERT_ID", default="")
DEFAULT_SCOPES= env_first("EBAY_SCOPES", default="https://api.ebay.com/oauth/api_scope")
ENV_REFRESH   = env_first("EBAY_REFRESH_TOKEN", "EBAY_REFRESH_TOKEN_PROD", "REFRESH_TOKEN", default="")

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

def _post_form_with_retry(url, form, headers, attempts=3):
    data = urllib.parse.urlencode(form).encode("utf-8")
    for i in range(attempts):
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
            if e.code >= 500 or "server_error" in body:
                _set_error(f"http_error {e.code} {body}")
                if i < attempts - 1:
                    time.sleep(1 << i)
                    continue
            _set_error(f"http_error {e.code} {body}")
            raise
        except Exception as e:
            _set_error(f"post_error {e}")
            if i < attempts - 1:
                time.sleep(1 << i)
                continue
            raise

def _refresh_access_token(refresh_token):
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    headers.update(_basic_auth_header())
    form = {"grant_type": "refresh_token", "refresh_token": refresh_token, "scope": DEFAULT_SCOPES}
    resp = _post_form_with_retry(TOKEN_URL, form, headers)
    access_token = resp.get("access_token")
    expires_in = int(resp.get("expires_in", 0))
    if not access_token or expires_in <= 0:
        _set_error("failed_to_refresh_access_token")
        raise RuntimeError("failed_to_refresh_access_token")
    tokens = _read_tokens()
    tokens["access_token"] = access_token
    tokens["token_type"] = resp.get("token_type", "Bearer")
    tokens["access_expires_at"] = _now() + expires_in
    tokens["refresh_token"] = refresh_token
    tokens.setdefault("refresh_expires_at", 0)
    _write_tokens(tokens)
    _set_error("")
    return access_token

def _get_refresh_token():
    tokens = _read_tokens()
    if ENV_REFRESH:
        tokens["refresh_token"] = ENV_REFRESH
        _write_tokens(tokens)
        return ENV_REFRESH
    return tokens.get("refresh_token", "")

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
