"""Outbound HTTP client factory that auto-propagates x-request-id.

In the real system, trace headers are forwarded by app code on every
outbound call so Envoy's per-hop spans stitch into one trace
(docs/05-observability-stack.md's explicit warning: miss this in one
service and the trace has a gap at that hop). This factory is the single
place that guarantees it -- every service constructs its outbound client
through here instead of calling httpx.get/post ad hoc, so no call site can
forget the header.

Deliberately does *not* set auth headers (X-Internal-Token, Authorization,
etc.) -- it's used both for internal-caller-authenticated calls and for
forwarding a user's own JWT, so callers still set their own auth per call.
"""
from __future__ import annotations

import httpx

from gestalt_shared.middleware import get_current_request_id


def _inject_request_id(request: httpx.Request) -> None:
    request_id = get_current_request_id()
    if request_id:
        request.headers["x-request-id"] = request_id


def make_internal_http_client(timeout: float) -> httpx.Client:
    return httpx.Client(timeout=timeout, event_hooks={"request": [_inject_request_id]})
