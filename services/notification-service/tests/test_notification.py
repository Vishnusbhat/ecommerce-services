"""Poison-pill -> DLQ verification (NEXT_STEP_REQUIREMENTS.md §5.2).

Publishes a raw, deliberately non-JSON message directly to order-events via
`kafka-console-producer.sh` inside the kafka container (no host-side Kafka
client dependency needed -- confluent-kafka has no prebuilt wheel for every
host Python/platform combo, and this project already leans on the console
scripts for exactly this kind of one-off local verification), then confirms
notification-service retried it exactly MAX_PROCESSING_ATTEMPTS times
(parsed from its own structured JSON logs) and that it landed on
order-events-dlq.
"""
from __future__ import annotations

import json
import subprocess
import time
import uuid
from datetime import datetime

from conftest import ROOT

TOPIC = "order-events"
DLQ_TOPIC = "order-events-dlq"
MAX_PROCESSING_ATTEMPTS = 3  # matches notification-service's MAX_PROCESSING_ATTEMPTS default


def _log_ts_to_epoch(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def _parse_json_log_lines(raw: str) -> list[dict]:
    records = []
    for line in raw.splitlines():
        idx = line.find("{")
        if idx == -1:
            continue
        try:
            records.append(json.loads(line[idx:]))
        except json.JSONDecodeError:
            continue
    return records


def _compose(*args: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", *args], cwd=ROOT, check=True, **kwargs)


def test_poison_pill_retries_then_lands_in_dlq():
    marker = f"poison-{uuid.uuid4().hex}"
    start_time = time.time()

    _compose(
        "exec",
        "-T",
        "kafka",
        "/opt/kafka/bin/kafka-console-producer.sh",
        "--bootstrap-server",
        "localhost:9092",
        "--topic",
        TOPIC,
        "--property",
        "parse.key=true",
        "--property",
        "key.separator=:",
        input=f"{marker}:not-valid-json-{marker}\n",
        text=True,
        capture_output=True,
    )

    # Poll for the DLQ-forward log line rather than a fixed sleep -- under a
    # busy shared consumer (e.g. processing backlog from earlier tests in the
    # same suite run), retry timing has enough real-world variance that a
    # single fixed wait was flaky.
    def _fetch_records() -> list[dict]:
        logs = subprocess.run(
            ["docker", "compose", "logs", "--no-color", "--no-log-prefix", "notification-service"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        return [r for r in _parse_json_log_lines(logs) if _log_ts_to_epoch(r["timestamp"]) >= start_time]

    deadline = time.time() + 30
    records: list[dict] = []
    while time.time() < deadline:
        records = _fetch_records()
        if any(r.get("message") == "sending_to_dlq" for r in records):
            break
        time.sleep(2)

    attempts = [r for r in records if r.get("message") == "processing_failed"]
    assert len(attempts) == MAX_PROCESSING_ATTEMPTS, attempts

    dlq_forwards = [r for r in records if r.get("message") == "sending_to_dlq"]
    assert len(dlq_forwards) == 1, dlq_forwards

    # And confirm it actually landed on the DLQ topic, not just logged as sent.
    result = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "kafka",
            "/opt/kafka/bin/kafka-console-consumer.sh",
            "--bootstrap-server",
            "localhost:9092",
            "--topic",
            DLQ_TOPIC,
            "--from-beginning",
            "--timeout-ms",
            "10000",
            "--property",
            "print.key=true",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert marker in result.stdout, f"poison-pill message never appeared on {DLQ_TOPIC}: {result.stdout}"
