"""FastAPI application entry point."""

import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.health import router as health_router
from app.api.payments import router as payments_router
from app.api.events import router as events_router, ws_router as ws_router
from app.api.demo import router as demo_router
from app.core.config import get_settings

settings = get_settings()
app = FastAPI(title=settings.app_name, debug=settings.debug, version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(payments_router)
app.include_router(events_router)
app.include_router(ws_router)
app.include_router(demo_router)

# Mount SRE Operations Dashboard frontend static files
ops_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ops_dashboard"))
if os.path.exists(ops_dir):
    app.mount("/", StaticFiles(directory=ops_dir, html=True), name="ops")
