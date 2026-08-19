from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    mongo_host: str = "mongodb"
    mongo_port: int = 27017
    mongo_user: str = "root"
    mongo_password: str = "mongopass"
    mongo_db: str = "review_db"

    auth_jwks_url: str = "http://auth-service:8080/auth/.well-known/jwks.json"

    kafka_brokers: str = "kafka:9092"
    kafka_order_events_topic: str = "order-events"
    kafka_order_events_dlq_topic: str = "order-events-dlq"
    consumer_group_id: str = "review-service-group"
    max_processing_attempts: int = 3
    retry_backoff_seconds: float = 1.0

    log_level: str = "info"

    @property
    def mongo_uri(self) -> str:
        return f"mongodb://{self.mongo_user}:{self.mongo_password}@{self.mongo_host}:{self.mongo_port}/?authSource=admin"


settings = Settings()
