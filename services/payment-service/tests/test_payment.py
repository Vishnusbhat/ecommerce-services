import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx

from conftest import BASE_URLS, internal_headers

PAYMENT = BASE_URLS["payment"]


def _charge(idempotency_key: str, order_id: str = "O-test", amount: int = 1000) -> httpx.Response:
    return httpx.post(
        f"{PAYMENT}/payments/charge",
        json={"orderId": order_id, "amount": amount, "currency": "INR", "idempotencyKey": idempotency_key},
        headers=internal_headers("order-service"),
        timeout=10.0,
    )


def test_idempotent_replay_returns_identical_result():
    key = f"test-{uuid.uuid4().hex}"
    r1 = _charge(key)
    r2 = _charge(key)
    assert r1.status_code == r2.status_code == 200
    assert r1.json() == r2.json()


def test_concurrent_charges_same_key_no_double_charge():
    """Must pass under UNSAFE_IDEMPOTENCY_MODE=false (the default). Asserts
    byte-identical response bodies, not just "no error" -- the naive unsafe
    implementation could coincidentally avoid raising while still having
    double-processed the charge (NEXT_STEP_REQUIREMENTS.md §5.2)."""
    key = f"test-race-{uuid.uuid4().hex}"

    with ThreadPoolExecutor(max_workers=5) as pool:
        responses = list(pool.map(lambda _: _charge(key), range(5)))

    bodies = [r.json() for r in responses]
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    assert all(b == bodies[0] for b in bodies), bodies
