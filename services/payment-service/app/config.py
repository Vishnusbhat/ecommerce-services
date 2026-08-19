from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    redis_host: str = "redis-payment"
    redis_port: int = 6379
    idempotency_ttl_seconds: int = 86400

    # Chaos-testing knobs, independent of Istio-level fault injection
    # (docs/02-api-contracts.md).
    failure_rate: float = 0.0
    latency_ms_min: int = 0
    latency_ms_max: int = 0

    # When true, skips the SET-NX claim-before-processing step and does a
    # naive GET-then-process-then-SET instead -- reproduces the exact
    # double-charge race payment-service.md calls out as "the one bug in the
    # whole project worth deliberately reintroducing once." Off by default.
    unsafe_idempotency_mode: bool = False

    internal_service_token: str = "dev-internal-token-change-me"

    log_level: str = "info"


settings = Settings()
