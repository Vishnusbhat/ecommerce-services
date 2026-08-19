import httpx

from conftest import BASE_URLS, internal_headers

CART = BASE_URLS["cart"]


def test_add_and_remove_items(auth_headers):
    r = httpx.post(f"{CART}/cart/items", headers=auth_headers, json={"productId": "P001", "quantity": 2})
    assert r.status_code == 200
    assert {"productId": "P001", "quantity": 2} in r.json()["items"]

    r = httpx.delete(f"{CART}/cart/items/P001", headers=auth_headers)
    assert r.status_code == 204

    r = httpx.get(f"{CART}/cart", headers=auth_headers)
    assert r.json()["items"] == []


def test_add_item_over_stock_limit_returns_409(auth_headers):
    r = httpx.post(
        f"{CART}/cart/items", headers=auth_headers, json={"productId": "P001", "quantity": 999_999}
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "INSUFFICIENT_STOCK"


def test_batch_remove_removes_exactly_specified_ids(auth_headers):
    httpx.post(f"{CART}/cart/items", headers=auth_headers, json={"productId": "P001", "quantity": 1})
    httpx.post(f"{CART}/cart/items", headers=auth_headers, json={"productId": "P002", "quantity": 1})

    # Need the user id for the internal X-User-Id header -- pull it from the
    # cart's owning user via a throwaway order-service-style internal call
    # isn't available here, so decode it isn't needed: cart-service keys
    # purely off the header value, and auth_headers alone doesn't expose the
    # user id. Fetch it from the JWT claims embedded in the access token.
    import base64
    import json as _json

    token = auth_headers["Authorization"].split(" ", 1)[1]
    payload_b64 = token.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)
    user_id = _json.loads(base64.urlsafe_b64decode(payload_b64))["sub"]

    r = httpx.request(
        "DELETE",
        f"{CART}/cart/items:batch",
        json={"productIds": ["P001", "P999-not-in-cart"]},
        headers={**internal_headers("order-service"), "X-User-Id": user_id},
    )
    assert r.status_code == 200
    # P999-not-in-cart was never present -- silently omitted, not an error.
    assert r.json()["removed"] == ["P001"]

    cart = httpx.get(f"{CART}/cart", headers=auth_headers).json()
    assert cart["items"] == [{"productId": "P002", "quantity": 1}]
