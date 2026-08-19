from fastapi import FastAPI

from app.db import db_is_ready, init_db
from app.redis_client import redis_is_ready
from app.routers.auth import router as auth_router
from gestalt_shared.errors import install_error_handlers
from gestalt_shared.health import build_health_router
from gestalt_shared.logging import configure_logging
from gestalt_shared.metrics import setup_metrics
from gestalt_shared.middleware import RequestIdMiddleware

from app.config import settings

configure_logging("auth-service", settings.log_level)

app = FastAPI(title="auth-service")

install_error_handlers(app)
setup_metrics(app, "auth-service")
# Added last so it's the outermost middleware layer: BaseHTTPMiddleware's
# call_next spawns the downstream app in a new anyio task, which copies the
# request-id contextvar at creation time -- so whichever middleware sets it
# must run (and set it) before anything that wraps around it calls
# call_next, or the mutation never becomes visible to those outer layers.
app.add_middleware(RequestIdMiddleware)
app.include_router(build_health_router(ready_check=lambda: db_is_ready() and redis_is_ready()))
app.include_router(auth_router)


@app.on_event("startup")
def on_startup():
    init_db()
