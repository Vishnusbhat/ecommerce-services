"""Liveness/readiness probe router, matching docs/03-kubernetes-deployment.md.

readinessProbe -> /healthz/ready (should reflect real dependency health, e.g.
DB connectivity -- see chaos scenario 3 in docs/06, which specifically calls
out that most tutorials skip DB checks in readiness probes).
livenessProbe  -> /healthz/live (process is up, nothing more).
"""
from __future__ import annotations

from typing import Callable

from fastapi import APIRouter
from starlette.responses import JSONResponse


def build_health_router(ready_check: Callable[[], bool] | None = None) -> APIRouter:
    router = APIRouter(tags=["health"])

    @router.get("/healthz/live")
    async def live():
        return {"status": "ok"}

    @router.get("/healthz/ready")
    async def ready():
        if ready_check is not None:
            try:
                ok = ready_check()
            except Exception:
                ok = False
            if not ok:
                return JSONResponse(status_code=503, content={"status": "not_ready"})
        return {"status": "ready"}

    return router
