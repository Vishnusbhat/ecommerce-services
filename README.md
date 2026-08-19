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
- **Cart clearing after checkout**: `cart-service.md` scopes that service to
  zero Kafka events produced/consumed, so nothing clears the cart after a
  successful order. It relies on the 24h TTL / client-side `DELETE`.
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
(purchase-eligibility gating on the simulated `order.delivered` event).
