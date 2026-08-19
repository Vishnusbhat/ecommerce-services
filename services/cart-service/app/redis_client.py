import redis

from app.config import settings

client = redis.Redis(host=settings.redis_host, port=settings.redis_port, decode_responses=True)


def cart_key(user_id: str) -> str:
    return f"cart:{user_id}"


def redis_is_ready() -> bool:
    try:
        return client.ping()
    except Exception:
        return False
