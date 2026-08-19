"""Run ONLY via scripts/test_unsafe_idempotency.sh, never as part of the
main suite (NEXT_STEP_REQUIREMENTS.md §5.2). It requires payment-service
restarted with UNSAFE_IDEMPOTENCY_MODE=true and artificial latency, which
would corrupt every other test relying on the safe default if run in the
same pass -- see scripts/run_tests.sh's explicit --ignore of this file.

This is a regression test in the opposite direction from test_payment.py's
test_concurrent_charges_same_key_no_double_charge: it proves the double
-charge race the safe mode prevents is real and reproducible, so that the
safe-mode test's pass is meaningful protection, not a vacuous assertion.
"""
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx

from conftest import BASE_URLS, internal_headers

PAYMENT = BASE_URLS["payment"]


def test_unsafe_mode_reproduces_double_charge():
    key = f"unsafe-race-{uuid.uuid4().hex}"

    def charge(_: int) -> httpx.Response:
        return httpx.post(
            f"{PAYMENT}/payments/charge",
            json={"orderId": "O-unsafe", "amount": 1000, "currency": "INR", "idempotencyKey": key},
            headers=internal_headers("order-service"),
            timeout=10.0,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(charge, range(8)))

    charged_ats = {r.json()["chargedAt"] for r in responses if r.status_code == 200}
    assert len(charged_ats) > 1, (
        "expected UNSAFE_IDEMPOTENCY_MODE to double-process this key (more than one "
        "distinct chargedAt), but it didn't -- either the race window closed or "
        "unsafe mode isn't actually enabled on the running payment-service container"
    )
