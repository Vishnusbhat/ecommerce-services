import asyncio

from fastapi import FastAPI

from app.abandonment import abandonment_loop
from app.config import settings
from app.redis_client import redis_is_ready
from app.routers.cart import router as cart_router
from gestalt_shared.errors import install_error_handlers
from gestalt_shared.health import build_health_router
from gestalt_shared.logging import configure_logging
from gestalt_shared.metrics import setup_metrics
from gestalt_shared.middleware import RequestIdMiddleware

configure_logging("cart-service", settings.log_level)

app = FastAPI(title="cart-service")

install_error_handlers(app)
setup_metrics(app, "cart-service")
# Must be outermost -- see auth-service/app/main.py for why (BaseHTTPMiddleware
# task-boundary + contextvar propagation).
app.add_middleware(RequestIdMiddleware)
app.include_router(build_health_router(ready_check=redis_is_ready))
app.include_router(cart_router)


@app.on_event("startup")
async def on_startup():
    app.state.abandonment_task = asyncio.create_task(abandonment_loop())
