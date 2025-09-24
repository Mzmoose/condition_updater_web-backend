from fastapi import FastAPI
from app.routers.bulk_photos_endpoint import router as bulk_router

app = FastAPI()

app.include_router(bulk_router)

@app.get("/")
def root():
    return {"status": "ok"}

@app.get("/health")
def health():
    return {"ok": True}

