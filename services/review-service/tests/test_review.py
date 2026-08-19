"""Purchase-eligibility gating (NEXT_STEP_REQUIREMENTS.md §5.2).

Waits out order-service's real delivery simulator (default ~30s delay +
up to a 10s check interval) rather than adding new tunability -- unlike
cart_abandonment_total, the Definition of Done doesn't ask this path to be
speedable, so this just polls with a generous deadline instead.
"""
from __future__ import annotations

import time
import uuid

import httpx

from conftest import BASE_URLS

ORDER = BASE_URLS["order"]
REVIEW = BASE_URLS["review"]


def test_review_gated_on_delivery_then_blocks_duplicate(auth_headers):
    idem_key = f"test-review-{uuid.uuid4().hex}"
    r = httpx.post(
        f"{ORDER}/orders",
        json={"items": [{"productId": "P002", "quantity": 1}]},
        headers={**auth_headers, "Idempotency-Key": idem_key},
        timeout=15.0,
    )
    assert r.status_code == 201, r.text
    order = r.json()
    assert order["status"] == "PAID"

    # Before delivery: rejected.
    r = httpx.post(
        f"{REVIEW}/reviews",
        headers=auth_headers,
        json={"productId": "P002", "orderId": order["id"], "rating": 5, "comment": "too soon"},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "NOT_ELIGIBLE"

    # After delivery: accepted. Poll rather than sleep-a-fixed-amount, since
    # the exact firing time depends on the simulator's check interval too.
    deadline = time.time() + 60
    accepted_body = None
    while time.time() < deadline and accepted_body is None:
        r = httpx.post(
            f"{REVIEW}/reviews",
            headers=auth_headers,
            json={"productId": "P002", "orderId": order["id"], "rating": 5, "comment": "great"},
        )
        if r.status_code == 201:
            accepted_body = r.json()
        else:
            time.sleep(3)

    assert accepted_body is not None, "review was never accepted -- delivery event may not have fired in time"

    # Second attempt for the same product: already reviewed.
    r = httpx.post(
        f"{REVIEW}/reviews",
        headers=auth_headers,
        json={"productId": "P002", "orderId": order["id"], "rating": 3, "comment": "changed my mind"},
    )
    assert r.status_code == 403
