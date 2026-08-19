"""Cache-aside for product reads (catalog-service.md).

On any stock-affecting write, the cache key is deleted, never updated in
place -- that avoids the class of bugs where cache and DB drift apart under
concurrent writes.
"""
import json

import redis

from app.config import settings

client = redis.Redis(host=settings.redis_host, port=settings.redis_port, decode_responses=True)


def _product_key(product_id: str) -> str:
    return f"product:{product_id}"


def get_cached_product(product_id: str) -> dict | None:
    raw = client.get(_product_key(product_id))
    return json.loads(raw) if raw else None


def set_cached_product(product_id: str, product: dict) -> None:
    client.set(_product_key(product_id), json.dumps(product), ex=settings.product_cache_ttl_seconds)


def invalidate_product(product_id: str) -> None:
    client.delete(_product_key(product_id))


def redis_is_ready() -> bool:
    try:
        return client.ping()
    except Exception:
        return False
