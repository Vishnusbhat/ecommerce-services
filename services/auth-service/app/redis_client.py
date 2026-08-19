import redis

from app.config import settings

client = redis.Redis(host=settings.redis_host, port=settings.redis_port, decode_responses=True)


def blacklist_key(token_id: str) -> str:
    return f"blacklist:{token_id}"


def blacklist_add(token_id: str, ttl_seconds: int) -> None:
    if ttl_seconds > 0:
        client.set(blacklist_key(token_id), "1", ex=ttl_seconds)


def is_blacklisted(token_id: str) -> bool:
    return client.exists(blacklist_key(token_id)) == 1


def redis_is_ready() -> bool:
    try:
        return client.ping()
    except Exception:
        return False
