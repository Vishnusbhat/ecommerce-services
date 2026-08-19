import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
import pymysql
import pytest

from conftest import BASE_URLS, internal_headers

CATALOG = BASE_URLS["catalog"]


@pytest.fixture
def low_stock_product():
    """Direct-SQL seed (there's no product-creation HTTP endpoint) of a
    throwaway product with stock=1, so the concurrency test controls the
    exact race window instead of relying on the shared demo catalog."""
    product_id = f"TEST{uuid.uuid4().hex[:8].upper()}"
    conn = pymysql.connect(
        host="localhost", port=3307, user="root", password="rootpass", database="catalog_db"
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO products (id, name, price_cents, stock) VALUES (%s, %s, %s, %s)",
                (product_id, "Concurrency Test Product", 1000, 1),
            )
        conn.commit()
    finally:
        conn.close()
    return product_id


def test_concurrent_reservation_no_overselling(low_stock_product):
    def reserve(order_id: str) -> httpx.Response:
        return httpx.post(
            f"{CATALOG}/catalog/stock/reserve",
            json={"productId": low_stock_product, "quantity": 1, "orderId": order_id},
            headers=internal_headers("order-service"),
            timeout=10.0,
        )

    with ThreadPoolExecutor(max_workers=5) as pool:
        responses = list(pool.map(reserve, [f"race-{i}" for i in range(5)]))

    statuses = sorted(r.status_code for r in responses)
    assert statuses == [200, 409, 409, 409, 409], [r.text for r in responses]

    r = httpx.get(
        f"{CATALOG}/catalog/products/{low_stock_product}/price",
        headers=internal_headers("order-service"),
    )
    assert r.json()["stock"] == 0


def test_internal_endpoints_reject_missing_caller():
    r = httpx.post(
        f"{CATALOG}/catalog/stock/reserve",
        json={"productId": "P001", "quantity": 1, "orderId": "x"},
    )
    assert r.status_code == 403


def test_internal_endpoints_reject_wrong_caller():
    # cart-service is a valid caller for /price but not for /stock/reserve.
    r = httpx.post(
        f"{CATALOG}/catalog/stock/reserve",
        json={"productId": "P001", "quantity": 1, "orderId": "x"},
        headers=internal_headers("cart-service"),
    )
    assert r.status_code == 403
