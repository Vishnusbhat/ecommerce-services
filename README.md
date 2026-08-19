# Gestalt Commerce — services

FastAPI implementation of the 7 microservices specified in
`gestalt-commerce-docs/`, wired together with docker-compose for local
development. This is the app layer only (roadmap Weeks 1-2) — Kubernetes,
Istio, GitOps, and observability manifests from the docs are a later phase.

## Layout

```
shared/gestalt_shared/   # cross-service: JWT verify, error envelope, health/metrics
                          # middleware, internal-caller auth (see below)
services/<name>/
  app/                    # FastAPI app
  Dockerfile              # builds from repo root context (needs shared/)
  requirements.txt        # service-specific deps (shared/requirements.txt has the rest)
infra/
  mariadb-init/           # per-service DB + user bootstrap SQL
docker-compose.yml
.env.example
```

## Running it

```
cp .env.example .env
docker compose up -d --build
```

All 7 services expose `/healthz/live`, `/healthz/ready`, and `/metrics`
(Prometheus format) — matching the K8s probe/ServiceMonitor conventions in
`docs/03-kubernetes-deployment.md` and `docs/05-observability-stack.md`,
even though nothing scrapes them yet in this phase.

| Service | Port | Datastore |
|---|---|---|
| auth-service | 8001 | MariaDB (`auth_db`) + Redis |
| catalog-service | 8002 | MariaDB (`catalog_db`) + Redis |
| cart-service | 8003 | Redis only |
| order-service | 8004 | MariaDB (`orders_db`) |
| payment-service | 8005 | Redis only |
| notification-service | 8006 | none (Kafka consumer) |
| review-service | 8007 | MongoDB |

MariaDB: `localhost:3307` (3306 is often already taken by a local install).
MongoDB: `localhost:27017`. Kafka: `localhost:29092` (host listener).

## Known simplifications vs. the docs (until the Istio/K8s phase)

- **JWT validation**: the docs have Envoy validate JWTs once at the ingress
  gateway (`docs/04-istio-service-mesh.md`). There's no Envoy here, so each
  protected service verifies independently via `auth-service`'s JWKS
  endpoint (`shared/gestalt_shared/security.py`). Becomes redundant-but-safe
  defense in depth once the mesh exists, not a rewrite.
- **Service-identity authorization**: the mesh's `AuthorizationPolicy`
  matrix (e.g. "only order-service may call payment-service") is
  approximated with a shared internal bearer token + a declared caller name
  header (`shared/gestalt_shared/internal_auth.py`). Not a real substitute
  for mTLS identity — just enough to make the authorization *shape*
  demoable and testable now.
- **Retries/timeouts/circuit-breaking**: owned by Istio `VirtualService`/
  `DestinationRule` in the real design; not implemented at the app level
  here (order-service's calls to catalog/payment use a flat timeout and do
  not themselves retry).
- **order.delivered**: genuinely simulated (per the docs) — order-service
  flips a PAID order to delivered after `DELIVERY_SIMULATION_DELAY_SECONDS`
  (default 30s) and publishes the event, no real fulfillment pipeline.
- **payment-service.md's deliberate bug**: set `UNSAFE_IDEMPOTENCY_MODE=true`
  on payment-service to swap the safe `SET NX` claim for the naive
  GET-then-process-then-SET race, for the chaos-engineering exercise in
  `docs/06-resilience-and-chaos-engineering.md`. Off by default.

## Verified so far

Register/login/refresh/logout, JWKS; product listing + cache-aside;
concurrent stock reservation under a `SELECT ... FOR UPDATE` race (no
overselling); payment idempotency under concurrent retries (no double
charge); the full checkout saga incl. compensating stock release on both
insufficient-stock and payment-decline paths; cart add/remove/checkout-intent
and order-service sourcing items from the cart; Kafka fan-out to
notification-service (with poison-pill → DLQ handling) and review-service
(purchase-eligibility gating on the simulated `order.delivered` event); cart
clearing after checkout on both `PAID` and `FAILED` outcomes (including the
reconciliation job's force-fail path); business metrics; request-id
propagation across every inter-service call; structured JSON logging.

## App-layer completion pass (`NEXT_STEP_REQUIREMENTS.md`)

Everything below closes the gaps this project's `PROJECT_STATUS.md` §9
originally flagged, still entirely at the app layer — no Kubernetes, Istio,
Prometheus/Grafana, or GitOps work is part of this pass (see that doc's §7
for the explicit boundary).

**Business metrics** — each service's existing `/metrics` endpoint now also
exposes: `orders_created_total`, `orders_paid_total`,
`orders_failed_total{reason}` (closed label set:
`INSUFFICIENT_STOCK`/`PRODUCT_NOT_FOUND`/`PAYMENT_DECLINED`/
`TRANSPORT_ERROR`/`RECONCILIATION_TIMEOUT` — also what
`orders.failure_reason` itself now stores, so the two can't drift),
`saga_stock_reservation_failures_total`, `saga_payment_failures_total`,
`order_amount_cents` (order-service); `cart_items_added_total`,
`cart_abandonment_total` (cart-service, via a new background scan job --
see `CART_ABANDONMENT_THRESHOLD_SECONDS`/`..._SCAN_INTERVAL_SECONDS` in
`.env.example`); `payment_failures_total{reason}`,
`payment_idempotent_replays_total` (payment-service);
`stock_reservation_conflicts_total`, `catalog_cache_hits_total` /
`catalog_cache_misses_total` (catalog-service). Defined per-service using
`prometheus_client` directly (same pattern as the original
`PAYMENT_FAILURES_TOTAL`), not centralized in `gestalt_shared` — that
module is imported by all 7 services, so business counters belong with the
service that owns them.

**Request-id propagation** — `gestalt_shared/http_client.py`'s
`make_internal_http_client()` factory auto-attaches the current request's
`x-request-id` to every outbound call (order→catalog, order→payment,
order→cart, cart→catalog); no call site can forget it. Backed by a
`contextvars.ContextVar` in `gestalt_shared/middleware.py`. One nuance worth
knowing if you touch middleware ordering: Starlette's `BaseHTTPMiddleware`
runs each layer's downstream call in a new `anyio` task, which only copies
context state already set *before* that call — so `RequestIdMiddleware`
must be the **outermost** middleware (registered last) or the id never
becomes visible to anything wrapping it. See the comment in any service's
`app/main.py` for the concrete failure mode this fixes.

**Cart clearing** — `DELETE /cart/items:batch` (internal-caller
-authenticated, `X-User-Id` header) removes exactly the checked-out product
ids from a user's cart. order-service calls it after every saga terminal
state — `PAID` or `FAILED`, including reservation failures, payment
declines, and the reconciliation job's force-fails — fire-and-forget, never
blocking or failing the order response.

**Structured logging** — every service emits one JSON object per log line
(`gestalt_shared/logging.py`): `{timestamp, level, service, request_id,
message, extra}`. `request_id` is read from the same contextvar as above,
so it's automatically `""` for background jobs (reconciliation, delivery
simulation, cart abandonment) and Kafka consumer threads, and correctly
populated during request handling, with no per-call-site plumbing.

## Automated tests

```
cp .env.example .env   # if not already done
./scripts/run_tests.sh
```

Integration-style against the live docker-compose stack (rebuilt fresh by
default) — no mocking, since the whole point of this project is
demonstrating real infrastructure behavior. Set `SKIP_DOCKER_BUILD=1` to
skip the rebuild step and run against an already-up stack for faster local
iteration. Covers: auth token rotation/revocation, catalog's concurrent
-reservation race (no overselling), payment idempotency under concurrent
retries, the full order saga (happy path, insufficient stock, idempotent
retry, payment-decline compensation, cart-clearing on both `PAID` and
`FAILED`), cart add/remove/batch-delete, notification-service's poison-pill
→ DLQ path, and review-service's delivery-gated eligibility.

`services/payment-service/tests/test_payment_unsafe_mode.py` is
deliberately **not** part of that run — it restarts payment-service with
`UNSAFE_IDEMPOTENCY_MODE=true`, which would corrupt every other test
assuming the safe default. Run it separately:

```
./scripts/test_unsafe_idempotency.sh
```

This proves the double-charge race the safe mode prevents is real and
reproducible (asserting more than one distinct `chargedAt` for the same
idempotency key under concurrent load) — the reason the main suite's
no-double-charge assertion is meaningful protection, not a vacuous one.
Restores `.env` and restarts payment-service to safe defaults on exit
either way.
