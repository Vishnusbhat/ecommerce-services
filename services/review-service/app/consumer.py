"""Kafka consumer for order-events, filtering for order.delivered
(review-service.md). Populates the eligibility collection that
POST /reviews checks -- this is the enforcement point for "only verified
purchasers can review." Same manual-commit-after-write and bounded-retry
-then-DLQ discipline as notification-service, and for the same reason: an
eligibility write failing after the message is consumed but before the
commit must not silently drop the event.
"""
from __future__ import annotations

import json
import logging
import threading
import time

from confluent_kafka import Consumer, KafkaError, Producer, TopicPartition
from confluent_kafka.admin import AdminClient

from app.config import settings
from app.db import eligibility_collection

logger = logging.getLogger("gestalt.review-service.consumer")

_ready = threading.Event()


def kafka_is_ready() -> bool:
    try:
        admin = AdminClient({"bootstrap.servers": settings.kafka_brokers})
        admin.list_topics(timeout=2.0)
        return True
    except Exception:
        return False


def consumer_is_ready() -> bool:
    return _ready.is_set()


def handle_event(raw: bytes) -> None:
    event = json.loads(raw)
    if event.get("type") != "order.delivered":
        return

    user_id = event["userId"]
    order_id = event["orderId"]
    delivered_at = event.get("deliveredAt")

    for product_id in event.get("productIds", []):
        eligibility_collection.update_one(
            {"_id": f"{user_id}:{product_id}"},
            {
                "$set": {"orderId": order_id, "deliveredAt": delivered_at},
                "$setOnInsert": {"reviewed": False},
            },
            upsert=True,
        )


def _send_to_dlq(producer: Producer, msg, error: str) -> None:
    logger.error(
        "sending_to_dlq",
        extra={
            "extra": {
                "job": "kafka-consumer",
                "topic": msg.topic(),
                "partition": msg.partition(),
                "kafka_offset": msg.offset(),
                "error": error,
            }
        },
    )
    producer.produce(
        settings.kafka_order_events_dlq_topic,
        key=msg.key(),
        value=msg.value(),
        headers={"x-dlq-error": error.encode("utf-8")[:1000], "x-original-topic": msg.topic().encode()},
    )
    producer.flush(5)


def run_consumer(stop_event: threading.Event) -> None:
    consumer = Consumer(
        {
            "bootstrap.servers": settings.kafka_brokers,
            "group.id": settings.consumer_group_id,
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
        }
    )
    producer = Producer({"bootstrap.servers": settings.kafka_brokers})
    consumer.subscribe([settings.kafka_order_events_topic])

    retry_counts: dict[tuple[str, int, int], int] = {}

    logger.info("consumer_started group=%s topic=%s", settings.consumer_group_id, settings.kafka_order_events_topic)
    _ready.set()

    try:
        while not stop_event.is_set():
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() != KafkaError._PARTITION_EOF:
                    logger.error("consumer_error: %s", msg.error())
                continue

            msg_id = (msg.topic(), msg.partition(), msg.offset())
            try:
                handle_event(msg.value())
                consumer.commit(msg, asynchronous=False)
                retry_counts.pop(msg_id, None)
            except Exception as exc:
                attempts = retry_counts.get(msg_id, 0) + 1
                retry_counts[msg_id] = attempts
                logger.warning(
                    "processing_failed",
                    extra={
                        "extra": {
                            "job": "kafka-consumer",
                            "topic": msg.topic(),
                            "partition": msg.partition(),
                            "kafka_offset": msg.offset(),
                            "attempt": attempts,
                            "error": str(exc),
                        }
                    },
                )

                if attempts >= settings.max_processing_attempts:
                    _send_to_dlq(producer, msg, str(exc))
                    consumer.commit(msg, asynchronous=False)
                    retry_counts.pop(msg_id, None)
                else:
                    consumer.seek(TopicPartition(msg.topic(), msg.partition(), msg.offset()))
                    time.sleep(settings.retry_backoff_seconds)
    finally:
        consumer.close()
