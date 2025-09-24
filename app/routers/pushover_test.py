import os
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/pushover", tags=["pushover"])
APP_TOKEN_ENV, USER_KEY_ENV = "PUSHOVER_APP_TOKEN", "PUSHOVER_USER_KEY"

class PushReq(BaseModel):
    title: str = Field(default="Monitor Test")
    message: str = Field(default="If you see this, Pushover wiring is good.")
    device: str | None = None
    sound: str | None = None

@router.post("/test")
async def pushover_test(req: PushReq):
    app_token = os.getenv(APP_TOKEN_ENV, "").strip()
    user_key = os.getenv(USER_KEY_ENV, "").strip()
    if not app_token or not user_key:
        raise HTTPException(status_code=500, detail={
            "ok": False,
            "error": "Missing Pushover credentials in environment",
            "missing": {APP_TOKEN_ENV: bool(app_token), USER_KEY_ENV: bool(user_key)}
        })
    data = {"token": app_token, "user": user_key, "title": req.title, "message": req.message}
    if req.device:
        data["device"] = req.device
    if req.sound:
        data["sound"] = req.sound
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post("https://api.pushover.net/1/messages.json", data=data)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail={
            "ok": False,
            "pushover_status": r.status_code,
            "pushover_body": r.text
        })
    return {"ok": True, "pushover_status": r.status_code, "pushover_body": r.json()}
