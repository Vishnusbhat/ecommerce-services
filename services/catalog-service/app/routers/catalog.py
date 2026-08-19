from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.cache import client as redis_client
from app.cache import get_cached_product, invalidate_product, set_cached_product
from app.config import settings
from app.db import engine, get_db
from app.models import Product
from app.schemas import (
    PriceOut,
    ProductListOut,
    ProductOut,
    ReleaseRequest,
    ReserveRequest,
    ReserveResponse,
)
from gestalt_shared.errors import AppError
from gestalt_shared.internal_auth import make_internal_caller_dependency

router = APIRouter(prefix="/catalog", tags=["catalog"])

require_order_service = make_internal_caller_dependency(
    settings.internal_service_token, allowed_callers=["order-service"]
)
require_price_reader = make_internal_caller_dependency(
    settings.internal_service_token, allowed_callers=["cart-service", "order-service"]
)


def _to_out(p: Product) -> ProductOut:
    return ProductOut(id=p.id, name=p.name, price_cents=p.price_cents, stock=p.stock)


@router.get("/products", response_model=ProductListOut)
def list_products(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    cache_key = f"products:list:{limit}:{offset}"
    cached = redis_client.get(cache_key)
    if cached:
        return ProductListOut(**json.loads(cached))

    total = db.execute(select(Product)).scalars().all()
    page = db.execute(select(Product).order_by(Product.id).limit(limit).offset(offset)).scalars().all()
    result = ProductListOut(
        items=[_to_out(p) for p in page], total=len(total), limit=limit, offset=offset
    )
    redis_client.set(cache_key, result.model_dump_json(), ex=settings.product_cache_ttl_seconds)
    return result


@router.get("/products/{product_id}", response_model=ProductOut)
def get_product(product_id: str, db: Session = Depends(get_db)):
    cached = get_cached_product(product_id)
    if cached:
        return ProductOut(**cached)

    product = db.get(Product, product_id)
    if product is None:
        raise AppError("PRODUCT_NOT_FOUND", f"No product with id {product_id}", 404)

    out = _to_out(product)
    set_cached_product(product_id, out.model_dump())
    return out


@router.get("/products/{product_id}/price", response_model=PriceOut, dependencies=[Depends(require_price_reader)])
def get_price(product_id: str, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if product is None:
        raise AppError("PRODUCT_NOT_FOUND", f"No product with id {product_id}", 404)
    return PriceOut(id=product.id, price_cents=product.price_cents, stock=product.stock)


@router.post("/stock/reserve", response_model=ReserveResponse, dependencies=[Depends(require_order_service)])
def reserve_stock(body: ReserveRequest):
    # Row-level lock for the transaction duration: two concurrent checkouts for
    # the last unit can't both read "1 available" and both succeed -- the
    # second blocks until the first commits, then sees the updated count.
    # (catalog-service.md / the exact overselling race the SDI prep flagged.)
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT stock FROM products WHERE id = :id FOR UPDATE"), {"id": body.productId}
        ).fetchone()
        if row is None:
            raise AppError("PRODUCT_NOT_FOUND", f"No product with id {body.productId}", 404)
        if row.stock < body.quantity:
            raise AppError(
                "INSUFFICIENT_STOCK", "Requested quantity exceeds available stock", 409
            )
        conn.execute(
            text("UPDATE products SET stock = stock - :q WHERE id = :id"),
            {"q": body.quantity, "id": body.productId},
        )
        remaining = row.stock - body.quantity

    invalidate_product(body.productId)
    return ReserveResponse(
        productId=body.productId, orderId=body.orderId, reserved=True, remainingStock=remaining
    )


@router.post("/stock/release", response_model=ReserveResponse, dependencies=[Depends(require_order_service)])
def release_stock(body: ReleaseRequest):
    with engine.begin() as conn:
        result = conn.execute(
            text("UPDATE products SET stock = stock + :q WHERE id = :id"),
            {"q": body.quantity, "id": body.productId},
        )
        if result.rowcount == 0:
            raise AppError("PRODUCT_NOT_FOUND", f"No product with id {body.productId}", 404)
        row = conn.execute(
            text("SELECT stock FROM products WHERE id = :id"), {"id": body.productId}
        ).fetchone()

    invalidate_product(body.productId)
    return ReserveResponse(
        productId=body.productId, orderId=body.orderId, reserved=False, remainingStock=row.stock
    )
