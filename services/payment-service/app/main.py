from fastapi import FastAPI

from app.config import settings
from app.redis_client import redis_is_ready
from app.routers.payments import router as payments_router
from gestalt_shared.errors import install_error_handlers
from gestalt_shared.health import build_health_router
from gestalt_shared.logging import configure_logging
from gestalt_shared.metrics import setup_metrics
from gestalt_shared.middleware import RequestIdMiddleware

configure_logging("payment-service", settings.log_level)

app = FastAPI(title="payment-service")

install_error_handlers(app)
setup_metrics(app, "payment-service")
# Must be outermost -- see auth-service/app/main.py for why (BaseHTTPMiddleware
# task-boundary + contextvar propagation).
app.add_middleware(RequestIdMiddleware)
app.include_router(build_health_router(ready_check=redis_is_ready))
app.include_router(payments_router)
