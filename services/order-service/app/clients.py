"""Outbound calls to catalog-service, payment-service, and cart-service.

Expected business failures (insufficient stock, a declined charge) are
returned as plain dicts for the saga to interpret -- only transport-level
problems or a bad request (unknown product at price-lookup time, before
anything is persisted) raise AppError.

All calls go through a client built by gestalt_shared.http_client, which
auto-attaches the current request's x-request-id header
(NEXT_STEP_REQUIREMENTS.md §2) -- no call site can forget it.
"""
from __future__ import annotations

import logging

import httpx

from app.config import settings
from gestalt_shared.errors import AppError
from gestalt_shared.http_client import make_internal_http_client

logger = logging.getLogger("gestalt.order-service.clients")

_client = make_internal_http_client(settings.http_timeout_seconds)

INTERNAL_HEADERS = {
    "X-Internal-Token": settings.internal_service_token,
    "X-Internal-Caller": "order-service",
}


def get_price(product_id: str) -> dict:
    try:
        r = _client.get(
            f"{settings.catalog_service_url}/catalog/products/{product_id}/price",
            headers=INTERNAL_HEADERS,
        )
    except httpx.HTTPError as exc:
        raise AppError("CATALOG_UNAVAILABLE", f"catalog-service is unavailable: {exc}", 503) from exc

    if r.status_code == 404:
        raise AppError("PRODUCT_NOT_FOUND", f"No product with id {product_id}", 404)
    if r.status_code != 200:
        raise AppError("CATALOG_ERROR", "catalog-service returned an unexpected error", 502)
    return r.json()


def reserve_stock(product_id: str, quantity: int, order_id: str) -> dict:
    try:
        r = _client.post(
            f"{settings.catalog_service_url}/catalog/stock/reserve",
            json={"productId": product_id, "quantity": quantity, "orderId": order_id},
            headers=INTERNAL_HEADERS,
        )
    except httpx.HTTPError as exc:
        return {"reserved": False, "reason": f"transport_error:{exc}"}

    if r.status_code == 200:
        return {"reserved": True}
    if r.status_code == 409:
        return {"reserved": False, "reason": "insufficient_stock"}
    if r.status_code == 404:
        return {"reserved": False, "reason": "product_not_found"}
    return {"reserved": False, "reason": f"upstream_error_{r.status_code}"}


def release_stock(product_id: str, quantity: int, order_id: str, reason: str) -> None:
    """Best-effort compensating action. A failure here is logged, not raised
    -- blocking the client response on a compensation call would trade a
    saga bug for a worse one (a hung request)."""
    logger.info(
        "compensating_stock_release",
        extra={
            "extra": {
                "order_id": order_id,
                "product_id": product_id,
                "quantity": quantity,
                "reason": reason,
            }
        },
    )
    try:
        _client.post(
            f"{settings.catalog_service_url}/catalog/stock/release",
            json={"productId": product_id, "quantity": quantity, "orderId": order_id},
            headers=INTERNAL_HEADERS,
        )
    except httpx.HTTPError:
        logger.warning(
            "compensating_stock_release_failed",
            extra={"extra": {"order_id": order_id, "product_id": product_id}},
        )


def charge(order_id: str, amount_cents: int, currency: str, idempotency_key: str) -> dict:
    try:
        r = _client.post(
            f"{settings.payment_service_url}/payments/charge",
            json={
                "orderId": order_id,
                "amount": amount_cents,
                "currency": currency,
                "idempotencyKey": idempotency_key,
            },
            headers=INTERNAL_HEADERS,
        )
    except httpx.HTTPError as exc:
        return {"charged": False, "reason": f"transport_error:{exc}"}

    if r.status_code == 200:
        return {"charged": True}
    if r.status_code == 402:
        reason = r.json().get("error", {}).get("code", "payment_declined")
        return {"charged": False, "reason": reason}
    return {"charged": False, "reason": f"upstream_error_{r.status_code}"}


def get_cart_items(authorization: str) -> list[dict]:
    try:
        r = _client.get(
            f"{settings.cart_service_url}/cart",
            headers={"Authorization": authorization},
        )
    except httpx.HTTPError as exc:
        raise AppError("CART_SERVICE_UNAVAILABLE", f"cart-service is unavailable: {exc}", 503) from exc

    if r.status_code != 200:
        raise AppError("CART_UNAVAILABLE", "Unable to retrieve cart", 502)
    return r.json().get("items", [])


def clear_cart_items(user_id: str, product_ids: list[str]) -> None:
    """Fire-and-forget (NEXT_STEP_REQUIREMENTS.md §3.3): a failure here must
    never fail or roll back an otherwise-terminal order."""
    if not product_ids:
        return
    try:
        # httpx's .delete() convenience method doesn't accept a body; use
        # .request() directly since this DELETE needs a JSON payload.
        r = _client.request(
            "DELETE",
            f"{settings.cart_service_url}/cart/items:batch",
            json={"productIds": product_ids},
            headers={
                **INTERNAL_HEADERS,
                "X-User-Id": user_id,
            },
        )
        if r.status_code != 200:
            logger.warning(
                "cart_clear_failed",
                extra={"extra": {"user_id": user_id, "status_code": r.status_code}},
            )
    except httpx.HTTPError as exc:
        logger.warning(
            "cart_clear_failed",
            extra={"extra": {"user_id": user_id, "error": str(exc)}},
        )
