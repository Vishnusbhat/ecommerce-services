import redis

from app.config import settings

client = redis.Redis(host=settings.redis_host, port=settings.redis_port, decode_responses=True)


def redis_is_ready() -> bool:
    try:
        return client.ping()
    except Exception:
        return False
