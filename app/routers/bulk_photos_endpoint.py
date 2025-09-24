from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.services.auth import auto_refresh_if_needed

router = APIRouter(prefix="/bulk", tags=["bulk"])
templates = Jinja2Templates(directory="app/templates")

def _identity_for_request():
    tok = auto_refresh_if_needed()
    me = None
    return tok, me

@router.get("/ui", response_class=HTMLResponse)
async def bulk_ui(request: Request):
    tok, me = _identity_for_request()
    if not tok:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return templates.TemplateResponse("bulk/ui.html", {"request": request, "me": me})

