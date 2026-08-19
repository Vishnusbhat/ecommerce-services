from fastapi import FastAPI

from app.cache import redis_is_ready
from app.config import settings
from app.db import db_is_ready, init_db
from app.routers.catalog import router as catalog_router
from app.seed import seed_if_empty
from gestalt_shared.errors import install_error_handlers
from gestalt_shared.health import build_health_router
from gestalt_shared.logging import configure_logging
from gestalt_shared.metrics import setup_metrics
from gestalt_shared.middleware import RequestIdMiddleware

configure_logging("catalog-service", settings.log_level)

app = FastAPI(title="catalog-service")

install_error_handlers(app)
setup_metrics(app, "catalog-service")
# Must be outermost -- see auth-service/app/main.py for why (BaseHTTPMiddleware
# task-boundary + contextvar propagation).
app.add_middleware(RequestIdMiddleware)
app.include_router(build_health_router(ready_check=lambda: db_is_ready() and redis_is_ready()))
app.include_router(catalog_router)


@app.on_event("startup")
def on_startup():
    init_db()
    seed_if_empty()
