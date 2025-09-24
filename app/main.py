from fastapi import FastAPI
from app.routers.bulk_photos_endpoint import router as bulk_router
from app.routers.auth_debug_endpoint import router as auth_debug_router
from app.routers.ub_monitor_endpoint import router as ub_monitor_router
from app.routers.auth_echo_endpoint import router as auth_echo_router

app = FastAPI()
app.include_router(bulk_router)
app.include_router(auth_debug_router)
app.include_router(ub_monitor_router)
app.include_router(auth_echo_router)

@app.get("/")
def root():
    return {"status": "ok"}

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/healthz")
def healthz():
    return {"ok": True}

@app.get("/healhtz")
def healhtz():
    return {"ok": True}
