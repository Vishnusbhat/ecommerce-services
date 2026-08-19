"""Periodic sweep for sagas that crashed mid-flight (order-service.md:
"a simple periodic reconciliation job... is the right scope versus a full
transactional outbox"). A PENDING order older than the timeout either had
stock reserved (release it) or didn't (nothing to compensate) -- either way
it gets force-failed so it stops counting against the "stuck in PENDING"
alert in docs/05-observability-stack.md.

Also clears the affected cart items (NEXT_STEP_REQUIREMENTS.md §3.3, applied
uniformly to every PAID/FAILED terminal state, including this one).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app import clients
from app.config import settings
from app.db import SessionLocal
from app.kafka_producer import publish_order_event
from app.metrics import ORDERS_FAILED_TOTAL, normalize_failure_reason
from app.models import Order, OrderItem, OrderStatus

logger = logging.getLogger("gestalt.order-service.reconciliation")


def run_pass() -> int:
    db = SessionLocal()
    reconciled = 0
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.pending_order_timeout_seconds)
        stuck = db.execute(
            select(Order).where(Order.status == OrderStatus.PENDING, Order.created_at < cutoff)
        ).scalars().all()

        for order in stuck:
            items = db.execute(select(OrderItem).where(OrderItem.order_id == order.id)).scalars().all()
            if order.stock_reserved:
                for item in items:
                    clients.release_stock(
                        item.product_id, item.quantity, order.id, reason="reconciliation_timeout"
                    )

            order.status = OrderStatus.FAILED
            order.failure_reason = normalize_failure_reason("reconciliation_timeout")
            db.commit()
            ORDERS_FAILED_TOTAL.labels(reason=order.failure_reason).inc()
            publish_order_event(
                "order.failed",
                order.id,
                {"orderId": order.id, "userId": order.user_id, "reason": order.failure_reason},
            )
            logger.warning(
                "reconciled_stuck_order",
                extra={"extra": {"job": "reconciliation", "order_id": order.id}},
            )
            clients.clear_cart_items(order.user_id, [i.product_id for i in items])
            reconciled += 1
    finally:
        db.close()
    return reconciled


async def reconciliation_loop() -> None:
    while True:
        await asyncio.sleep(settings.reconciliation_interval_seconds)
        try:
            run_pass()
        except Exception:
            logger.exception("reconciliation_pass_failed", extra={"extra": {"job": "reconciliation"}})
