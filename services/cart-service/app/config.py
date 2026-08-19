from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    redis_host: str = "redis-cart"
    redis_port: int = 6379
    cart_ttl_seconds: int = 86_400

    auth_jwks_url: str = "http://auth-service:8080/auth/.well-known/jwks.json"
    catalog_service_url: str = "http://catalog-service:8080"
    internal_service_token: str = "dev-internal-token-change-me"
    http_timeout_seconds: float = 3.0

    log_level: str = "info"


settings = Settings()
