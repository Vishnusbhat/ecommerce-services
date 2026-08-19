"""App-level Prometheus metrics (docs/05-observability-stack.md).

Istio/Envoy telemetry gives golden signals for free via the sidecar's
/stats/prometheus -- that doesn't exist in local docker-compose, so this
middleware fills the same role (request rate/duration/error split) plus a
/metrics endpoint each service exposes for its own business counters.
"""
from __future__ import annotations

import logging
import time

from fastapi import FastAPI
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("gestalt.http")

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total", "Total HTTP requests", ["service", "method", "path", "status"]
)
HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds", "HTTP request duration", ["service", "method", "path"]
)


class MetricsMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, service_name: str):
        super().__init__(app)
        self.service_name = service_name

    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start
        path = request.scope.get("route").path if request.scope.get("route") else request.url.path
        HTTP_REQUESTS_TOTAL.labels(self.service_name, request.method, path, response.status_code).inc()
        HTTP_REQUEST_DURATION.labels(self.service_name, request.method, path).observe(duration)
        # Logged *after* call_next returns: RequestIdMiddleware (which wraps
        # inside this one) sets the request-id contextvar during that call,
        # and both middlewares share one asyncio context for the request (no
        # task boundary between them), so it's already populated here
        # regardless of add_middleware registration order.
        logger.info(
            "request_completed",
            extra={
                "extra": {
                    "method": request.method,
                    "path": path,
                    "status": response.status_code,
                    "duration_ms": round(duration * 1000, 2),
                }
            },
        )
        return response


def setup_metrics(app: FastAPI, service_name: str) -> None:
    app.add_middleware(MetricsMiddleware, service_name=service_name)

    @app.get("/metrics", include_in_schema=False)
    async def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
