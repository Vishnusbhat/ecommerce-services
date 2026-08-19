import asyncio
import logging

from fastapi import FastAPI

from app.config import settings
from app.db import db_is_ready, init_db
from app.delivery_simulator import delivery_simulation_loop
from app.reconciliation import reconciliation_loop
from app.routers.orders import router as orders_router
from gestalt_shared.errors import install_error_handlers
from gestalt_shared.health import build_health_router
from gestalt_shared.metrics import setup_metrics
from gestalt_shared.middleware import RequestIdMiddleware

logging.basicConfig(level=settings.log_level.upper())

app = FastAPI(title="order-service")

app.add_middleware(RequestIdMiddleware)
install_error_handlers(app)
setup_metrics(app, "order-service")
app.include_router(build_health_router(ready_check=db_is_ready))
app.include_router(orders_router)


@app.on_event("startup")
async def on_startup():
    init_db()
    app.state.reconciliation_task = asyncio.create_task(reconciliation_loop())
    app.state.delivery_simulation_task = asyncio.create_task(delivery_simulation_loop())
