"""Cart-abandonment scan (NEXT_STEP_REQUIREMENTS.md §1.3).

Definition: a cart has items, has not been modified (no add/remove/batch
-remove call, tracked via the _updated_at field cart writes refresh -- not
Redis TTL/OBJECT IDLETIME, which the doc explicitly says would be wrong
since it's refreshed by unrelated reads too) for the threshold window, and
hasn't been marked counted yet. Each qualifying cart increments the counter
exactly once, then gets flagged so a later scan of the same still-idle cart
never double-counts it.
"""
from __future__ import annotations

import asyncio
import logging
import time

from app.config import settings
from app.metrics import CART_ABANDONMENT_TOTAL
from app.redis_client import ABANDONMENT_COUNTED_FIELD, META_FIELDS, UPDATED_AT_FIELD
from app.redis_client import client as redis_client

logger = logging.getLogger("gestalt.cart-service.abandonment")


def run_pass() -> int:
    now = time.time()
    counted = 0
    for key in redis_client.scan_iter(match="cart:*"):
        cart = redis_client.hgetall(key)
        if not cart:
            continue
        if cart.get(ABANDONMENT_COUNTED_FIELD):
            continue

        has_items = any(field not in META_FIELDS for field in cart)
        if not has_items:
            continue

        updated_at = float(cart.get(UPDATED_AT_FIELD, 0))
        if now - updated_at < settings.cart_abandonment_threshold_seconds:
            continue

        CART_ABANDONMENT_TOTAL.inc()
        redis_client.hset(key, ABANDONMENT_COUNTED_FIELD, "1")
        logger.info("cart_abandoned", extra={"extra": {"job": "cart-abandonment", "cart_key": key}})
        counted += 1
    return counted


async def abandonment_loop() -> None:
    while True:
        await asyncio.sleep(settings.cart_abandonment_scan_interval_seconds)
        try:
            run_pass()
        except Exception:
            logger.exception("abandonment_pass_failed", extra={"extra": {"job": "cart-abandonment"}})
