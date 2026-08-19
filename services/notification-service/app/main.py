import threading

from fastapi import FastAPI

from app.config import settings
from app.consumer import consumer_is_ready, kafka_is_ready, run_consumer
from gestalt_shared.errors import install_error_handlers
from gestalt_shared.health import build_health_router
from gestalt_shared.logging import configure_logging
from gestalt_shared.metrics import setup_metrics
from gestalt_shared.middleware import RequestIdMiddleware

configure_logging("notification-service", settings.log_level)

app = FastAPI(title="notification-service")

install_error_handlers(app)
setup_metrics(app, "notification-service")
# Must be outermost -- see auth-service/app/main.py for why (BaseHTTPMiddleware
# task-boundary + contextvar propagation).
app.add_middleware(RequestIdMiddleware)
app.include_router(build_health_router(ready_check=lambda: kafka_is_ready() and consumer_is_ready()))

_stop_event = threading.Event()


@app.on_event("startup")
def on_startup():
    thread = threading.Thread(target=run_consumer, args=(_stop_event,), daemon=True)
    thread.start()
    app.state.consumer_thread = thread


@app.on_event("shutdown")
def on_shutdown():
    _stop_event.set()
