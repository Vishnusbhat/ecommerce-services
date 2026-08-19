"""Shared pytest fixtures/helpers for the integration suite
(NEXT_STEP_REQUIREMENTS.md §5). Tests run against the live docker-compose
stack's exposed host ports -- no mocking, since this project's whole point
is demonstrating real infrastructure behavior.

Root-level on purpose: pytest imports the root conftest.py first (before
any test module, walking the path down to each test file), which puts this
file's directory on sys.path for the rest of the session -- so every
services/*/tests/test_*.py can `from conftest import BASE_URLS,
internal_headers, ...` directly, with no path hacks or shared package.
"""
from __future__ import annotations

import os
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).parent

BASE_URLS = {
    "auth": "http://localhost:8001",
    "catalog": "http://localhost:8002",
    "cart": "http://localhost:8003",
    "order": "http://localhost:8004",
    "payment": "http://localhost:8005",
    "notification": "http://localhost:8006",
    "review": "http://localhost:8007",
}

# Matches .env.example's local-dev defaults.
INTERNAL_SERVICE_TOKEN = os.environ.get("INTERNAL_SERVICE_TOKEN", "dev-internal-token-change-me")

MARIADB_ROOT = dict(host="localhost", port=3307, user="root", password="rootpass")


def internal_headers(caller: str) -> dict:
    return {"X-Internal-Token": INTERNAL_SERVICE_TOKEN, "X-Internal-Caller": caller}


@pytest.fixture(scope="session", autouse=True)
def docker_stack():
    if os.environ.get("SKIP_DOCKER_BUILD"):
        subprocess.run(["docker", "compose", "up", "-d"], check=True, cwd=ROOT)
    else:
        subprocess.run(["docker", "compose", "up", "-d", "--build"], check=True, cwd=ROOT)

    deadline = time.time() + 180
    pending = dict(BASE_URLS)
    while pending and time.time() < deadline:
        for name, url in list(pending.items()):
            try:
                r = httpx.get(f"{url}/healthz/ready", timeout=2.0)
                if r.status_code == 200:
                    del pending[name]
            except httpx.HTTPError:
                pass
        if pending:
            time.sleep(2)

    if pending:
        pytest.exit(f"Services never became ready: {sorted(pending)}")

    yield


@pytest.fixture
def unique_email() -> str:
    return f"test_{uuid.uuid4().hex[:12]}@example.com"


@pytest.fixture
def new_user(unique_email: str) -> dict:
    """Registers + logs in a fresh throwaway user, returns email/tokens."""
    password = "password123"
    r = httpx.post(
        f"{BASE_URLS['auth']}/auth/register", json={"email": unique_email, "password": password}
    )
    assert r.status_code == 201, r.text

    r = httpx.post(
        f"{BASE_URLS['auth']}/auth/login", json={"email": unique_email, "password": password}
    )
    assert r.status_code == 200, r.text
    tokens = r.json()

    return {
        "email": unique_email,
        "password": password,
        "access_token": tokens["accessToken"],
        "refresh_token": tokens["refreshToken"],
    }


@pytest.fixture
def auth_headers(new_user: dict) -> dict:
    return {"Authorization": f"Bearer {new_user['access_token']}"}
