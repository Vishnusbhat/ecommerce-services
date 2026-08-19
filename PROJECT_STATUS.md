# Gestalt Commerce — Generation Log & Current State

This document is a complete handoff record of what has been built so far, why,
and what's left. It's written to be self-contained — a reader with no prior
context on this conversation should be able to pick up the project from here.

---

## 1. What this project is

**Gestalt Commerce** is a near-production e-commerce microservices system,
built as an infrastructure-focused learning/portfolio project (Kubernetes,
Istio service mesh, GitOps, observability, chaos engineering). The full
system design was pre-specified by the user in a set of markdown docs at
`gestalt-commerce-docs/` (README + `docs/01`–`09` + `services/*.md`), which
this repo's code implements. Business logic is deliberately shallow — the
docs are explicit that the point of the project is the *infrastructure*, not
the app.

The 7 business services and their responsibilities, per the source docs:

| Service | Responsibility | Datastore |
|---|---|---|
| auth-service | Login, JWT issuance (RS256), refresh rotation, revocation | MariaDB + Redis |
| catalog-service | Product catalog, stock, price, row-locked reservation | MariaDB + Redis (cache-aside) |
| cart-service | Ephemeral shopping cart | Redis only |
| order-service | Checkout orchestration (saga), Kafka producer | MariaDB |
| payment-service | Mocked, idempotent payment processing | Redis (idempotency keys only) |
| notification-service | Async order notifications via Kafka | none (pure consumer) |
| review-service | Post-purchase reviews, gated on delivery | MongoDB |

Full architecture, data-ownership rules, the checkout saga sequence diagram,
Kafka event schemas, and the eventual K8s/Istio/observability/chaos/GitOps
design all live in `gestalt-commerce-docs/`. That directory was **not**
modified — it's the spec. Everything under `shared/`, `services/`,
`infra/`, `docker-compose.yml`, `.env.example`, and this file is what has
been generated against that spec.

---

## 2. Scope decisions made at kickoff

Before writing any code, three scope questions were asked and answered:

1. **What's in scope right now?** → *FastAPI services + local docker-compose
   only.* K8s manifests, Istio config, Helm/Argo CD GitOps, Prometheus/Grafana
   dashboards, K6 scripts — all described in the docs — are **explicitly
   deferred**, not built yet.
2. **Build order?** → *Follow the roadmap's incremental order*
   (`docs/09-build-roadmap.md`): Week 1 critical path first (auth, catalog,
   order, payment — the checkout flow, no cart yet), then Week 2 additions
   (cart, notification, review with Kafka).
3. **Repo layout?** → *Single monorepo*, one folder per service under
   `services/`, each an independent FastAPI app with its own
   `requirements.txt`/`Dockerfile`, plus a root `docker-compose.yml` running
   everything together.

Language/framework: **Python 3.12 + FastAPI**, per the auth-service doc's
explicit suggestion ("Python/FastAPI is a reasonable default") and the
roadmap's "use whatever language you're fastest in — this project is about
infra, not app code quality."

---

## 3. Repository layout (as built)

```
ecommerce-services/
├── gestalt-commerce-docs/        # the spec (untouched, pre-existing)
├── shared/
│   ├── requirements.txt          # deps common to every service
│   └── gestalt_shared/           # importable package, copied into every image
│       ├── errors.py             # AppError + standard {error:{code,message,requestId}} envelope
│       ├── middleware.py         # RequestIdMiddleware (x-request-id, Envoy's job when the mesh exists)
│       ├── health.py             # build_health_router() -> /healthz/live, /healthz/ready
│       ├── metrics.py            # Prometheus HTTP metrics middleware + /metrics endpoint
│       ├── security.py           # JWKSClient + RS256 JWT verification (get_current_user dependency)
│       └── internal_auth.py      # shared-token internal-caller auth (mesh AuthorizationPolicy stand-in)
├── services/
│   ├── auth-service/
│   ├── catalog-service/
│   ├── cart-service/
│   ├── order-service/
│   ├── payment-service/
│   ├── notification-service/
│   └── review-service/
│       # each: app/ (FastAPI code), Dockerfile, requirements.txt, tests/ (currently empty)
├── infra/
│   └── mariadb-init/01-init.sql  # per-service DB + credentials bootstrap
├── docker-compose.yml            # data layer + all 7 services
├── .env.example                  # copy to .env before running
├── .gitignore
└── README.md                     # quick-start + known-simplifications summary
```

Every service `Dockerfile` builds from the **repo root** as build context
(not the service directory), so it can `COPY shared/gestalt_shared` and
`COPY services/<name>/app` into the image, with `PYTHONPATH=/app` making
`gestalt_shared` importable alongside the service's own `app` package. This
is why `docker-compose.yml` sets `build: { context: ., dockerfile:
services/<name>/Dockerfile }` for each service rather than `context:
services/<name>`.

---

## 4. Shared package (`gestalt_shared`)

Five small modules, reused identically across all 7 services:

- **`errors.py`** — `AppError(code, message, status_code)` exception, plus
  `install_error_handlers(app)` which registers handlers so *every* error
  response (including validation errors and uncaught exceptions) comes back
  as `{"error": {"code", "message", "requestId"}}`, matching
  `docs/02-api-contracts.md`'s standard envelope exactly.
- **`middleware.py`** — `RequestIdMiddleware`. In the real system Envoy
  auto-injects `x-request-id` at the ingress gateway; since there's no Envoy
  locally, this middleware honors an inbound `x-request-id` if present, else
  mints one, and stamps it on the response.
- **`health.py`** — `build_health_router(ready_check)` → `/healthz/live`
  (always 200) and `/healthz/ready` (calls the service's own readiness
  check, e.g. DB ping), matching the K8s liveness/readiness probe paths
  used throughout `docs/03-kubernetes-deployment.md`.
- **`metrics.py`** — a middleware that records `http_requests_total` and
  `http_request_duration_seconds` (by service/method/path/status) and
  exposes them on `/metrics` in Prometheus text format. Stands in for the
  golden-signal metrics Envoy sidecars give for free in the real system.
- **`security.py`** — `JWKSClient` (fetches + caches auth-service's JWKS by
  `kid`, serves a stale cache if a refresh fails rather than hard-failing —
  mirroring the "auth-service down doesn't invalidate already-issued
  tokens" behavior called out in `services/auth-service.md`) and
  `make_current_user_dependency(jwks_client)`, which builds a FastAPI
  dependency that verifies `Authorization: Bearer <jwt>` and returns
  `TokenClaims(user_id, email, raw)`.
- **`internal_auth.py`** — `make_internal_caller_dependency(shared_token,
  allowed_callers)`. See §6 below — this is the local stand-in for Istio's
  `AuthorizationPolicy` service-identity matrix.

---

## 5. Two deliberate design substitutions (mesh features, done at app level)

These are the two most important things to understand about how this code
diverges from the eventual Istio-mesh design — both are called out inline in
code comments and in the README, but are consolidated here:

### 5.1 JWT validation
**Real design** (`docs/04-istio-service-mesh.md`): Envoy at the ingress
gateway validates the JWT signature once via `RequestAuthentication`,
pulling JWKS from auth-service. Application code never sees an invalid
token; it's a mesh-offload decision.

**Here**: there is no Envoy, so every protected endpoint (cart-service,
order-service, review-service) independently verifies the JWT itself via
`gestalt_shared.security`, hitting auth-service's `GET
/auth/.well-known/jwks.json` (cached). When the Istio phase is eventually
built, this becomes redundant-but-harmless defense-in-depth rather than
something that needs rewriting.

### 5.2 Service-identity authorization
**Real design**: `AuthorizationPolicy` resources restrict e.g.
`payment-service` to accept `POST /payments/charge` only from a caller whose
*cryptographic mTLS identity* is `order-service`'s service account — a
compromised `notification-service` pod, even with a valid cert for itself,
gets a 403.

**Here**: no mesh, no mTLS identity. Instead, `internal_auth.py` implements
a shared-secret scheme: internal callers send `X-Internal-Token: <shared
secret from .env>` plus `X-Internal-Caller: <name>`, and the callee
whitelists caller names per endpoint. E.g. catalog-service's
`/catalog/stock/reserve` only accepts `X-Internal-Caller: order-service`.
This is explicitly **not** a real security boundary (the "identity" is
just a self-reported header) — it exists purely so the *authorization shape*
from the docs' matrix is enforceable and demoable before Istio exists.

Both of these are the first things that should be replaced, not
supplemented, when the Istio/K8s phase of the roadmap begins.

---

## 6. Per-service implementation notes

### auth-service (port 8001, MariaDB `auth_db`, Redis)
- `POST /auth/register`, `POST /auth/login`, `POST /auth/refresh`, `POST
  /auth/logout`, `GET /auth/.well-known/jwks.json`.
- RS256 keypair generated on first boot into a named Docker volume
  (`auth-keys`) so `kid` and the public key stay stable across restarts;
  `kid` is derived deterministically from the public key's SHA-256.
- Password hashing via `bcrypt` directly (not `passlib`, to sidestep a known
  passlib/bcrypt≥4.1 compatibility issue).
- Refresh tokens: the `refresh_tokens.token_id` (a UUID) *is* the token
  value handed to the client — matches the documented schema exactly rather
  than adding a separate hashed-token column.
- Refresh rotation: each `/auth/refresh` call revokes the presented token
  and issues a new access+refresh pair. Redis blacklist check happens
  **before** the DB lookup on every refresh, per the explicit failure-mode
  note in `services/auth-service.md` (a revoked token must never validate
  in the gap between blacklist and DB checks).
- Logout revokes a specific refresh token (by id, passed in the request
  body) and blacklists it in Redis with TTL = remaining time to its natural
  expiry.

### catalog-service (port 8002, MariaDB `catalog_db`, Redis)
- `GET /catalog/products` (paginated, cache-aside), `GET
  /catalog/products/{id}` (cache-aside), `GET
  /catalog/products/{id}/price` (internal-only), `POST
  /catalog/stock/reserve` / `POST /catalog/stock/release` (internal-only,
  `order-service` caller only).
- Reservation uses the exact documented pattern: `SELECT stock FROM
  products WHERE id = ? FOR UPDATE` inside a transaction, application-level
  quantity check, then `UPDATE ... SET stock = stock - ?`, via raw SQL
  through `engine.begin()` (not the ORM session) so the transaction
  boundary is explicit.
- Cache-aside: on any stock-affecting write, the Redis key is **deleted**,
  never updated in place, per `services/catalog-service.md`'s explicit
  guidance to avoid cache/DB drift.
- Seeds 5 demo products (`P001`–`P005`) on first boot if the table is empty,
  so manual testing and a future K6 script have something to browse.

### payment-service (port 8005, Redis only, no relational DB)
- `POST /payments/charge`, `GET /payments/{idempotencyKey}`.
- Implements the documented **safe** idempotency pattern: `SET
  idempotency:{key} "IN_PROGRESS" NX EX <ttl>` claims the key atomically
  *before* any processing starts; a concurrent second caller that loses the
  NX race polls briefly for the first caller's result instead of
  re-processing.
- `FAILURE_RATE`, `LATENCY_MS_MIN`, `LATENCY_MS_MAX` env vars simulate
  synthetic declines/latency, independent of future Istio-level fault
  injection, exactly as specified.
- **`UNSAFE_IDEMPOTENCY_MODE`** (env var, default `false`): when `true`,
  swaps the safe NX-claim for the naive
  GET-then-process-then-SET sequence — i.e. it deliberately reproduces the
  exact double-charge race that `services/payment-service.md` calls "the
  one bug in the whole project worth deliberately reintroducing once" for
  the chaos-engineering exercise in `docs/06`. Built now, to save rework
  later; **off by default**, do not enable outside a deliberate chaos demo.

### order-service (port 8004, MariaDB `orders_db`) — the saga orchestrator
- `POST /orders`, `GET /orders/{id}`, `GET /orders`.
- Accepts an **optional** `items` array in the request body. If omitted
  (matching the K6 script in `docs/08-load-testing.md`, which POSTs an
  empty body), it fetches the caller's cart from cart-service instead
  (`GET /cart`, forwarding the caller's own `Authorization` header — this
  call does *not* use the internal-caller scheme since it's just forwarding
  the end user's own credential). This dual-mode was a deliberate choice so
  the Week-1 critical path (auth+catalog+order+payment) could be built and
  tested *before* cart-service existed.
- Saga: price snapshot from catalog (also validates products exist) →
  persist `PENDING` order + items → publish `order.created` → reserve stock
  per line item (compensating-release any partial reservations on failure)
  → mark `stock_reserved=true` → charge payment-service using the *same*
  idempotency key end-to-end → on success mark `PAID` + publish
  `order.paid`; on any failure (insufficient stock, product not found,
  payment declined, transport error) release all reserved stock, mark
  `FAILED` with a `failure_reason`, publish `order.failed`.
- Idempotency at the API level: `orders.idempotency_key` is `UNIQUE`. A
  fresh request with a seen key returns the existing order's current state
  immediately; a genuine race between two near-simultaneous first-time
  requests is caught by the DB `IntegrityError` on insert, not a
  check-then-insert race.
- **Schema extension beyond the literal docs**: added `orders.stock_reserved`
  (bool) and `orders.delivered` (bool) columns, not present in the docs'
  literal SQL. `stock_reserved` lets the reconciliation job (below) know
  whether a compensating release is owed for a crashed saga; `delivered`
  drives the delivery simulator (below). Both are documented inline as
  intentional extensions.
- **Reconciliation job** (`app/reconciliation.py`): an `asyncio` background
  loop (every `RECONCILIATION_INTERVAL_SECONDS`, default 30s) that
  force-fails any order stuck in `PENDING` longer than
  `PENDING_ORDER_TIMEOUT_SECONDS` (default 60s, matching the alert
  threshold in `docs/05-observability-stack.md`), releasing stock first if
  `stock_reserved` was true. This is the "simple periodic reconciliation
  job" `services/order-service.md` scopes as the right level of complexity
  versus a full transactional outbox.
- **Delivery simulator** (`app/delivery_simulator.py`): a second background
  loop that, `DELIVERY_SIMULATION_DELAY_SECONDS` (default 30s) after an
  order goes `PAID`, publishes a synthetic `order.delivered` event and
  flips `delivered=true`. This wasn't explicitly scoped as order-service's
  job in the original task breakdown but was added because
  `docs/02-api-contracts.md` states `order.delivered` is "simulated via a
  delay/cron for demo purposes" by order-service, and review-service has
  nothing to consume without it.
- Kafka producer uses `acks=all` and calls `flush(5)` synchronously after
  every publish (trades a little latency for delivery certainty in this
  low-throughput demo context, rather than risking an unflushed message on
  container exit).

### cart-service (port 8003, Redis only)
- `GET /cart`, `POST /cart/items`, `DELETE /cart/items/{productId}`, `POST
  /cart/checkout-intent`.
- Data shape exactly as documented: Redis hash `cart:{userId}`, fields
  `productId → quantity`, TTL 24h refreshed on every write.
- `POST /cart/items` calls catalog-service's internal price/stock endpoint
  before accepting the add, rejecting with `INSUFFICIENT_STOCK` (409) if
  the new total would exceed available stock.
- `checkout-intent` was interpreted as a **non-destructive** snapshot read
  (returns current cart contents, doesn't clear them) — deliberately, since
  `services/cart-service.md` scopes cart-service to zero Kafka events
  produced/consumed, so there's no mechanism for the cart to be cleared
  when an order later succeeds or fails. This is called out as a known
  simplification in the README; a production version would need
  order-service to call back into cart-service (or cart-service would need
  to consume `order.created`, which the docs explicitly forbid it from
  doing).

### notification-service (port 8006, no datastore, pure Kafka consumer)
- No business HTTP surface (per the docs) but does run a minimal FastAPI
  app for `/healthz/live`, `/healthz/ready`, `/metrics` (needed for the
  same K8s probe conventions every other service uses) — the actual Kafka
  consumer loop runs in a background thread.
- Consumer group `notification-service-group`, `enable.auto.commit=false`,
  offsets committed only **after** successful processing — the explicit
  point of this service's design per the docs (crash-during-send means
  at-least-once redelivery, never zero-delivery).
- Poison-pill handling: on a processing failure, the consumer `seek()`s
  back to the same message's offset and retries in-process (with a short
  backoff), up to `MAX_PROCESSING_ATTEMPTS` (default 3); after that it
  forwards the raw message to `order-events-dlq` and commits past it, so
  one malformed message can't block the partition forever. **Verified
  live** — see §7.
- Notification "delivery": logs the content, and posts to
  `SLACK_WEBHOOK_URL` if configured (unset by default; logs-only).

### review-service (port 8007, MongoDB `review_db`)
- `GET /reviews/product/{productId}` (public), `POST /reviews` (JWT,
  gated).
- Two collections: `reviews` (the actual review documents) and
  `eligibility` (`_id: "{userId}:{productId}"`, tracks `orderId`,
  `deliveredAt`, `reviewed: bool`).
- Kafka consumer group `review-service-group`, subscribed to `order-events`,
  filters for `order.delivered` only (ignores everything else), upserts one
  eligibility document per `productId` in the event's `productIds[]`. Uses
  the same manual-commit-after-write + bounded-retry-then-DLQ pattern as
  notification-service, for the same reason (`services/review-service.md`
  calls out the same offset-discipline requirement).
- `POST /reviews` checks `eligibility` for `{userId}:{productId}` with
  `reviewed: false`; rejects with `403 NOT_ELIGIBLE` otherwise. On success,
  inserts the review and flips `reviewed: true` (so a second review attempt
  on the same product is also correctly rejected).

---

## 7. Verification performed (all live, against running containers)

Every service was built and smoke-tested individually before moving to the
next, then the whole system was torn down (`docker compose down -v`) and
rebuilt from a clean slate to confirm boot-order/dependency correctness.
Specific things that were actually exercised and confirmed working, not just
written:

- **auth-service**: register → login → refresh (rotation confirmed: reusing
  the old refresh token after rotation correctly returns `401
  INVALID_TOKEN`) → logout → reusing the logged-out refresh token correctly
  fails; duplicate registration correctly returns `409`.
- **catalog-service**: public list/detail reads; internal-only endpoints
  correctly reject calls with no/wrong internal-caller identity (`403`);
  **concurrency test**: set one product's stock to 1, fired 5 concurrent
  reservation requests — exactly one succeeded, the other four got a clean
  `409 INSUFFICIENT_STOCK`, final stock was `0` (no overselling).
- **payment-service**: basic charge + idempotent replay (identical
  `chargedAt` on retry with the same key); **concurrency test**: injected
  500–800ms artificial latency, fired 5 concurrent charges with the same
  new idempotency key — all 5 responses had the exact same `chargedAt`
  timestamp (no double charge).
- **order-service / full saga**: happy path (multi-item order → `PAID`,
  stock correctly decremented, amount correctly summed from catalog
  prices); insufficient-stock path (order for 99,999 units → `FAILED`,
  stock left unchanged, nothing to compensate); idempotent retry (same
  `Idempotency-Key` posted twice → same order id returned both times, stock
  only decremented once); **payment-decline compensation path**: set
  `PAYMENT_FAILURE_RATE=1.0`, placed an order → `FAILED` with reason
  `PAYMENT_DECLINED`, and confirmed reserved stock was released back to its
  original value.
- **Kafka**: consumed `order-events` directly via
  `kafka-console-consumer.sh` and confirmed `order.created` /
  `order.paid` payloads matched the documented schema.
- **cart-service**: add/remove items, stock-limit rejection (409), full
  cart read; confirmed order-service's empty-body `POST /orders` correctly
  sources items from the cart end-to-end (this is the literal flow the K6
  script in `docs/08` will exercise once it's built).
- **notification-service**: confirmed it processes the full backlog of
  historical `order-events` on first startup (consumer group semantics,
  `auto.offset.reset=earliest`); **poison-pill test**: published a raw
  non-JSON message directly to `order-events`, confirmed exactly 3 retry
  attempts (logged), then forwarding to `order-events-dlq`, then
  confirmed the offending message actually landed in the DLQ topic.
- **review-service**: confirmed `POST /reviews` is rejected (`403
  NOT_ELIGIBLE`) before the simulated delivery event fires; waited for
  order-service's delivery simulator to publish `order.delivered`;
  confirmed the review then succeeds, and a second review attempt for the
  same product is correctly rejected (`403`, already reviewed); confirmed
  `GET /reviews/product/{id}` lists it.
- **Full clean-slate integration pass**: `docker compose down -v` (wipes
  all volumes) → `docker compose up -d --build` (all services + data layer)
  → all 7 `/healthz/ready` returned `200` → ran the complete
  register→login→browse→add-to-cart→checkout-intent→checkout→(async)
  notify+review-eligibility→review journey against the fresh stack
  end-to-end successfully.

No automated test suite exists yet — all verification above was done via
`curl`/`docker compose exec` against live containers, not `pytest`. The
`tests/` directory in each service is currently empty (see §9).

---

## 8. Current status against the roadmap

Mapped against `docs/09-build-roadmap.md`'s week-by-week plan:

- **Week 1** (auth, catalog, order, payment; ConfigMap/Secret pattern; no
  mesh): **app-layer done**, ConfigMap/Secret equivalents not yet written
  (still plain `.env` / docker-compose `environment:` blocks).
- **Week 2** (cart, notification, review; MariaDB/Redis/MongoDB/Kafka as
  StatefulSets/Deployments; NetworkPolicy): **app-layer done** (all 7
  services + Kafka working together locally); the StatefulSet/Deployment
  K8s manifests and NetworkPolicy are **not started**.
- **Weeks 3–8** (Istio mesh, observability stack, chaos scenarios, EKS
  migration, load testing + HPA, canary rollout): **not started.** These
  were explicitly deferred at kickoff (see §2).

In short: **the full local application layer (all 7 services) is complete
and verified.** Nothing beyond docker-compose (K8s, Istio, GitOps,
Prometheus/Grafana, K6, Terraform) has been built yet.

---

## 9. Known gaps / explicitly not done

- **No automated tests.** `tests/` directories exist but are empty. All
  verification so far is manual/live against running containers (§7).
- **No K8s manifests** (Deployments, Services, ConfigMaps, Secrets,
  StatefulSets, NetworkPolicy, HPA) — `docs/03-kubernetes-deployment.md` is
  entirely unimplemented.
- **No Istio config** (`PeerAuthentication`, `RequestAuthentication`,
  `AuthorizationPolicy`, `DestinationRule`, fault injection, canary
  `VirtualService` weighting) — `docs/04` unimplemented. The two app-level
  substitutions in §5 exist specifically to make this gap non-blocking for
  now.
- **No observability stack** (Prometheus scraping, Grafana dashboards,
  Kiali, Jaeger tracing) — `docs/05` unimplemented. Every service *exposes*
  `/metrics` already (see §4), but nothing scrapes or visualizes it yet,
  and there's no trace-header propagation between services.
- **No chaos engineering scenarios executed** — `docs/06`'s 5 scenarios are
  unimplemented, though the two building blocks they most depend on
  (payment-service's `FAILURE_RATE`/`LATENCY_MS_*` and
  `UNSAFE_IDEMPOTENCY_MODE`, order-service's reconciliation job) already
  exist in the code, built ahead of need.
- **No GitOps** (Helm charts, Argo CD app-of-apps, Argo Rollouts) —
  `docs/07` unimplemented.
- **No K6 load test script** actually written yet (`docs/08` has the
  reference script; it hasn't been adapted/run against this stack).
- **No Terraform / EKS provisioning** — entirely deferred, per the roadmap
  itself ("Week 6").
- **Cart is never cleared after a successful checkout** — see §6
  (cart-service), a deliberate consequence of the "zero events" scoping in
  the docs, flagged as a real production gap worth revisiting.
- **`docker-compose.yml` maps MariaDB to host port `3307`**, not `3306`
  (a local MariaDB install on the dev machine already owned 3306) — cosmetic,
  but worth knowing if scripting against the host-exposed port directly.

---

## 10. How to pick this back up

```
cd ecommerce-services
cp .env.example .env      # if not already done
docker compose up -d --build
```

All 7 services will be reachable at `localhost:8001`–`8007` (see the table
in §6 or the README for the exact mapping). `docker compose logs -f
<service>` for any service's logs; `docker compose down -v` for a full
clean-slate reset (wipes all data).

The natural next slice, following the roadmap in `docs/09`, is **Week 3**:
standing up Istio locally (or on the eventual K8s target) and replacing the
two app-level substitutions in §5 with the real mesh primitives — but this
file exists so that decision, and everything after it, can be made fresh in
whatever session picks this up next.
