"""Simulates delivery for PAID orders (order-service.md / docs/02-api-contracts.md:
order.delivered is "simulated via a delay/cron for demo purposes", there is
no real shipping pipeline in this project). review-service consumes this
event to populate its purchase-verification eligibility collection.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.kafka_producer import publish_order_event
from app.models import Order, OrderItem, OrderStatus

logger = logging.getLogger("gestalt.order-service.delivery_simulator")


def run_pass() -> int:
    db = SessionLocal()
    delivered_count = 0
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.delivery_simulation_delay_seconds)
        due = db.execute(
            select(Order).where(
                Order.status == OrderStatus.PAID,
                Order.delivered.is_(False),
                Order.updated_at < cutoff,
            )
        ).scalars().all()

        for order in due:
            items = db.execute(select(OrderItem).where(OrderItem.order_id == order.id)).scalars().all()
            product_ids = [i.product_id for i in items]
            delivered_at = datetime.now(timezone.utc)

            order.delivered = True
            db.commit()
            publish_order_event(
                "order.delivered",
                order.id,
                {
                    "orderId": order.id,
                    "userId": order.user_id,
                    "productIds": product_ids,
                    "deliveredAt": delivered_at.isoformat(),
                },
            )
            delivered_count += 1
    finally:
        db.close()
    return delivered_count


async def delivery_simulation_loop() -> None:
    while True:
        await asyncio.sleep(settings.delivery_check_interval_seconds)
        try:
            run_pass()
        except Exception:
            logger.exception("delivery_simulation_pass_failed")
