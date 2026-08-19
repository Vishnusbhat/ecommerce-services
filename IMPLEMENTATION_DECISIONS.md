# Implementation Decisions

Every non-obvious choice made while building this codebase, in one place:
what the alternatives were, why the current implementation won, and what
was given up to get it. Organized by concern, not by service — most
decisions here are cross-cutting (the same pattern gets reused across all
7 services) with per-service specifics called out where they diverge.

Format per entry: **Decision** (what was built) → **Alternatives**
(what else was on the table) → **Why this one** → **Tradeoff** (what you
pay for it, and when it'd bite).

---

## Part 1 — Foundational choices

### 1.1 Language & framework: Python + FastAPI
**Alternatives:** Node/Express or NestJS, Go (net/http or Gin), Java/Spring
Boot.

**Why FastAPI:** the source spec (`gestalt-commerce-docs/services/auth-service.md`)
explicitly suggested it ("a reasonable default given your existing FastAPI
experience"), and the roadmap doc is explicit that business logic is
deliberately shallow — the project's point is the infrastructure, not
language choice. FastAPI additionally gives dependency injection
(`Depends()`) for free, which is exactly the shape needed for the two
recurring auth patterns (JWT dependency, internal-caller dependency)
without writing a framework of our own.

**Tradeoff:** every service pays Python's per-request overhead and the GIL;
none of that matters at this project's traffic scale, but it's the reason
sync SQLAlchemy (§1.3) was an acceptable choice instead of forcing
everything async.

### 1.2 Monorepo, one folder per service
**Alternatives:** a separate git repo per service (mirroring the eventual
GitOps app-of-apps structure more literally); a single flat app with
internal module boundaries instead of separate deployables.

**Why monorepo:** decided at kickoff with the user — local iteration speed
matters more right now than repo-per-service ceremony, and a flat
single-app design would defeat the actual point of the project (7
independently deployable services demonstrating real service boundaries).

**Tradeoff:** every service's `Dockerfile` has to build from the **repo
root** (not its own directory) so it can `COPY shared/gestalt_shared`
alongside its own `app/` — see §1.5. That's a small, permanent piece of
build-context awkwardness in exchange for not needing a published shared
package or git submodules.

### 1.3 Sync SQLAlchemy + sync FastAPI route handlers, not async
**Alternatives:** async SQLAlchemy (`asyncpg`/`aiomysql` drivers) with
`async def` routes throughout; a mixed approach (async routes, sync DB
calls via `run_in_threadpool`).

**Why sync:** transaction boundaries needed to be *explicit and impossible
to get subtly wrong* — most visibly catalog-service's `SELECT ... FOR
UPDATE` reservation query, built directly on `engine.begin()` rather than
the ORM session. Sync SQLAlchemy's transaction semantics are simpler to
reason about correctly under this project's actual concurrency need (a
handful of overlapping requests, not thousands), and FastAPI runs sync
`def` handlers in a threadpool automatically — so there's no real
throughput cliff at this scale, just simpler code.

**Tradeoff:** this genuinely would not scale to high concurrency (threadpool
size becomes the ceiling, not the event loop) — a real production service
under load would want async DB access. Explicitly not a concern here; the
concurrency demonstrations (catalog race, payment race) use 5–8 concurrent
requests, not 5,000.

### 1.4 docker-compose first, Kubernetes/Istio deferred
**Alternatives:** build directly against Kubernetes (kind/minikube) from
day one, skipping compose.

**Why compose first:** decided at kickoff — the roadmap's own week-by-week
plan builds services locally before introducing the mesh (`docs/09`,
Week 1–2 vs Week 3+). Compose gives a tighter edit/rebuild/test loop than
`kubectl apply` + image push during active development, and every
mesh-shaped decision this project makes (JWT-at-edge, service-identity
authorization) has a clean, explicitly-documented substitute in the
meantime — see `AUTHZ_BASELINE.md`.

**Tradeoff:** two substitutions currently stand in for real mesh behavior
(JWT re-verified per-service instead of once at the edge; a shared-secret
header instead of mTLS identity) and **must** be swapped out, not just
supplemented, once Istio arrives — `AUTHZ_BASELINE.md` exists specifically
so that swap can be verified rather than assumed correct.

### 1.5 Shared code as a copied-in package, not a published dependency
**Decision:** `shared/gestalt_shared/` is a plain Python package, `COPY`'d
into every service's Docker image (build context = repo root) and put on
`PYTHONPATH`, rather than `pip install`'d from anywhere.

**Alternatives:** publish it as an installable package (private PyPI index,
or `pip install -e` against a path) and have each service declare it as a
real dependency; use a git submodule.

**Why copy-in:** zero extra infrastructure (no package index to stand up
or fake), and the build-context trick (`Dockerfile` at
`services/<name>/Dockerfile`, but `context: .` in `docker-compose.yml`) is
a two-line cost per service.

**Tradeoff:** no version pinning between a service and the shared code it
runs against — every service always gets *whatever's on disk* at build
time. Fine for a monorepo where everything ships together; would need
revisiting if services were ever deployed independently on different
release cadences.

---

## Part 2 — Cross-cutting infrastructure (`gestalt_shared/`)

### 2.1 Standard error envelope
**Decision:** every error, on every service, is
`{"error": {"code", "message", "requestId"}}`, enforced centrally via
`gestalt_shared/errors.py`'s `AppError` + FastAPI exception handlers
(including a catch-all for unhandled exceptions, so nothing ever leaks a
raw traceback to a client).

**Alternatives:** per-service ad hoc error shapes (FastAPI's default
`{"detail": "..."}`); RFC 7807 Problem Details.

**Why this shape:** it's what `gestalt-commerce-docs/docs/02-api-contracts.md`
specifies verbatim — not a free choice, a spec requirement, chosen there so
Envoy/Grafana error-rate dashboards and client handling stay consistent
across all 7 services once the mesh exists.

**Tradeoff:** none really — it's strictly more informative than FastAPI's
default, and centralizing it means no service can accidentally drift from
the contract.

### 2.2 JWT verification: per-service JWKS client, not a shared trust boundary
**Decision:** every protected endpoint verifies the caller's JWT itself,
via `gestalt_shared/security.py`'s `JWKSClient` (fetches + caches
auth-service's public key by `kid`, serves a stale cache if a refresh
fails rather than hard-failing).

**Alternatives:** trust an upstream-injected header (`X-User-Id`) with no
per-service verification, since "the edge already checked it" — this is
literally what the real Istio design does (`RequestAuthentication` at the
ingress gateway, app code "never sees an invalid token").

**Why verify per-service now:** there is no ingress gateway locally. Either
every service verifies independently, or there's no verification at all
until Istio exists — the latter would mean months of the project running
with effectively no auth enforcement between services and the outside
world, which defeats the purpose of a security-focused project doc.

**Tradeoff:** N services now each pay a JWKS-verification cost per request
that the real design intends to pay exactly once, at the edge. This is
explicitly not meant to be optimized — once Istio's `RequestAuthentication`
exists, this becomes redundant-but-harmless defense in depth, not a
performance problem worth avoiding today. The stale-cache-on-fetch-failure
behavior was deliberately copied from the real design's stated property
("killing auth-service doesn't invalidate already-issued tokens") so the
behavior, not just the mechanism, matches what the spec describes.

### 2.3 Internal-caller auth: shared secret + declared header, not mTLS
**Decision:** `gestalt_shared/internal_auth.py`'s
`make_internal_caller_dependency(shared_token, allowed_callers)` — every
internal call carries a static `X-Internal-Token` (one value, shared across
all 7 services) plus a self-declared `X-Internal-Caller` name, checked
against a per-endpoint allow-list.

**Alternatives:** no auth at all on internal endpoints (rely on Docker
network isolation only); per-service-pair secrets instead of one global
secret; hand-rolled mutual-TLS between services (generate and manage certs
manually, without Istio's automation).

**Why the shared-token scheme:** it's the cheapest thing that makes the
*authorization shape* — "only order-service may call
`POST /payments/charge`" — actually enforceable and testable today, which
is the entire point (see `AUTHZ_BASELINE.md` §2 for the full matrix this
produces, deliberately shaped to mirror `docs/04`'s `AuthorizationPolicy`
table row-for-row). Hand-rolled mTLS was considered and rejected as
wasted effort: it would be thrown away wholesale once Istio automates the
exact same thing properly, for no learning benefit in between.

**Tradeoff — the important one:** this is **not** a real security boundary.
Anyone who knows the one shared secret can claim to be any service by
setting `X-Internal-Caller` to whatever they like — there's no
cryptographic binding between the claimed identity and the actual caller,
unlike Istio's mTLS-derived principals. `AUTHZ_BASELINE.md` §3 states this
explicitly so it's never mistaken for equivalent protection once the real
`AuthorizationPolicy` resources exist.

### 2.4 Request-id propagation: a shared HTTP client factory, not per-call-site headers
**Decision:** `gestalt_shared/http_client.py`'s `make_internal_http_client()`
returns an `httpx.Client` with a `request` event hook that reads a
`contextvars.ContextVar` (set by `RequestIdMiddleware`) and injects
`x-request-id` automatically; every outbound call goes through a client
built this way.

**Alternatives:** thread the request id through every function call
manually and set it as a header at each `httpx.get/post` call site; a
decorator wrapping call sites.

**Why the factory:** a per-call-site convention is exactly the kind of
thing that's silently forgotten on the *next* new call site someone adds —
requiring the factory instead makes forgetting structurally impossible
rather than relying on discipline.

**Tradeoff / real bug this surfaced:** Starlette's `BaseHTTPMiddleware`
runs each middleware's downstream call (`call_next`) in a **new `anyio`
task**, which only copies `contextvars` state that was already set
*before* that call — mutations made *inside* the downstream task never
propagate back out. Concretely: `RequestIdMiddleware` has to be the
**outermost** middleware (registered *last*, after `setup_metrics()`) or
the id it sets never becomes visible to anything wrapping it, including
the metrics/logging middleware that needs to log it. This was caught live
(the id showed up as `""` in logs despite being sent in the request) and
fixed by reordering middleware registration in all 7 `main.py` files — the
comment explaining why is left in place at every call site precisely so a
future edit doesn't silently reintroduce it. This is the single most
subtle bug in the whole codebase and the best illustration of why "it
looked reasonable and worked in the simple case" isn't the same as
correct — it needed reading Starlette's actual source to understand.

### 2.5 Structured logging: stdlib `logging` + a custom `Formatter`, not a third-party library
**Decision:** `gestalt_shared/logging.py`'s `configure_logging()` installs a
`JsonFormatter` on the root logger; `request_id` is read from the same
contextvar as §2.4 at *format time*, not passed by the caller.

**Alternatives:** `structlog`, `python-json-logger`, or a hand-rolled
per-call `logger.info(json.dumps(...))` convention.

**Why stdlib:** the JSON schema needed (`timestamp/level/service/request_id/
message/extra`) is small and fixed — a whole library's configuration
surface buys nothing here that a ~40-line `Formatter` subclass doesn't
already give. Reading `request_id` from the contextvar at format time
(rather than requiring every `logger.info()` call to pass it) is what
makes background jobs and Kafka consumer threads correctly show `""`
with zero special-casing: `asyncio.create_task`-spawned loops copy the
context at creation (permanently `""`, since they're never inside a
request), and plain `threading.Thread` consumers don't inherit contextvars
across the thread boundary at all — both fall out of Python's actual
semantics, not code written to detect "am I in a background job."

**Tradeoff:** the `extra={"extra": {...}}` calling convention (an outer key
literally named `extra`, required by how stdlib `logging.Logger.info(...,
extra=...)` merges kwargs onto the `LogRecord`) reads a little oddly the
first time; it's the price of not adding a dependency for something this
small.

### 2.6 Business metrics: colocated per-service, not centralized in `gestalt_shared`
**Decision:** every business counter (`orders_paid_total`,
`cart_abandonment_total`, etc.) is declared with plain
`prometheus_client.Counter`/`Histogram` directly in the owning service's
own code — following the precedent payment-service already set with
`PAYMENT_FAILURES_TOTAL` — not added to the shared `metrics.py` module.

**Alternatives:** define every business metric in `gestalt_shared/metrics.py`
since that's where the generic HTTP metrics middleware already lives.

**Why colocated:** `gestalt_shared` is imported by all 7 services sharing
one process-global Prometheus registry model per service; if
`orders_paid_total` were declared in the shared module, it would register
(frozen at zero) on cart-service's, catalog-service's, etc. `/metrics`
too — actively misleading, since those services have nothing to do with
orders. Ownership of a metric should match ownership of the thing it
measures.

**Tradeoff:** no single file lists every metric across the system — you
have to know which service owns which counter (documented in
`NEXT_STEP_REQUIREMENTS.md` §1 and `API_REFERENCE.md` §14 instead).

### 2.7 `orders.failure_reason`: a closed 5-value label set, not free text
**Decision:** every raw failure string coming back from `clients.py`
(`"insufficient_stock"`, `f"transport_error:{exc}"`,
`f"upstream_error_{code}"`, etc.) gets normalized through
`order-service/app/metrics.py`'s `normalize_failure_reason()` into exactly
one of `INSUFFICIENT_STOCK` / `PRODUCT_NOT_FOUND` / `PAYMENT_DECLINED` /
`TRANSPORT_ERROR` / `RECONCILIATION_TIMEOUT` — and that normalized value is
what actually gets **stored** in `orders.failure_reason`, not just used for
the metric label.

**Alternatives:** store the raw diagnostic string in the DB (more specific,
e.g. the exact exception text) and only normalize at the point of
incrementing the Prometheus counter.

**Why normalize the stored value too:** Prometheus labels with unbounded
cardinality (a raw exception message differs every time) are a real
operational anti-pattern — each distinct value creates a new time series.
More importantly, `NEXT_STEP_REQUIREMENTS.md` §1.2 explicitly required the
label to equal what's stored on the order row, so the two can never
silently drift apart into "the DB says one thing, the dashboard says
another."

**Tradeoff:** `POST /orders`'s `failureReason` field is less
diagnostically specific than the raw error would have been (e.g. a
connection-refused and a 502 both collapse into `TRANSPORT_ERROR`). The
detailed reason is still available in the structured logs (§2.5) at the
moment of failure — the API response optimizes for "what bucket does this
belong to," not "what exactly went wrong."

---

## Part 3 — Service-specific decisions

### 3.1 auth-service: RSA keypair generated on first boot, persisted to a volume
**Alternatives:** bake a fixed dev keypair into the image (same key every
build); generate fresh on every container start (no persistence).

**Why generate-once-and-persist:** a fixed baked-in key would be a real
secret checked into version control (bad practice even for a demo);
regenerating on every restart would invalidate every previously-issued
token and rotate `kid` constantly, breaking the "auth-service can go down
without invalidating already-issued tokens" property the spec calls out
(`services/auth-service.md`). Generating once into the `auth-keys` named
Docker volume gets both: no secret in the repo, and stability across
restarts.

**Tradeoff:** `docker compose down -v` (which this project's own docs tell
you to run for a clean slate) wipes the volume and rotates the key —
acceptable for local dev, would need a real KMS/secrets-manager story for
production, which the source docs already flag as a deliberate future
step (`docs/03-kubernetes-deployment.md`'s note about AWS Secrets Manager).

### 3.2 auth-service: refresh token *is* the DB primary key (a UUID), not a separately hashed secret
**Alternatives:** generate a high-entropy opaque token, store only its hash
in the DB (so a DB leak doesn't directly hand out valid refresh tokens),
look up by hash on refresh.

**Why the simpler version:** it matches the documented schema
(`services/auth-service.md`'s `refresh_tokens` table) exactly, and a UUID4
has 122 bits of entropy — reasonable for this project's threat model.

**Tradeoff:** a database compromise directly exposes usable refresh tokens
(no hash-and-compare step protecting them), unlike a production auth
system's best practice. Explicitly a demo-scope simplification, not
recommended verbatim for production use.

### 3.3 auth-service: password hashing via `bcrypt` directly, not `passlib`
**Alternatives:** `passlib[bcrypt]`, the more common high-level choice.

**Why direct `bcrypt`:** `passlib` 1.7.4 has a known compatibility issue
with `bcrypt>=4.1` (it probes `bcrypt.__about__`, which newer `bcrypt`
versions removed) — calling `bcrypt.hashpw`/`checkpw` directly sidesteps a
real, currently-unresolved upstream bug rather than pinning to an old
`bcrypt` to work around it.

**Tradeoff:** loses `passlib`'s multi-algorithm abstraction (easy migration
to argon2 later, automatic rehash-on-verify for old hash formats) — not
needed here since there's exactly one hash format in play.

### 3.4 catalog-service: raw SQL transaction (`engine.begin()`), not the ORM session, for stock reservation
**Alternatives:** do the same `SELECT ... FOR UPDATE` / `UPDATE` sequence
through the SQLAlchemy ORM session (`db.execute(select(...).with_for_update())`).

**Why raw `engine.begin()`:** this is the one query in the whole codebase
where the exact transaction boundary is the entire point — it's the direct
concrete answer to the overselling race condition the source spec calls
out explicitly. Writing it as raw SQL inside an explicit `with
engine.begin() as conn:` block makes the boundary visually unambiguous
(everything between `begin()` and the block exit is one transaction) in a
way that's easy to accidentally blur with an ORM session that might have
its own autoflush/autocommit behavior layered on top.

**Tradeoff:** this one function doesn't participate in the request-scoped
ORM session every other catalog-service endpoint uses — a deliberate,
narrow exception, not a pattern to spread elsewhere.

### 3.5 catalog-service cache-aside: delete-on-write, never update-in-place
**Alternatives:** update the Redis cache entry directly when stock changes
(`stock -= quantity` against the cached value too), avoiding a cache miss
on the next read.

**Why delete, not update:** explicitly the guidance in
`services/catalog-service.md` — updating the cache in place opens a real
race window where a concurrent write's cache update and a concurrent read's
cache-fill can interleave and leave stale data behind. A guaranteed cache
miss forces a fresh DB read, which is always correct.

**Tradeoff:** every stock-affecting write costs the next reader one extra
DB round-trip (cache miss) instead of a cache hit — a small, deliberate
performance-for-correctness trade the source spec calls out by name as
worth making.

### 3.6 cart-service: metadata fields embedded in the same Redis hash as cart contents
**Decision:** `_updated_at` and `_abandonment_counted` live as extra fields
inside the same `cart:{userId}` hash as the actual `productId → quantity`
pairs, filtered out by convention (`META_FIELDS`) everywhere the cart is
read.

**Alternatives:** a separate `cart-meta:{userId}` key; a Redis sorted set
keyed by last-write timestamp across all carts (would make the abandonment
scan O(log n) instead of a full `SCAN`).

**Why embedded:** one Redis round-trip reads both the cart contents and its
metadata together, and `touch_cart()` can update both fields plus refresh
the TTL in one place. This was also the fastest path to satisfying the
requirement that cart writes (not TTL/idle-time) drive the abandonment
timer — see §3.7.

**Tradeoff:** every cart-reading code path has to remember to filter
`META_FIELDS` out (`_read_cart()` in `routers/cart.py` does this once,
centrally — but it's an easy thing for a new endpoint to forget). A sorted
set would scale better for very large numbers of carts (the abandonment
scan is a full `SCAN` over every `cart:*` key today); not a real concern
at this project's scale.

### 3.7 Cart abandonment: explicit `_updated_at` field, not Redis TTL/`OBJECT IDLETIME`
**Alternatives:** treat a cart nearing TTL expiry as "abandoned"; use
Redis's built-in `OBJECT IDLETIME` (time since last *any* access, read or
write).

**Why an explicit write-timestamp field:** both alternatives conflate
*reads* with *activity*. `GET /cart` (a read) would reset `OBJECT
IDLETIME` even though the user didn't actually do anything — silently
redefining "abandoned" to mean something looser than intended, and
producing an undercount. `NEXT_STEP_REQUIREMENTS.md` §1.3 specified this
exact distinction and explicitly forbade the TTL shortcut for this reason.

**Tradeoff:** requires every write path (`add_item`, `remove_item`,
`batch_remove_items`) to remember to call `touch_cart()` — one more thing a
new write endpoint has to get right, versus TTL-based staleness which
would be automatic but wrong.

### 3.8 order-service: dual-mode checkout (explicit `items` or cart-sourced)
**Alternatives:** always require explicit `items` in the request body
(simpler order-service, but doesn't match the K6 reference script's
empty-body `POST /orders`); always source from the cart (can't test
order-service before cart-service exists).

**Why dual-mode:** cart-service was built *after* order-service in the
build sequence (Week 1 vs Week 2 of the roadmap) — dual-mode is what let
the Week 1 critical path (auth → catalog → order → payment) be built and
fully verified standalone before cart-service existed, while still ending
up matching the documented cart-sourced flow once it did.

**Tradeoff:** two code paths through the same handler instead of one —
a small amount of permanent branching complexity in exchange for not
having to build cart-service before order-service could be tested at all.

### 3.9 order-service: orchestration saga (a synchronous handler), not choreography
**Decision:** `POST /orders` is one long-lived request that calls
catalog-service then payment-service in sequence and returns the final
terminal state — not a purely event-driven flow where each step reacts to
the previous one's Kafka event with no central coordinator.

**Alternatives:** choreography — order-service publishes `order.created`,
a (hypothetical) stock-reservation consumer reacts and publishes its own
result, a payment consumer reacts to that, etc.

**Why orchestration:** the source spec is explicit about this choice
(`docs/01-architecture-overview.md`) — the flow is short (two dependencies)
and the client needs a synchronous, immediately-visible answer to "did my
checkout work," not a webhook later. Choreography is the better fit for a
longer chain with independently-scaling steps (e.g. a real multi-day
shipping pipeline), which is exactly why `order.delivered` *is* choreographed
(order-service publishes it, review-service reacts asynchronously) while
checkout itself is not.

**Tradeoff:** order-service is a hard dependency on the synchronous
checkout path — if catalog-service or payment-service is down, checkout
fails immediately rather than degrading to "accepted, processing." That's
the intended behavior here (a synchronous answer was the whole point), but
it's worth being explicit that this is a tradeoff, not a free win.

### 3.10 order-service: reconciliation job (periodic sweep), not a transactional outbox
**Alternatives:** a full transactional outbox pattern (write the "reserve
stock" intent and the DB commit in the same transaction, with a separate
relay process guaranteeing exactly-once publication); a saga-state-machine
framework.

**Why a periodic sweep:** `services/order-service.md` explicitly scopes
this as "the right level of complexity" for this project versus a full
outbox — a background loop that force-fails any order stuck `PENDING`
past a timeout (releasing stock if it was reserved, tracked via the
`stock_reserved` column) covers the actual failure mode (process crash
mid-saga) without building general-purpose exactly-once delivery
infrastructure.

**Tradeoff:** a genuinely crashed saga stays in an inconsistent-looking
`PENDING` state for up to `PENDING_ORDER_TIMEOUT_SECONDS` (60s) before
being cleaned up — an outbox would resolve it as part of the crash
recovery itself, faster and with stronger guarantees. Acceptable given
this is a demo failure mode, not a production SLA.

### 3.11 order-service: `stock_reserved` and `delivered` columns beyond the documented schema
**Decision:** `orders` gained two boolean columns not present in
`services/order-service.md`'s literal SQL.

**Why:** `stock_reserved` is what makes the reconciliation job (§3.10)
*safe* — without it, a stuck `PENDING` order gives no way to know whether
a compensating `/stock/release` call is actually owed, and calling it
unconditionally would incorrectly over-credit stock for orders that never
reserved anything. `delivered` is what prevents the delivery simulator
(§3.12) from republishing `order.delivered` on every pass once a `PAID`
order crosses the delay threshold.

**Tradeoff:** the running schema now diverges slightly from the docs'
literal SQL — flagged explicitly, both here and inline in
`order-service/app/models.py`, so it reads as a deliberate, documented
extension rather than a silent drift from spec.

### 3.12 order-service: delivery simulation as a second background loop, not a one-off script/cron
**Alternatives:** an external cron job hitting an admin endpoint; a
one-shot script run manually per order.

**Why an in-process `asyncio` loop:** `docs/02-api-contracts.md` states
`order.delivered` is "simulated via a delay/cron for demo purposes" by
order-service itself — an in-process loop (same shape as the
reconciliation job) needs no extra infrastructure and keeps the "who
publishes this event" ownership exactly where the spec puts it, with a
configurable delay (`DELIVERY_SIMULATION_DELAY_SECONDS`) so it can be sped
up for testing without code changes.

**Tradeoff:** ties `order.delivered`'s timing to order-service's own
uptime/scan interval rather than a precise, externally-schedulable time —
fine for a simulation, wouldn't be how a real fulfillment integration
would work.

### 3.13 payment-service: atomic `SET NX` claim before processing, not check-then-act
**Alternatives:** `GET` the idempotency key, and if absent, process and
`SET` the result (check-then-act) — this is what `UNSAFE_IDEMPOTENCY_MODE`
deliberately implements, as a controlled demonstration of the bug.

**Why atomic claim:** this is the actual point of the service, per
`services/payment-service.md` — the failure class it demonstrates
(two-phase writes across non-transactional systems: Redis and the "charge"
side effect) is only safe if the *claim* on the idempotency key happens
before processing starts, atomically, so two concurrent retries can't both
pass a `GET`-based check and both charge.

**Tradeoff:** a request that loses the `SET NX` race has to poll
(`_wait_for_result`, bounded retries with backoff) for the winner's result
rather than getting an immediate answer — a small latency cost for callers
unlucky enough to race, in exchange for the correctness guarantee.

### 3.14 payment-service: `UNSAFE_IDEMPOTENCY_MODE` built in as a toggle, not a separate branch
**Alternatives:** keep the unsafe implementation only in git history (a
past commit) or a separate git branch, to be checked out manually when
demonstrating the chaos-engineering scenario later.

**Why a runtime toggle:** `services/payment-service.md` calls the
double-charge bug "the one bug in the whole project worth deliberately
reintroducing once" for a future chaos-engineering exercise — building the
toggle now means that exercise needs zero extra implementation work later,
just flipping an env var. It also makes the safe mode's protection
*provable*: `test_payment_unsafe_mode.py` demonstrates the race genuinely
reproduces under the unsafe path, which is what makes the safe-mode test's
pass meaningful rather than vacuous (see §4.3).

**Tradeoff:** production code now contains a deliberately-unsafe code path,
gated only by a boolean env var defaulting to `false`. This is a real
risk if that default were ever flipped in a real deployment by mistake —
mitigated by keeping it loudly documented (module docstring, `.env.example`
comment, README) and never wired into any default-on config.

### 3.15 notification-service / review-service: bounded in-process retry (`seek`-based) before DLQ, not immediate DLQ or infinite retry
**Alternatives:** forward to the DLQ on the very first processing failure
(no retry); retry forever in place (never give up, blocking the partition
indefinitely on a truly poisonous message).

**Why bounded retry then DLQ:** a single transient failure (e.g. a
momentary Slack webhook timeout) shouldn't immediately exile a perfectly
good message to the DLQ — but a message that's *genuinely* malformed
(unparseable JSON) will fail identically forever if retried without limit,
permanently blocking every other message behind it on that partition. Using
`consumer.seek()` back to the failed message's own offset (rather than
just letting `poll()` naturally advance) is what makes this a real
in-process retry of *that specific message*, not just "move on and let a
future restart redeliver it" — that distinction matters because Kafka
consumers otherwise only get redelivery semantics on restart/rebalance,
not mid-session.

**Tradeoff:** `MAX_PROCESSING_ATTEMPTS` (default 3) is a judgment call, not
a derived number — too low risks DLQ'ing something that would have
succeeded on a 4th try; too high delays every message behind a poison
message in the same partition for longer. 3 attempts with a 1s backoff was
chosen as "enough to ride out a genuinely transient blip, not so many that
a real poison message blocks the partition for long."

### 3.16 review-service: MongoDB, not MariaDB
**Decision:** reviews and the purchase-eligibility collection live in
MongoDB, the only service in the system that doesn't use MariaDB or Redis
as its primary store.

**Why:** explicitly reasoned through in `services/review-service.md` — a
review document has genuinely variable structure (optional fields like
photo attachments or a future "verified purchase" badge, no schema
migration needed to add them) and, critically, **no cross-document
transactional need** the way order-service's saga requires relational
guarantees. Defaulting to "MariaDB everywhere because that's the rest of
the stack" would have been the worse design choice for this specific data
shape — the spec calls this out directly as a reasoning exercise, not just
a technology-diversity checkbox.

**Tradeoff:** a second datastore technology to run and operate
(`mongodb` container, `pymongo` dependency, different query idioms) for
one service's data — justified here specifically because the project's
purpose includes demonstrating a document-model use case is a genuine fit,
not an accident of familiarity.

---

## Part 4 — Testing strategy

### 4.1 Integration tests against the live stack, no mocking
**Alternatives:** unit tests with mocked DB/Redis/Kafka/Mongo clients
(faster, no Docker dependency, standard "unit test" shape).

**Why integration-only:** `NEXT_STEP_REQUIREMENTS.md` §5.1 states this
directly — this project's entire point is demonstrating real
infrastructure behavior (a real row lock actually preventing overselling,
a real Redis `NX` claim actually preventing a double charge). A mocked
`SELECT ... FOR UPDATE` would test that the *code called the mock
correctly*, not that the concurrency guarantee actually holds — which is
precisely the thing worth proving.

**Tradeoff:** the suite is slow (~90–120s for 18 tests, plus a separate
~25s for the unsafe-mode script) and requires Docker running — normal unit
tests would run in milliseconds with no external dependencies. Accepted
because speed isn't the goal here; correctness-under-real-conditions is.

### 4.2 A single root `conftest.py`, not per-service test infrastructure
**Alternatives:** a shared test-support package installed into each
service's test environment; duplicated fixtures/helpers copy-pasted into
each service's `tests/` directory.

**Why root-level:** pytest imports the root `conftest.py` first, before any
test module (walking the directory tree down to each test file), which
puts its directory on `sys.path` for the whole session — every
`services/*/tests/test_*.py` can `from conftest import BASE_URLS,
internal_headers, ...` directly with zero path configuration. This is
standard, well-established pytest behavior for monorepos, verified working
by actually running the suite rather than assumed.

**Tradeoff:** relies on pytest always being invoked from the repo root
(documented in every script) — running a single test file directly from
inside a service's directory without the right working directory would
break the import. `scripts/run_tests.sh` and `scripts/test_unsafe_idempotency.sh`
both `cd` to repo root explicitly for exactly this reason.

### 4.3 `test_payment_unsafe_mode.py` isolated into its own script, not part of the main suite
**Alternatives:** run it in the same `pytest` invocation as everything
else, using a pytest fixture to flip the env var and restart
payment-service around just that one test.

**Why a separate script:** flipping `UNSAFE_IDEMPOTENCY_MODE` requires
restarting the *shared* payment-service container — every other test file
in the suite assumes the safe default, and pytest's default execution is
sequential within one session sharing that one container, so a fixture
-scoped restart would leave a window where other tests (if collected
before or interleaved incorrectly) could run against the unsafe container.
A fully separate script (`scripts/test_unsafe_idempotency.sh`) makes the
isolation explicit and impossible to get wrong via collection-order
changes, exactly matching `NEXT_STEP_REQUIREMENTS.md` §5.2's explicit
allowance for "a documented manual/CI-separate step" for this one case.

**Tradeoff:** "run the whole test suite" is now two commands instead of
one — documented clearly in the README specifically so this isn't a
surprise.

### 4.4 Kafka producer/consumer tests via `docker exec` + console scripts, not a host-side Kafka client library
**Decision:** `test_notification.py` shells out to
`kafka-console-producer.sh`/`kafka-console-consumer.sh` inside the `kafka`
container (via `docker compose exec`), rather than using `confluent-kafka`
from the host Python test environment.

**Alternatives:** add `confluent-kafka` to `requirements-test.txt` and
drive Kafka directly from the test process, matching how the *application*
code (order-service, notification-service) uses it.

**Why the shell-out approach, for tests specifically:** `confluent-kafka`
has no prebuilt wheel for every host OS/Python-version combination — it
failed to build on the very machine this suite was developed on (macOS,
Python 3.14, no system `librdkafka` installed), which would make the test
suite non-portable across contributors' machines for a dependency the
*application* doesn't even need on the host (the app runs it inside Linux
containers with prebuilt wheels, which work fine). The console scripts are
already how this project does manual Kafka verification elsewhere
(`API_REFERENCE.md`'s poison-pill flow), so reusing that exact mechanism
in the test avoids adding a fragile host build-toolchain dependency for no
real benefit.

**Tradeoff:** the test correlates a produced message to its consumer-side
log entries by a timestamp window (`_log_ts_to_epoch`) and content marker
rather than a precise partition/offset handed back by a real producer
client — slightly less precise, made robust in practice by waiting for a
specific expected log line to appear (polling, not a fixed sleep — see
§4.5) rather than trusting exact timing.

### 4.5 Polling for expected conditions, not fixed `sleep()` durations
**Decision:** tests that wait on asynchronous behavior (the poison-pill
→ DLQ path, review eligibility after simulated delivery) poll for the
specific expected log line or API response, with a generous deadline —
not a single fixed `time.sleep(N)` guess.

**Why:** a fixed sleep was tried first for the poison-pill test and proved
flaky in practice — under a busy shared Kafka consumer (processing backlog
from earlier tests in the same suite run), the actual time-to-DLQ varied
enough that a single guessed duration sometimes wasn't enough. This was
caught by literally running the suite and watching it fail intermittently,
not anticipated in advance — the fix (poll for the specific
`sending_to_dlq` log line, with a 30s deadline) is robust to that variance
by construction instead of hoping the guessed number is always big enough.

**Tradeoff:** polling tests take slightly more wall-clock code to write
than a bare `sleep()` — worth it for not having an intermittently-flaky
suite, which is worse than a slightly longer one.

---

## What to read next

- **`AUTHZ_BASELINE.md`** — the precise authorization matrix these
  decisions produce today, meant as a verification target for the Istio
  migration.
- **`API_REFERENCE.md`** — every endpoint, with live-captured example
  requests/responses and the multi-step flows these decisions combine into.
- **`PROJECT_STATUS.md`** — current state, what's verified, what's still
  open.
- **`NEXT_STEP_REQUIREMENTS.md`** — the fully-specified requirements doc
  most of Part 2 and Part 4 above were built against.
