"""Structured JSON logging, one object per line, shared by every service.

request_id is read from gestalt_shared.middleware's contextvar at format
time -- not passed by the caller -- so it's automatically "" outside a
request context (asyncio background loops, Kafka consumer threads) and
correct inside one, with no per-call-site plumbing. Callers attach
structured fields via `logger.info(msg, extra={"extra": {...}})`; stdlib
logging merges that dict's keys onto the LogRecord, so `record.extra` ends
up holding exactly the inner dict.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from gestalt_shared.middleware import get_current_request_id


class JsonFormatter(logging.Formatter):
    def __init__(self, service_name: str):
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "service": self.service_name,
            "request_id": get_current_request_id(),
            "message": record.getMessage(),
            "extra": getattr(record, "extra", None) or {},
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(service_name: str, level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(service_name))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
