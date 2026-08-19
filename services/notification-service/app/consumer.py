"""Kafka consumer for order-events (notification-service.md).

Offsets are committed manually, after processing -- if the process crashes
mid-notify(), the message is redelivered on restart because the offset was
never committed (at-least-once, never zero-times). A message that fails
processing is retried in-process (via `seek` back to its own offset) up to
`max_processing_attempts` times before being forwarded to
`order-events-dlq` and committed past, so one poison-pill message can't
block the whole partition forever.
"""
from __future__ import annotations

import json
import logging
import threading
import time

from confluent_kafka import Consumer, KafkaError, Producer, TopicPartition
from confluent_kafka.admin import AdminClient

from app.config import settings
from app.notifier import notify

logger = logging.getLogger("gestalt.notification-service.consumer")

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
    event = json.loads(raw)  # malformed JSON -> raises -> poison-pill path
    event_type = event.get("type")
    order_id = event.get("orderId")

    if event_type == "order.created":
        notify(f"Order {order_id} received")
    elif event_type == "order.paid":
        notify(f"Order {order_id} paid — {event.get('amount')}")
    elif event_type == "order.failed":
        notify(f"Order {order_id} failed: {event.get('reason')}")
    else:
        logger.info("ignoring_unhandled_event_type type=%s", event_type)


def _send_to_dlq(producer: Producer, msg, error: str) -> None:
    logger.error(
        "sending_to_dlq topic=%s partition=%s offset=%s error=%s",
        msg.topic(), msg.partition(), msg.offset(), error,
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
                logger.warning("processing_failed attempt=%s msg=%s error=%s", attempts, msg_id, exc)

                if attempts >= settings.max_processing_attempts:
                    _send_to_dlq(producer, msg, str(exc))
                    consumer.commit(msg, asynchronous=False)
                    retry_counts.pop(msg_id, None)
                else:
                    consumer.seek(TopicPartition(msg.topic(), msg.partition(), msg.offset()))
                    time.sleep(settings.retry_backoff_seconds)
    finally:
        consumer.close()
