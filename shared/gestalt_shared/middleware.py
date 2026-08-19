"""Request-ID propagation.

In the real system, Envoy auto-injects x-request-id at the ingress gateway
(docs/02-api-contracts.md). There is no Envoy in local docker-compose, so this
middleware plays that role: honor an inbound x-request-id if present (so a
trace started by a caller keeps its id), otherwise mint one.
"""
from __future__ import annotations

import contextvars
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

_request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")


def get_current_request_id() -> str:
    """Empty string outside a request context (background jobs, Kafka
    consumer threads) -- see gestalt_shared/logging.py for why that's
    correct with zero special-casing."""
    return _request_id_ctx.get()


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:8]
        request.state.request_id = request_id
        _request_id_ctx.set(request_id)
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response
