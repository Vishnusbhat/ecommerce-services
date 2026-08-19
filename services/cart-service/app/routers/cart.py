from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.catalog_client import get_price_and_stock
from app.config import settings
from app.redis_client import cart_key
from app.redis_client import client as redis_client
from app.schemas import AddItemRequest, CartItemOut, CartOut
from app.security import current_user_dependency
from gestalt_shared.errors import AppError
from gestalt_shared.security import TokenClaims

router = APIRouter(prefix="/cart", tags=["cart"])


def _read_cart(user_id: str) -> CartOut:
    raw = redis_client.hgetall(cart_key(user_id))
    items = [CartItemOut(productId=pid, quantity=int(qty)) for pid, qty in raw.items()]
    return CartOut(items=items)


@router.get("", response_model=CartOut)
def get_cart(user: TokenClaims = Depends(current_user_dependency)):
    return _read_cart(user.user_id)


@router.post("/items", response_model=CartOut)
def add_item(body: AddItemRequest, user: TokenClaims = Depends(current_user_dependency)):
    # Validate against catalog-service so the cart never advertises a price
    # or stock level that's no longer true (cart-service.md).
    product = get_price_and_stock(body.productId)

    key = cart_key(user.user_id)
    current_qty = int(redis_client.hget(key, body.productId) or 0)
    new_qty = current_qty + body.quantity
    if new_qty > product["stock"]:
        raise AppError("INSUFFICIENT_STOCK", "Requested quantity exceeds available stock", 409)

    redis_client.hset(key, body.productId, new_qty)
    redis_client.expire(key, settings.cart_ttl_seconds)
    return _read_cart(user.user_id)


@router.delete("/items/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_item(product_id: str, user: TokenClaims = Depends(current_user_dependency)):
    key = cart_key(user.user_id)
    redis_client.hdel(key, product_id)
    if redis_client.hlen(key) > 0:
        redis_client.expire(key, settings.cart_ttl_seconds)
    return None


@router.post("/checkout-intent", response_model=CartOut)
def checkout_intent(user: TokenClaims = Depends(current_user_dependency)):
    # "Freeze" here means a stable read of current contents for the client to
    # confirm before calling POST /orders -- it does not clear the cart.
    # cart-service.md scopes this service to zero events produced/consumed,
    # so clearing-on-order-created is intentionally not wired up here; the
    # cart's 24h TTL is what bounds staleness instead.
    cart = _read_cart(user.user_id)
    if not cart.items:
        raise AppError("EMPTY_CART", "Cart is empty, nothing to check out", 400)
    return cart
