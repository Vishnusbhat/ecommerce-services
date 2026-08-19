import logging

from fastapi import FastAPI

from app.config import settings
from app.redis_client import redis_is_ready
from app.routers.cart import router as cart_router
from gestalt_shared.errors import install_error_handlers
from gestalt_shared.health import build_health_router
from gestalt_shared.metrics import setup_metrics
from gestalt_shared.middleware import RequestIdMiddleware

logging.basicConfig(level=settings.log_level.upper())

app = FastAPI(title="cart-service")

app.add_middleware(RequestIdMiddleware)
install_error_handlers(app)
setup_metrics(app, "cart-service")
app.include_router(build_health_router(ready_check=redis_is_ready))
app.include_router(cart_router)
