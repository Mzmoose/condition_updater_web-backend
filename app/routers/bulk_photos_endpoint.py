from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from . import __init__ as _pkg
from app.services.bulk_downloader import run_bulk_download

router = APIRouter(prefix="/bulk", tags=["bulk"])

class BulkReq(BaseModel):
    start_prefix: str = Field(min_length=1, max_length=10)
    count: int = Field(gt=0, le=500)

@router.post("/photos/run")
def bulk_photos_run(req: BulkReq):
    try:
        batch_zip = run_bulk_download(req.start_prefix, req.count)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    filename = f"bulk_photos_{req.start_prefix}_{req.count}.zip"
    return StreamingResponse(iter([batch_zip]), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{filename}"'})
