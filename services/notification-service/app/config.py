from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    kafka_brokers: str = "kafka:9092"
    kafka_order_events_topic: str = "order-events"
    kafka_order_events_dlq_topic: str = "order-events-dlq"
    consumer_group_id: str = "notification-service-group"

    max_processing_attempts: int = 3
    retry_backoff_seconds: float = 1.0

    slack_webhook_url: str = ""

    log_level: str = "info"


settings = Settings()
