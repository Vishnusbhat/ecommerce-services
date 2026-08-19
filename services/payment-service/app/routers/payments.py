from __future__ import annotations

import json
import logging
import random
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from prometheus_client import Counter

from app.config import settings
from app.redis_client import client as redis_client
from app.schemas import ChargeRequest, ChargeResponse
from gestalt_shared.errors import AppError
from gestalt_shared.internal_auth import make_internal_caller_dependency

logger = logging.getLogger("gestalt.payment-service")

router = APIRouter(prefix="/payments", tags=["payments"])

require_order_service = make_internal_caller_dependency(
    settings.internal_service_token, allowed_callers=["order-service"]
)

PAYMENT_FAILURES_TOTAL = Counter(
    "payment_failures_total", "Synthetic/processed payment failures", ["reason"]
)
PAYMENT_IDEMPOTENT_REPLAYS_TOTAL = Counter(
    "payment_idempotent_replays_total",
    "Charge requests served from the idempotency cache instead of freshly processed",
)


def _key(idempotency_key: str) -> str:
    return f"idempotency:{idempotency_key}"


def _process_charge(body: ChargeRequest) -> dict:
    latency_s = random.uniform(settings.latency_ms_min, settings.latency_ms_max) / 1000
    if latency_s > 0:
        time.sleep(latency_s)

    if random.random() < settings.failure_rate:
        PAYMENT_FAILURES_TOTAL.labels(reason="SYNTHETIC_DECLINE").inc()
        return {
            "success": False,
            "code": "PAYMENT_DECLINED",
            "message": "Synthetic payment failure (chaos testing)",
        }

    return {
        "success": True,
        "orderId": body.orderId,
        "amount": body.amount,
        "currency": body.currency,
        "chargedAt": datetime.now(timezone.utc).isoformat(),
    }


def _respond(idempotency_key: str, result: dict) -> ChargeResponse:
    if result.get("success"):
        return ChargeResponse(
            orderId=result["orderId"],
            amount=result["amount"],
            currency=result["currency"],
            idempotencyKey=idempotency_key,
            status="CHARGED",
            chargedAt=result["chargedAt"],
        )
    raise AppError(result.get("code", "PAYMENT_DECLINED"), result.get("message", "Payment declined"), 402)


def _wait_for_result(idempotency_key: str, attempts: int = 10, interval_s: float = 0.2) -> dict:
    """Another concurrent request is already processing this key -- poll
    briefly for it to settle rather than starting a second charge."""
    for _ in range(attempts):
        raw = redis_client.get(_key(idempotency_key))
        if raw:
            stored = json.loads(raw)
            if stored.get("status") != "IN_PROGRESS":
                return stored
        time.sleep(interval_s)
    raise AppError("CHARGE_IN_PROGRESS", "Charge for this idempotency key is still processing", 409)


@router.post("/charge", response_model=ChargeResponse, dependencies=[Depends(require_order_service)])
def charge(body: ChargeRequest):
    key = _key(body.idempotencyKey)

    if settings.unsafe_idempotency_mode:
        # Deliberately reintroducible bug (payment-service.md): GET-then-process
        # -then-SET has a window where two near-simultaneous retries both pass
        # the GET-miss check and both charge. Never enable outside a chaos demo.
        existing = redis_client.get(key)
        if existing:
            PAYMENT_IDEMPOTENT_REPLAYS_TOTAL.inc()
            logger.info(
                "idempotent_replay",
                extra={"extra": {"idempotency_key": body.idempotencyKey, "mode": "unsafe"}},
            )
            return _respond(body.idempotencyKey, json.loads(existing))
        result = _process_charge(body)
        redis_client.set(key, json.dumps(result), ex=settings.idempotency_ttl_seconds)
        return _respond(body.idempotencyKey, result)

    # Safe version: atomically claim the key before any processing starts.
    claimed = redis_client.set(
        key, json.dumps({"status": "IN_PROGRESS"}), nx=True, ex=settings.idempotency_ttl_seconds
    )
    if not claimed:
        result = _wait_for_result(body.idempotencyKey)
        PAYMENT_IDEMPOTENT_REPLAYS_TOTAL.inc()
        logger.info(
            "idempotent_replay",
            extra={"extra": {"idempotency_key": body.idempotencyKey, "mode": "safe"}},
        )
        return _respond(body.idempotencyKey, result)

    result = _process_charge(body)
    redis_client.set(key, json.dumps(result), ex=settings.idempotency_ttl_seconds)
    return _respond(body.idempotencyKey, result)


@router.get("/{idempotency_key}", response_model=ChargeResponse, dependencies=[Depends(require_order_service)])
def get_result(idempotency_key: str):
    raw = redis_client.get(_key(idempotency_key))
    if not raw:
        raise AppError("NOT_FOUND", "No charge found for this idempotency key", 404)
    stored = json.loads(raw)
    if stored.get("status") == "IN_PROGRESS":
        raise AppError("CHARGE_IN_PROGRESS", "Charge for this idempotency key is still processing", 409)
    return _respond(idempotency_key, stored)
