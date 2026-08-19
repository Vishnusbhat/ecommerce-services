"""Producer for the `order-events` topic (docs/02-api-contracts.md).

Publishes with acks=all for at-least-once delivery, and flushes synchronously
after each produce -- throughput here is low (one publish per saga step), and
a flushed-before-response guarantee is worth the small added latency versus
risking an unflushed message if the container exits.
"""
from __future__ import annotations

import json
import logging

from confluent_kafka import Producer

from app.config import settings

logger = logging.getLogger("gestalt.order-service.kafka")

_producer = Producer({"bootstrap.servers": settings.kafka_brokers, "acks": "all"})


def _delivery_report(err, msg):
    if err is not None:
        logger.error("kafka_delivery_failed", extra={"error": str(err), "topic": msg.topic()})


def publish_order_event(event_type: str, order_id: str, payload: dict) -> None:
    envelope = {"type": event_type, **payload}
    _producer.produce(
        settings.kafka_order_events_topic,
        key=order_id.encode("utf-8"),
        value=json.dumps(envelope).encode("utf-8"),
        callback=_delivery_report,
    )
    _producer.flush(5)
