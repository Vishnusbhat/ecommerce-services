import time

import redis

from app.config import settings

client = redis.Redis(host=settings.redis_host, port=settings.redis_port, decode_responses=True)

# Metadata fields stored inside the same cart:{userId} hash as product
# quantities (NEXT_STEP_REQUIREMENTS.md §1.3) -- callers building the
# items list must filter these out.
UPDATED_AT_FIELD = "_updated_at"
ABANDONMENT_COUNTED_FIELD = "_abandonment_counted"
META_FIELDS = {UPDATED_AT_FIELD, ABANDONMENT_COUNTED_FIELD}


def cart_key(user_id: str) -> str:
    return f"cart:{user_id}"


def touch_cart(key: str) -> None:
    """Call on every cart write (add/remove/batch-remove). Refreshes the
    last-write timestamp the abandonment job keys off, and un-marks a cart
    that activity has just proven isn't abandoned after all."""
    client.hset(key, UPDATED_AT_FIELD, str(time.time()))
    client.hdel(key, ABANDONMENT_COUNTED_FIELD)
    client.expire(key, settings.cart_ttl_seconds)


def redis_is_ready() -> bool:
    try:
        return client.ping()
    except Exception:
        return False
