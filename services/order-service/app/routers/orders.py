from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import clients
from app.config import settings
from app.db import get_db
from app.kafka_producer import publish_order_event
from app.metrics import (
    ORDER_AMOUNT_CENTS,
    ORDERS_CREATED_TOTAL,
    ORDERS_FAILED_TOTAL,
    ORDERS_PAID_TOTAL,
    SAGA_PAYMENT_FAILURES_TOTAL,
    SAGA_STOCK_RESERVATION_FAILURES_TOTAL,
    normalize_failure_reason,
)
from app.models import Order, OrderItem, OrderStatus
from app.schemas import CreateOrderRequest, OrderItemOut, OrderListOut, OrderOut
from app.security import current_user_dependency
from gestalt_shared.errors import AppError
from gestalt_shared.security import TokenClaims

logger = logging.getLogger("gestalt.order-service.saga")

router = APIRouter(prefix="/orders", tags=["orders"])


def _to_out(order: Order, items: list[OrderItem]) -> OrderOut:
    return OrderOut(
        id=order.id,
        userId=order.user_id,
        status=order.status.value,
        amountCents=order.amount_cents,
        items=[OrderItemOut(productId=i.product_id, quantity=i.quantity) for i in items],
        failureReason=order.failure_reason,
        createdAt=order.created_at.isoformat(),
    )


def _load_items(db: Session, order_id: str) -> list[OrderItem]:
    return list(db.execute(select(OrderItem).where(OrderItem.order_id == order_id)).scalars().all())


@router.post("", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
def create_order(
    body: CreateOrderRequest,
    db: Session = Depends(get_db),
    user: TokenClaims = Depends(current_user_dependency),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    authorization: str = Header(default=""),
):
    if not idempotency_key:
        raise AppError("MISSING_IDEMPOTENCY_KEY", "Idempotency-Key header is required", 400)

    existing = db.execute(
        select(Order).where(Order.idempotency_key == idempotency_key)
    ).scalar_one_or_none()
    if existing:
        # Idempotent replay: the original request already ran the saga
        # (including its cart-clear call), so this path doesn't repeat it.
        return _to_out(existing, _load_items(db, existing.id))

    # ---- 1. Resolve items (explicit body, else the caller's cart) ----
    if body.items:
        raw_items = [(i.productId, i.quantity) for i in body.items]
    else:
        cart_items = clients.get_cart_items(authorization)
        if not cart_items:
            raise AppError("EMPTY_CART", "Cart is empty, nothing to check out", 400)
        raw_items = [(i["productId"], i["quantity"]) for i in cart_items]

    # ---- 2. Price snapshot (also validates every product exists) ----
    amount_cents = 0
    for product_id, quantity in raw_items:
        price = clients.get_price(product_id)
        amount_cents += price["price_cents"] * quantity

    # ---- 3. Persist PENDING order; DB unique constraint is the real race
    # guard for two near-simultaneous retries of the same Idempotency-Key
    # (order-service.md) ----
    order = Order(user_id=user.user_id, amount_cents=amount_cents, idempotency_key=idempotency_key)
    db.add(order)
    try:
        db.flush()
        for product_id, quantity in raw_items:
            db.add(OrderItem(order_id=order.id, product_id=product_id, quantity=quantity))
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.execute(
            select(Order).where(Order.idempotency_key == idempotency_key)
        ).scalar_one_or_none()
        if existing:
            return _to_out(existing, _load_items(db, existing.id))
        raise

    ORDERS_CREATED_TOTAL.inc()
    publish_order_event(
        "order.created",
        order.id,
        {
            "orderId": order.id,
            "userId": order.user_id,
            "status": "PENDING",
            "items": [{"productId": p, "quantity": q} for p, q in raw_items],
            "amount": amount_cents,
        },
    )

    # ---- 4. Reserve stock for every line item ----
    reserved_so_far: list[tuple[str, int]] = []
    reservation_failure: str | None = None
    for product_id, quantity in raw_items:
        result = clients.reserve_stock(product_id, quantity, order.id)
        if not result.get("reserved"):
            reservation_failure = result.get("reason", "reservation_failed")
            break
        reserved_so_far.append((product_id, quantity))

    if reservation_failure:
        # Compensate whatever was reserved before the failure.
        for product_id, quantity in reserved_so_far:
            clients.release_stock(product_id, quantity, order.id, reason="reservation_failed_partial_rollback")
        order.status = OrderStatus.FAILED
        order.failure_reason = normalize_failure_reason(reservation_failure)
        db.commit()
        SAGA_STOCK_RESERVATION_FAILURES_TOTAL.inc()
        ORDERS_FAILED_TOTAL.labels(reason=order.failure_reason).inc()
        logger.info(
            "saga_transition",
            extra={
                "extra": {
                    "order_id": order.id,
                    "from_status": "PENDING",
                    "to_status": "FAILED",
                    "reason": order.failure_reason,
                }
            },
        )
        publish_order_event(
            "order.failed",
            order.id,
            {"orderId": order.id, "userId": order.user_id, "reason": order.failure_reason},
        )
        clients.clear_cart_items(order.user_id, [p for p, _ in raw_items])
        return _to_out(order, _load_items(db, order.id))

    order.stock_reserved = True
    db.commit()

    # ---- 5. Charge, using the same idempotency key end-to-end ----
    charge_result = clients.charge(order.id, order.amount_cents, "INR", order.idempotency_key)

    if not charge_result.get("charged"):
        for product_id, quantity in raw_items:
            clients.release_stock(product_id, quantity, order.id, reason="payment_failed")
        order.status = OrderStatus.FAILED
        order.failure_reason = normalize_failure_reason(charge_result.get("reason", "payment_failed"))
        db.commit()
        SAGA_PAYMENT_FAILURES_TOTAL.inc()
        ORDERS_FAILED_TOTAL.labels(reason=order.failure_reason).inc()
        logger.info(
            "saga_transition",
            extra={
                "extra": {
                    "order_id": order.id,
                    "from_status": "PENDING",
                    "to_status": "FAILED",
                    "reason": order.failure_reason,
                }
            },
        )
        publish_order_event(
            "order.failed",
            order.id,
            {"orderId": order.id, "userId": order.user_id, "reason": order.failure_reason},
        )
        clients.clear_cart_items(order.user_id, [p for p, _ in raw_items])
        return _to_out(order, _load_items(db, order.id))

    order.status = OrderStatus.PAID
    db.commit()
    ORDERS_PAID_TOTAL.inc()
    ORDER_AMOUNT_CENTS.observe(order.amount_cents)
    logger.info(
        "saga_transition",
        extra={"extra": {"order_id": order.id, "from_status": "PENDING", "to_status": "PAID"}},
    )
    publish_order_event(
        "order.paid",
        order.id,
        {
            "orderId": order.id,
            "userId": order.user_id,
            "amount": order.amount_cents,
            "paidAt": datetime.now(timezone.utc).isoformat(),
        },
    )
    clients.clear_cart_items(order.user_id, [p for p, _ in raw_items])
    return _to_out(order, _load_items(db, order.id))


@router.get("/{order_id}", response_model=OrderOut)
def get_order(order_id: str, db: Session = Depends(get_db), user: TokenClaims = Depends(current_user_dependency)):
    order = db.get(Order, order_id)
    if order is None or order.user_id != user.user_id:
        raise AppError("ORDER_NOT_FOUND", f"No order with id {order_id}", 404)
    return _to_out(order, _load_items(db, order.id))


@router.get("", response_model=OrderListOut)
def list_orders(
    db: Session = Depends(get_db),
    user: TokenClaims = Depends(current_user_dependency),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    orders = list(
        db.execute(
            select(Order)
            .where(Order.user_id == user.user_id)
            .order_by(Order.created_at.desc())
            .limit(limit)
            .offset(offset)
        ).scalars()
    )
    return OrderListOut(items=[_to_out(o, _load_items(db, o.id)) for o in orders])
