"""Outbound calls to catalog-service, payment-service, and cart-service.

Expected business failures (insufficient stock, a declined charge) are
returned as plain dicts for the saga to interpret -- only transport-level
problems or a bad request (unknown product at price-lookup time, before
anything is persisted) raise AppError.
"""
from __future__ import annotations

import httpx

from app.config import settings
from gestalt_shared.errors import AppError

INTERNAL_HEADERS = {
    "X-Internal-Token": settings.internal_service_token,
    "X-Internal-Caller": "order-service",
}


def get_price(product_id: str) -> dict:
    try:
        r = httpx.get(
            f"{settings.catalog_service_url}/catalog/products/{product_id}/price",
            headers=INTERNAL_HEADERS,
            timeout=settings.http_timeout_seconds,
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
        r = httpx.post(
            f"{settings.catalog_service_url}/catalog/stock/reserve",
            json={"productId": product_id, "quantity": quantity, "orderId": order_id},
            headers=INTERNAL_HEADERS,
            timeout=settings.http_timeout_seconds,
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


def release_stock(product_id: str, quantity: int, order_id: str) -> None:
    """Best-effort compensating action. A failure here is logged, not raised
    -- blocking the client response on a compensation call would trade a
    saga bug for a worse one (a hung request)."""
    try:
        httpx.post(
            f"{settings.catalog_service_url}/catalog/stock/release",
            json={"productId": product_id, "quantity": quantity, "orderId": order_id},
            headers=INTERNAL_HEADERS,
            timeout=settings.http_timeout_seconds,
        )
    except httpx.HTTPError:
        pass


def charge(order_id: str, amount_cents: int, currency: str, idempotency_key: str) -> dict:
    try:
        r = httpx.post(
            f"{settings.payment_service_url}/payments/charge",
            json={
                "orderId": order_id,
                "amount": amount_cents,
                "currency": currency,
                "idempotencyKey": idempotency_key,
            },
            headers=INTERNAL_HEADERS,
            timeout=settings.http_timeout_seconds,
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
        r = httpx.get(
            f"{settings.cart_service_url}/cart",
            headers={"Authorization": authorization},
            timeout=settings.http_timeout_seconds,
        )
    except httpx.HTTPError as exc:
        raise AppError("CART_SERVICE_UNAVAILABLE", f"cart-service is unavailable: {exc}", 503) from exc

    if r.status_code != 200:
        raise AppError("CART_UNAVAILABLE", "Unable to retrieve cart", 502)
    return r.json().get("items", [])
