# Gestalt Commerce — App-Layer Completion Requirements

**Status:** Ready to implement
**Depends on:** `PROJECT_STATUS.md` (current state as of Week 1–2 completion)
**Scope of this doc:** App-layer only. No Kubernetes, Istio, Prometheus, Jaeger, or GitOps work is in scope here — see §7 (Non-Goals) for the explicit boundary. Everything below must be implementable and testable against the existing `docker-compose.yml` stack with no new infrastructure.

This document is written so a reader with no other context can implement it correctly. Where a decision could go two ways, the decision is made here, not left to the implementer.

---

## 0. Priority order (implement in this order)

1. Business metrics (§1)
2. Trace-context (request-id) propagation (§2)
3. Cart clearing after checkout (§3)
4. Structured logging (§4)
5. Automated test suite (§5)

Optional items (§6) may be done in any order after the five above, or skipped.

Reasoning for this order: §1 and §2 are additive (no existing behavior changes, low regression risk) and unblock future observability work cheaply. §3 changes existing behavior in two services and should be done once, correctly, before test coverage is written against it. §5 is placed last on purpose — it should cover the final behavior of §1–§4, not be written against a moving target.

---

## 1. Business metrics

### 1.1 Requirement
Every metric below must be emitted via the existing `prometheus_client` setup in `gestalt_shared/metrics.py` (extend it — do not create a second metrics module) and appear on each service's existing `/metrics` endpoint in Prometheus text format. No new dependency is needed; `prometheus_client`'s `Counter`/`Histogram` types cover everything here.

### 1.2 Exact metrics to add, by service

**order-service:**

| Metric name | Type | Labels | Incremented when |
|---|---|---|---|
| `orders_created_total` | Counter | none | immediately after an order row is persisted as `PENDING` (i.e., every checkout attempt, regardless of eventual outcome) |
| `orders_paid_total` | Counter | none | saga reaches `PAID` |
| `orders_failed_total` | Counter | `reason` (values: `INSUFFICIENT_STOCK`, `PRODUCT_NOT_FOUND`, `PAYMENT_DECLINED`, `TRANSPORT_ERROR`, `RECONCILIATION_TIMEOUT`) | saga reaches `FAILED`, labeled with the same `failure_reason` value already stored on the order row |
| `saga_stock_reservation_failures_total` | Counter | none | specifically the stock-reservation step fails (subset of `orders_failed_total`, but isolated so the two failure classes the docs call out — stock vs payment — are separately graphable, per `docs/05-observability-stack.md`'s explicit requirement) |
| `saga_payment_failures_total` | Counter | none | specifically the payment-charge step fails (the other half of the same split) |
| `order_amount_cents` | Histogram | none | observed with the order's `amount_cents` value, on every `PAID` order — buckets: use `prometheus_client`'s default histogram buckets, no custom bucket list needed for this scale |

**cart-service:**

| Metric name | Type | Labels | Incremented when |
|---|---|---|---|
| `cart_items_added_total` | Counter | none | every successful `POST /cart/items` |
| `cart_abandonment_total` | Counter | none | see §1.3 — this one requires a specific implementation decision, not a simple increment-on-event |

**payment-service:**

| Metric name | Type | Labels | Incremented when |
|---|---|---|---|
| `payment_failures_total` | Counter | `reason` (values: `SYNTHETIC_DECLINE`, `LATENCY_TIMEOUT` if applicable) | a charge attempt returns a non-success result |
| `payment_idempotent_replays_total` | Counter | none | a charge request is served from the idempotency cache rather than freshly processed (this is a genuinely useful metric not explicitly named in the docs, but directly supports the chaos-scenario-5 story — add it) |

**catalog-service:**

| Metric name | Type | Labels | Incremented when |
|---|---|---|---|
| `stock_reservation_conflicts_total` | Counter | none | a reservation request returns `409 INSUFFICIENT_STOCK` — this is your direct evidence metric for the concurrency behavior already manually verified in `PROJECT_STATUS.md` §7 |
| `catalog_cache_hits_total` / `catalog_cache_misses_total` | Counter (two separate counters) | none | on every `GET /catalog/products/{id}` and `GET /catalog/products`, depending on whether the Redis cache-aside lookup hit or missed |

### 1.3 `cart_abandonment_total` — exact definition (do not leave this ambiguous)

"Abandonment" is defined as: **a cart has items, has not been modified (no `POST /cart/items` or `DELETE /cart/items/{id}` call) for 30 minutes, and no `order.created` event referencing that user's cart contents was observed.**

Implementation: cart-service does not currently have a background job. Add one — an `asyncio` loop (same pattern as order-service's `reconciliation.py`) that runs every 5 minutes, scans Redis for `cart:*` keys whose last-write timestamp (store this explicitly as a field in the hash, e.g. `_updated_at`, alongside the product quantity fields — do not rely on Redis `TTL`/`OBJECT IDLETIME` for this since TTL is refreshed by unrelated reads, not just writes) is older than 30 minutes, and increments `cart_abandonment_total` once per such cart, then marks it (e.g., add a `_abandonment_counted: true` field) so it is never double-counted on a later scan of the same still-idle cart.

This is a deliberate, fully-specified design so two different implementers would build the identical metric — do not substitute a simpler heuristic (e.g., "cart TTL expired") without updating this doc first, since that would silently redefine what the metric means.

---

## 2. Trace-context (request-id) propagation

### 2.1 Current state
`gestalt_shared/middleware.py`'s `RequestIdMiddleware` already honors an inbound `x-request-id` header or mints one, and stamps it on the *response*. It does **not** currently forward that id on any *outbound* call a service makes to another service.

### 2.2 Requirement
Every outbound HTTP call made by one service to another (catalog→none, cart→catalog, order→catalog, order→payment, order→cart) must include the current request's `x-request-id` value as a header on the outbound call.

### 2.3 Exact implementation
Add a helper to `gestalt_shared/middleware.py`:

```python
import contextvars

_request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")

def get_current_request_id() -> str:
    return _request_id_ctx.get()
```

`RequestIdMiddleware` must set `_request_id_ctx` at the start of request handling (before calling `call_next`), not just stamp the response header. Every service's outbound HTTP client call (wherever `httpx` or equivalent is used to call another service) must read `get_current_request_id()` and set it as the `x-request-id` header on the outbound request. This must be true for both the internal-caller-authenticated calls (catalog reservation, payment charge) and the user-credential-forwarding call (order→cart).

Do **not** implement this as a decorator or wrapper around every individual `httpx` call site — instead, add a single shared `httpx.Client`/`AsyncClient` factory function in `gestalt_shared` (e.g. `make_internal_http_client()`) that installs an `httpx` event hook or transport that injects the header automatically, and require every service to construct its outbound client through that factory. This guarantees no call site can forget the header, which a per-call-site approach cannot guarantee.

### 2.4 Acceptance test
A single request through the full checkout flow (order-service receiving one inbound `x-request-id`) must produce **the same `x-request-id` value** in the logs (see §4) of order-service, catalog-service (both reserve and release, if a release occurs), and payment-service, for that one checkout call. This is directly testable without Jaeger — grep the four services' logs for one id and confirm all four appear.

---

## 3. Cart clearing after checkout

### 3.1 Requirement
When an order reaches a terminal state (`PAID` or `FAILED`), the items that were checked out must be removed from the user's cart. This applies regardless of whether the order was created from an explicit `items` array or from the cart (see `PROJECT_STATUS.md` §6, order-service's dual-mode behavior) — in both cases, whatever items ended up on the order must be cleared from the cart if they are still present there.

### 3.2 Exact contract

Add a new internal-only endpoint to cart-service:

```
DELETE /cart/items:batch
Header: X-Internal-Token, X-Internal-Caller: order-service
Body: { "productIds": ["P001", "P002"] }
Response: 200 { "removed": ["P001", "P002"] }
```

This removes exactly the listed `productIds` from the caller's cart (specified via a required `X-User-Id` header on this internal call, since there is no end-user JWT on an internal-caller-authenticated request) — not the whole cart, and not by TTL. If the cart no longer contains a given product id (e.g., it was already removed by the user before checkout completed), that id is simply omitted from the `removed` array in the response — this is not an error condition.

### 3.3 Call site and timing
order-service calls this endpoint **after** the saga reaches its terminal state (`PAID` or `FAILED`), not before, and does so **regardless of which branch the saga took** — including the `FAILED` branch. This is a deliberate decision worth stating explicitly: even a failed order (e.g., payment declined) should clear the attempted items from the cart, on the reasoning that the user has already seen a definitive outcome for that checkout attempt and a stale cart re-showing items they just failed to buy is worse UX than an empty cart. If a future session disagrees with this call, it must update this doc, not silently diverge from it.

This call is fire-and-forget from the saga's perspective — a failure to clear the cart must **not** fail or roll back an otherwise-successful order. Wrap it in a try/except that logs a warning (see §4) and does nothing else on failure.

### 3.4 What this replaces
This closes the exact gap flagged in `PROJECT_STATUS.md` §9 ("Cart is never cleared after a successful checkout") — including the failure case, which the original flag didn't even consider. Update `PROJECT_STATUS.md` §9 to remove this bullet once implemented, and update `services/cart-service.md`'s "Events" section is unaffected (this is a direct API call, not a Kafka event, so the docs' "cart-service produces/consumes zero events" constraint is respected).

---

## 4. Structured logging

### 4.1 Requirement
Replace whatever ad hoc logging currently exists with structured JSON logs, one JSON object per line, on every log statement in every service.

### 4.2 Exact schema

```json
{
  "timestamp": "2026-08-19T10:15:30.123Z",
  "level": "INFO",
  "service": "order-service",
  "request_id": "a1b2c3d4",
  "message": "saga completed",
  "extra": { "order_id": "...", "status": "PAID" }
}
```

`request_id` must be populated from `get_current_request_id()` (§2.3) on every log line emitted during request handling, and must be an empty string (not `null`, not omitted) on log lines emitted outside a request context (e.g., the reconciliation job's background loop, the Kafka consumer loops) — those get their own correlation instead: reconciliation-job log lines use `"extra": {"job": "reconciliation", "order_id": "..."}`; Kafka consumer log lines use `"extra": {"kafka_offset": ..., "topic": ..., "partition": ...}`.

### 4.3 Implementation
Add a `gestalt_shared/logging.py` module exporting a `configure_logging(service_name: str)` function using Python's standard `logging` module with a custom `logging.Formatter` subclass that emits the schema above (do not add a third-party logging library dependency — the stdlib is sufficient here). Every service's app startup must call `configure_logging("order-service")` (or its own name) before any other code runs, and every subsequent `logger.info(...)`/`logger.warning(...)`/`logger.error(...)` call anywhere in that service automatically gets the schema applied.

### 4.4 Minimum log points (per service, non-exhaustive floor, add more where useful)
- Every request's start and completion, with status code and duration — this can live in the existing metrics middleware or a sibling logging middleware, implementer's choice, but must exist
- Every state transition in order-service's saga (`PENDING`→`PAID`, `PENDING`→`FAILED`, with reason)
- Every compensating action (stock release) — log the order id and the reason it was triggered
- Every idempotency-cache hit in payment-service (i.e., every `payment_idempotent_replays_total` increment from §1.2 should have a matching log line)
- Every DLQ forward in notification-service (already exists per `PROJECT_STATUS.md` §7 — just needs reformatting into the schema, not new logic)
- Every reconciliation-job force-fail

---

## 5. Automated test suite

### 5.1 Requirement
Port the manual verification scenarios from `PROJECT_STATUS.md` §7 into `pytest`, one test file per service under each service's existing (currently empty) `tests/` directory. Tests run against the docker-compose stack (integration-style, not mocked) — this project's whole point is demonstrating real infrastructure behavior, so mocking out the database/Redis/Kafka would defeat the purpose.

### 5.2 Required test cases (minimum floor — this list is exhaustive for "required," additional tests are welcome but these must all exist)

**`services/auth-service/tests/test_auth.py`:**
- Register → login → refresh succeeds, and reusing the old (pre-rotation) refresh token after a successful refresh returns `401 INVALID_TOKEN`
- Logout, then reusing the logged-out refresh token returns `401`
- Duplicate registration (same email) returns `409`

**`services/catalog-service/tests/test_catalog.py`:**
- Concurrency test: set a product's stock to 1, fire 5 concurrent `POST /catalog/stock/reserve` calls for quantity 1 each (use `asyncio.gather` or `httpx.AsyncClient` with concurrent tasks — not sequential calls), assert exactly one `200` and four `409 INSUFFICIENT_STOCK`, assert final stock is `0`
- Internal-only endpoints reject calls with missing or wrong `X-Internal-Caller` with `403`

**`services/payment-service/tests/test_payment.py`:**
- Idempotent replay: two charge calls with the same idempotency key return identical `chargedAt`
- Concurrency test: 5 concurrent charge calls with the same new idempotency key, assert all 5 responses have identical `chargedAt` (this must pass with `UNSAFE_IDEMPOTENCY_MODE=false`, the default — do **not** write this test in a way that would also pass under unsafe mode; assert the response bodies are byte-identical, not just "no error," since the naive unsafe implementation could still coincidentally avoid an exception while still double-charging)
- A separate test explicitly run with `UNSAFE_IDEMPOTENCY_MODE=true` (either a fixture toggling the env var and restarting the test container, or a documented manual/CI-separate step if that's impractical in this suite) that asserts the double-charge race **is** reproducible — this is a regression test in the opposite direction, proving the unsafe mode is genuinely unsafe and therefore that the safe mode's protection is meaningful, not vacuous

**`services/order-service/tests/test_order_saga.py`:**
- Happy path: multi-item order reaches `PAID`, stock correctly decremented, `amount_cents` correctly summed
- Insufficient-stock path: order for a quantity exceeding stock reaches `FAILED`, stock unchanged
- Idempotent retry: same `Idempotency-Key` posted twice returns the same order id both times, stock decremented only once
- Payment-decline compensation: with `PAYMENT_FAILURE_RATE=1.0`, an order reaches `FAILED` with `reason: PAYMENT_DECLINED`, and previously-reserved stock is confirmed released back to its original value
- New (not in the original manual verification, required here): after either a `PAID` or `FAILED` terminal state, confirm the cart-clearing call from §3 actually removed the checked-out items from cart-service's state (query cart-service directly in the test, don't just assert order-service logged an attempt)

**`services/cart-service/tests/test_cart.py`:**
- Add/remove items, stock-limit rejection (`409`)
- New: the batch-delete endpoint from §3.2 removes exactly the specified product ids and correctly no-ops (not errors) on an already-absent id

**`services/notification-service/tests/test_notification.py`:**
- Poison-pill test: publish a malformed message directly to `order-events`, assert exactly `MAX_PROCESSING_ATTEMPTS` retry attempts occur, then assert the message lands in `order-events-dlq`

**`services/review-service/tests/test_review.py`:**
- `POST /reviews` rejected `403 NOT_ELIGIBLE` before a delivery event; succeeds after; a second attempt for the same product is rejected `403` as already-reviewed

### 5.3 Test infra requirement
Add a `docker-compose.test.yml` override (or a documented `pytest` fixture using `docker compose up -d --build` as a session-scoped fixture with teardown) so the full suite is runnable with a single command from repo root. Document the exact command in the top-level `README.md`.

---

## 6. Optional (implement only if time remains, in any order)

- Refresh-token garbage collection: a periodic job in auth-service deleting `refresh_tokens` rows past their `expires_at`. Not required — an unbounded but slow-growing table is acceptable for a demo project — but a clean addition if there's spare time.
- `POST /admin/reset` on order-service or a small standalone script that truncates all service databases and re-seeds catalog-service's demo products, for fast repeatable resets before a live interview demo, as an alternative to `docker compose down -v`.
- Pagination/sorting hardening on `GET /orders` (currently, per `PROJECT_STATUS.md`, unspecified beyond the basic list).

These are explicitly **not required** for this doc to be considered complete. Do not spend time here before §1–§5 are done.

---

## 7. Non-Goals (explicit boundary — do not do any of this in this pass)

- No Kubernetes manifests of any kind
- No Istio/service-mesh config of any kind
- No Prometheus, Grafana, Kiali, or Jaeger deployment — metrics and request-id propagation must exist and be *correct*, but nothing needs to consume them yet
- No Terraform / EKS work
- No GitOps/Argo CD/Helm
- No K6 script (the reference script in `docs/08` remains reference-only until Week 7 per the roadmap)

If implementing §1–§5 surfaces a strong reason to start on any of the above early, stop and update this doc (or write a new one) rather than silently expanding scope mid-implementation.

---

## 8. Definition of done

This requirements doc is satisfied when all of the following are true simultaneously, verified against a fresh `docker compose down -v && docker compose up -d --build`:

- [ ] Every metric in §1.2 is visible on its service's `/metrics` endpoint, with at least one non-zero observation achievable by exercising the corresponding flow manually
- [ ] `cart_abandonment_total` increments correctly per the §1.3 definition, verified by waiting out (or artificially shortening, via an env-var-configurable interval, for test purposes) the 30-minute window once
- [ ] A single checkout's `x-request-id` is confirmed identical across order/catalog/payment service logs for that one request (§2.4)
- [ ] A successful checkout empties the checked-out items from the cart; a failed checkout also empties the attempted items from the cart (§3)
- [ ] Every service emits the JSON log schema from §4.2 on startup and during request handling
- [ ] The full test suite from §5.2 passes via a single documented command
- [ ] `PROJECT_STATUS.md` is updated to reflect all of the above, including removing the now-closed items from its §9 gap list
