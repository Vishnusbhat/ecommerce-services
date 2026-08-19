from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    mariadb_host: str = "mariadb"
    mariadb_port: int = 3306
    mariadb_db: str = "orders_db"
    mariadb_user: str = "order_svc"
    mariadb_password: str = "order_pass"

    auth_jwks_url: str = "http://auth-service:8080/auth/.well-known/jwks.json"
    catalog_service_url: str = "http://catalog-service:8080"
    payment_service_url: str = "http://payment-service:8080"
    cart_service_url: str = "http://cart-service:8080"

    internal_service_token: str = "dev-internal-token-change-me"
    http_timeout_seconds: float = 3.0

    kafka_brokers: str = "kafka:9092"
    kafka_order_events_topic: str = "order-events"

    # docs/05-observability-stack.md: "Order saga stuck: orders in PENDING
    # state > 60s" is the alert threshold this reconciliation job enforces.
    pending_order_timeout_seconds: int = 60
    reconciliation_interval_seconds: int = 30

    # docs/services/order-service.md: order.delivered is "simulated via a
    # delay/cron for demo purposes" -- there's no real shipping pipeline here.
    delivery_simulation_delay_seconds: int = 30
    delivery_check_interval_seconds: int = 10

    log_level: str = "info"

    @property
    def database_url(self) -> str:
        return (
            f"mysql+pymysql://{self.mariadb_user}:{self.mariadb_password}"
            f"@{self.mariadb_host}:{self.mariadb_port}/{self.mariadb_db}"
        )


settings = Settings()
