from fastapi import APIRouter
router = APIRouter()
@router.get("/oauth/callback")
def oauth_callback(code: str = "", state: str = "", error: str = "", error_description: str = ""):
    if error:
        return {"error": error, "error_description": error_description, "state": state}
    return {"code": code, "state": state}
