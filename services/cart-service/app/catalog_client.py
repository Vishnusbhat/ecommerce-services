import httpx

from app.config import settings
from gestalt_shared.errors import AppError

INTERNAL_HEADERS = {
    "X-Internal-Token": settings.internal_service_token,
    "X-Internal-Caller": "cart-service",
}


def get_price_and_stock(product_id: str) -> dict:
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
