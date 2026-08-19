"""Periodic sweep for sagas that crashed mid-flight (order-service.md:
"a simple periodic reconciliation job... is the right scope versus a full
transactional outbox"). A PENDING order older than the timeout either had
stock reserved (release it) or didn't (nothing to compensate) -- either way
it gets force-failed so it stops counting against the "stuck in PENDING"
alert in docs/05-observability-stack.md.
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
                    clients.release_stock(item.product_id, item.quantity, order.id)

            order.status = OrderStatus.FAILED
            order.failure_reason = "reconciliation_timeout"
            db.commit()
            publish_order_event(
                "order.failed",
                order.id,
                {"orderId": order.id, "userId": order.user_id, "reason": "reconciliation_timeout"},
            )
            logger.warning("reconciled_stuck_order order_id=%s", order.id)
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
            logger.exception("reconciliation_pass_failed")
