from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    redis_host: str = "redis-cart"
    redis_port: int = 6379
    cart_ttl_seconds: int = 86_400

    # cart_abandonment_total definition (NEXT_STEP_REQUIREMENTS.md §1.3):
    # a cart with items, unmodified for this long, counts as abandoned.
    # Both env-overridable so the 30-minute default doesn't have to be
    # waited out for real to verify the metric.
    cart_abandonment_threshold_seconds: int = 1800
    cart_abandonment_scan_interval_seconds: int = 300

    auth_jwks_url: str = "http://auth-service:8080/auth/.well-known/jwks.json"
    catalog_service_url: str = "http://catalog-service:8080"
    internal_service_token: str = "dev-internal-token-change-me"
    http_timeout_seconds: float = 3.0

    log_level: str = "info"


settings = Settings()
