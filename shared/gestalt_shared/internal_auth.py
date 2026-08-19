"""Stand-in for Istio's service-identity AuthorizationPolicy (docs/04, the
matrix restricting e.g. payment-service to callers whose *mesh identity* is
order-service). Without a mesh/mTLS locally, identity has to come from
somewhere else: every internal caller presents a shared bearer token plus a
declared caller name, and the callee whitelists caller names per endpoint.

This is intentionally simple and is explicitly NOT a substitute for the real
AuthorizationPolicy -- it's here so the app-level authorization *shape*
(e.g. "only order-service may call POST /payments/charge") is enforceable
and demoable before the Istio phase of the project exists.
"""
from __future__ import annotations

from fastapi import Header

from gestalt_shared.errors import AppError


def make_internal_caller_dependency(shared_token: str, allowed_callers: list[str]):
    async def require_internal_caller(
        x_internal_token: str = Header(default=""),
        x_internal_caller: str = Header(default=""),
    ) -> str:
        if not shared_token or x_internal_token != shared_token:
            raise AppError("FORBIDDEN", "Invalid internal service token", 403)
        if x_internal_caller not in allowed_callers:
            raise AppError(
                "FORBIDDEN",
                f"Caller '{x_internal_caller}' is not authorized for this endpoint",
                403,
            )
        return x_internal_caller

    return require_internal_caller
