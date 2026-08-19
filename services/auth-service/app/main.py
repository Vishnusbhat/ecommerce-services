import logging

from fastapi import FastAPI

from app.db import db_is_ready, init_db
from app.redis_client import redis_is_ready
from app.routers.auth import router as auth_router
from gestalt_shared.errors import install_error_handlers
from gestalt_shared.health import build_health_router
from gestalt_shared.metrics import setup_metrics
from gestalt_shared.middleware import RequestIdMiddleware

from app.config import settings

logging.basicConfig(level=settings.log_level.upper())

app = FastAPI(title="auth-service")

app.add_middleware(RequestIdMiddleware)
install_error_handlers(app)
setup_metrics(app, "auth-service")
app.include_router(build_health_router(ready_check=lambda: db_is_ready() and redis_is_ready()))
app.include_router(auth_router)


@app.on_event("startup")
def on_startup():
    init_db()
