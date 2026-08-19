from __future__ import annotations

import subprocess
import time
import uuid
from contextlib import contextmanager

import httpx

from conftest import BASE_URLS, ROOT

ORDER = BASE_URLS["order"]
CATALOG = BASE_URLS["catalog"]
CART = BASE_URLS["cart"]


def _get_stock(product_id: str) -> int:
    return httpx.get(f"{CATALOG}/catalog/products/{product_id}").json()["stock"]


def _create_order(auth_headers: dict, body: dict, idem_key: str | None = None) -> httpx.Response:
    idem_key = idem_key or f"test-{uuid.uuid4().hex}"
    headers = {**auth_headers, "Idempotency-Key": idem_key}
    return httpx.post(f"{ORDER}/orders", json=body, headers=headers, timeout=15.0)


@contextmanager
def _payment_failure_rate(rate: str):
    """Flips PAYMENT_FAILURE_RATE for the duration of the `with` block and
    restarts payment-service, restoring the original value on exit. Safe
    under pytest's default sequential (non-parallel) execution -- the
    environment is only "wrong" for the duration of this context, restored
    before control returns for the next test."""
    env_path = ROOT / ".env"
    original = env_path.read_text()
    lines = [l for l in original.splitlines() if not l.startswith("PAYMENT_FAILURE_RATE=")]
    lines.append(f"PAYMENT_FAILURE_RATE={rate}")
    env_path.write_text("\n".join(lines) + "\n")
    subprocess.run(["docker", "compose", "up", "-d", "payment-service"], check=True, cwd=ROOT)
    time.sleep(3)
    try:
        yield
    finally:
        env_path.write_text(original)
        subprocess.run(["docker", "compose", "up", "-d", "payment-service"], check=True, cwd=ROOT)
        time.sleep(3)


def test_happy_path_multi_item_order(auth_headers):
    p1_before = _get_stock("P001")
    p2_before = _get_stock("P002")
    price1 = httpx.get(f"{CATALOG}/catalog/products/P001").json()["price_cents"]
    price2 = httpx.get(f"{CATALOG}/catalog/products/P002").json()["price_cents"]

    r = _create_order(
        auth_headers,
        {"items": [{"productId": "P001", "quantity": 2}, {"productId": "P002", "quantity": 1}]},
    )
    assert r.status_code == 201, r.text
    order = r.json()
    assert order["status"] == "PAID"
    assert order["amountCents"] == 2 * price1 + 1 * price2
    assert _get_stock("P001") == p1_before - 2
    assert _get_stock("P002") == p2_before - 1


def test_insufficient_stock_leaves_order_failed_and_stock_unchanged(auth_headers):
    stock_before = _get_stock("P003")

    # 99_999, not 999_999 -- price_cents * quantity must stay within
    # orders.amount_cents's INT column range or this hits a genuine 500
    # (integer overflow) instead of exercising the insufficient-stock path.
    r = _create_order(auth_headers, {"items": [{"productId": "P003", "quantity": 99_999}]})
    assert r.status_code == 201
    order = r.json()
    assert order["status"] == "FAILED"
    assert order["failureReason"] == "INSUFFICIENT_STOCK"
    assert _get_stock("P003") == stock_before


def test_idempotent_retry_same_key_decrements_stock_once(auth_headers):
    stock_before = _get_stock("P005")
    idem_key = f"test-idem-{uuid.uuid4().hex}"

    r1 = _create_order(auth_headers, {"items": [{"productId": "P005", "quantity": 1}]}, idem_key)
    r2 = _create_order(auth_headers, {"items": [{"productId": "P005", "quantity": 1}]}, idem_key)

    assert r1.json()["id"] == r2.json()["id"]
    assert _get_stock("P005") == stock_before - 1


def test_paid_order_clears_checked_out_cart_items(auth_headers):
    r = httpx.post(f"{CART}/cart/items", headers=auth_headers, json={"productId": "P002", "quantity": 1})
    assert r.status_code == 200

    r = _create_order(auth_headers, {})
    assert r.status_code == 201
    assert r.json()["status"] == "PAID"

    cart = httpx.get(f"{CART}/cart", headers=auth_headers).json()
    assert cart["items"] == []


def test_payment_decline_releases_stock_and_clears_cart(auth_headers):
    r = httpx.post(f"{CART}/cart/items", headers=auth_headers, json={"productId": "P004", "quantity": 1})
    assert r.status_code == 200

    stock_before = _get_stock("P004")

    with _payment_failure_rate("1.0"):
        r = _create_order(auth_headers, {})

    assert r.status_code == 201, r.text
    order = r.json()
    assert order["status"] == "FAILED"
    assert order["failureReason"] == "PAYMENT_DECLINED"
    assert _get_stock("P004") == stock_before

    cart = httpx.get(f"{CART}/cart", headers=auth_headers).json()
    assert cart["items"] == []
