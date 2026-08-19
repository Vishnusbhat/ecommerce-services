# API Reference & Testing Guide

Every example below was run live against the current stack (`docker compose
up -d --build`, then the commands as shown) — outputs are real, not
reconstructed. Product ids `P001`–`P005` are catalog-service's demo seed
data (see `services/catalog-service/app/seed.py`); stock/price numbers will
differ on your machine depending on what you've already exercised.

## Setup

```
cp .env.example .env
docker compose up -d --build
```

| Service | Port |
|---|---|
| auth-service | 8001 |
| catalog-service | 8002 |
| cart-service | 8003 |
| order-service | 8004 |
| payment-service | 8005 |
| notification-service | 8006 |
| review-service | 8007 |

Two auth conventions used throughout:

- **End-user auth**: `Authorization: Bearer <accessToken>` from
  `POST /auth/login`.
- **Internal-caller auth** (service-to-service endpoints only — you'd never
  call these from a browser/client): `X-Internal-Token: <INTERNAL_SERVICE_TOKEN
  from .env>` (default `dev-internal-token-change-me`) plus
  `X-Internal-Caller: <calling-service-name>`. See `AUTHZ_BASELINE.md` for
  exactly which caller names are allowed on which endpoint, and why this is
  a shared-secret stand-in, not real service identity.

Every error response, on every service, uses the same envelope:
```json
{"error": {"code": "SOME_CODE", "message": "human-readable", "requestId": "a1b2c3d4"}}
```

Every service also exposes, identically:
- `GET /healthz/live` → `{"status":"ok"}`
- `GET /healthz/ready` → `{"status":"ready"}` or `503` if a dependency (DB/Redis/Kafka/Mongo) is down
- `GET /metrics` → Prometheus text format

---

## auth-service (8001)

### `POST /auth/register` — no auth
```bash
curl -s -X POST http://localhost:8001/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"demo@example.com","password":"password123"}'
```
```json
{"id": "42130ac9-659d-45d7-92ab-b6a2f88ff76f", "email": "demo@example.com"}
```
`201`. Duplicate email → `409 EMAIL_TAKEN`. Password under 8 chars →
`422 VALIDATION_ERROR`.

### `POST /auth/login` — no auth
```bash
curl -s -X POST http://localhost:8001/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"demo@example.com","password":"password123"}'
```
```json
{
  "accessToken": "eyJhbGciOiJSUzI1NiIs...",
  "refreshToken": "c9710edc-668c-4003-962b-b1c8ce847b39",
  "tokenType": "bearer",
  "expiresIn": 900
}
```
`200`. Wrong password/unknown email → `401 INVALID_CREDENTIALS`.
`accessToken` is an RS256 JWT, 900s (15min) TTL. `refreshToken` is an opaque
UUID, 14-day TTL (`JWT_REFRESH_TOKEN_TTL_SECONDS` in `.env`).

### `POST /auth/refresh` — refresh token in body
```bash
curl -s -X POST http://localhost:8001/auth/refresh \
  -H 'Content-Type: application/json' \
  -d '{"refreshToken":"c9710edc-668c-4003-962b-b1c8ce847b39"}'
```
Returns a **new** access+refresh pair, `200`, same shape as login. The
presented refresh token is revoked as part of this call (rotation) — reuse
of the old one after a successful refresh returns `401 INVALID_TOKEN`, as
does reuse of any expired/revoked token.

### `POST /auth/logout` — JWT + refresh token in body
```bash
curl -s -X POST http://localhost:8001/auth/logout \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"refreshToken":"c9710edc-668c-4003-962b-b1c8ce847b39"}'
```
`204 No Content`. Revokes that specific refresh token (DB `revoked=true`
+ Redis blacklist). Refreshing with it afterward returns `401`.

### `GET /auth/.well-known/jwks.json` — no auth
```bash
curl -s http://localhost:8001/auth/.well-known/jwks.json
```
```json
{"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": "a86da62c86037470", "n": "...", "e": "AQAB"}]}
```
This is what every other service's JWT verification fetches (and caches)
to validate access tokens — see `gestalt_shared/security.py`.

---

## catalog-service (8002)

### `GET /catalog/products?limit=&offset=` — no auth
```bash
curl -s "http://localhost:8002/catalog/products?limit=3"
```
```json
{"items": [{"id": "P001", "name": "Wireless Mouse", "price_cents": 1999, "stock": 98}, ...],
 "total": 6, "limit": 3, "offset": 0}
```
Cache-aside via Redis (`PRODUCT_CACHE_TTL_SECONDS`, default 60s) — first
call is a DB read + cache write, subsequent identical calls are served
from Redis until TTL or a stock-affecting write invalidates it.

### `GET /catalog/products/{id}` — no auth
```bash
curl -s http://localhost:8002/catalog/products/P001
```
`200` with the product, or `404 PRODUCT_NOT_FOUND`.

### `GET /catalog/products/{id}/price` — internal only (`cart-service`, `order-service`)
```bash
curl -s http://localhost:8002/catalog/products/P002/price \
  -H "X-Internal-Token: dev-internal-token-change-me" \
  -H "X-Internal-Caller: cart-service"
```
```json
{"id": "P002", "price_cents": 6999, "stock": 47}
```
Without valid internal headers: `403 FORBIDDEN`.

### `POST /catalog/stock/reserve` — internal only (`order-service`)
```bash
curl -s -X POST http://localhost:8002/catalog/stock/reserve \
  -H "X-Internal-Token: dev-internal-token-change-me" \
  -H "X-Internal-Caller: order-service" \
  -H 'Content-Type: application/json' \
  -d '{"productId":"P001","quantity":2,"orderId":"O-demo"}'
```
```json
{"productId": "P001", "orderId": "O-demo", "reserved": true, "remainingStock": 96}
```
`200` on success. `409 INSUFFICIENT_STOCK` if `quantity > stock` (row-locked
via `SELECT ... FOR UPDATE` — see "Concurrency" flow below). Wrong caller
(e.g. `cart-service`) → `403 FORBIDDEN`.

### `POST /catalog/stock/release` — internal only (`order-service`)
Same shape as reserve, adds stock back. Used as the saga's compensating
action; `reserved: false` in the response signals a release, not a
reservation.

---

## cart-service (8003)

Cart contents live in a Redis hash `cart:{userId}`, TTL 24h, refreshed on
every write.

### `GET /cart` — JWT
```bash
curl -s http://localhost:8003/cart -H "Authorization: Bearer $ACCESS_TOKEN"
```
```json
{"items": [{"productId": "P001", "quantity": 2}]}
```

### `POST /cart/items` — JWT
```bash
curl -s -X POST http://localhost:8003/cart/items \
  -H "Authorization: Bearer $ACCESS_TOKEN" -H 'Content-Type: application/json' \
  -d '{"productId":"P001","quantity":2}'
```
Returns the full updated cart, `200`. Adding beyond available stock (checked
live against catalog-service) → `409 INSUFFICIENT_STOCK`. Repeated adds of
the same product **increment** the existing quantity.

### `DELETE /cart/items/{productId}` — JWT
```bash
curl -s -X DELETE http://localhost:8003/cart/items/P001 -H "Authorization: Bearer $ACCESS_TOKEN"
```
`204 No Content`, idempotent (no error if the id wasn't in the cart).

### `POST /cart/checkout-intent` — JWT
```bash
curl -s -X POST http://localhost:8003/cart/checkout-intent -H "Authorization: Bearer $ACCESS_TOKEN"
```
Returns the current cart as a read-only snapshot (does **not** clear it —
see `AUTHZ_BASELINE.md`/`PROJECT_STATUS.md` for why). `400 EMPTY_CART` if
there's nothing in it.

### `DELETE /cart/items:batch` — internal only (`order-service`)
```bash
curl -s -X DELETE http://localhost:8003/cart/items:batch \
  -H "X-Internal-Token: dev-internal-token-change-me" \
  -H "X-Internal-Caller: order-service" \
  -H "X-User-Id: 42130ac9-659d-45d7-92ab-b6a2f88ff76f" \
  -H 'Content-Type: application/json' \
  -d '{"productIds":["P003","P999-not-there"]}'
```
```json
{"removed": ["P003"]}
```
Removes exactly the listed ids that were actually present; anything not
found is silently omitted, not an error. This is what order-service calls
after every checkout — see the "Cart clearing" flow below.

---

## order-service (8004) — the saga orchestrator

### `POST /orders` — JWT, requires `Idempotency-Key` header
```bash
curl -s -X POST http://localhost:8004/orders \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Idempotency-Key: my-unique-key-1" \
  -H 'Content-Type: application/json' \
  -d '{"items":[{"productId":"P001","quantity":2}]}'
```
```json
{
  "id": "3e7cc71d-aaff-4db2-9774-0833f51c2202",
  "userId": "42130ac9-659d-45d7-92ab-b6a2f88ff76f",
  "status": "PAID",
  "amountCents": 3998,
  "items": [{"productId": "P001", "quantity": 2}],
  "failureReason": null,
  "createdAt": "2026-08-19T16:57:48"
}
```
`201`, always — the saga runs synchronously and the response reflects its
terminal state (`PAID` or `FAILED`), never a pending/async response.
`items` is **optional**: omit it (`-d '{}'`) and order-service sources them
from your cart instead (this is the flow the K6 script in
`gestalt-commerce-docs/docs/08-load-testing.md` uses). Missing
`Idempotency-Key` → `400 MISSING_IDEMPOTENCY_KEY`. See the "Checkout saga"
flow below for what happens internally and all the ways it can fail.

### `GET /orders/{id}` — JWT, owner only
```bash
curl -s http://localhost:8004/orders/3e7cc71d-aaff-4db2-9774-0833f51c2202 \
  -H "Authorization: Bearer $ACCESS_TOKEN"
```
`200` with the order, or `404 ORDER_NOT_FOUND` if it's not yours (not `403`
— avoids confirming another user's order id exists).

### `GET /orders?limit=&offset=` — JWT
```bash
curl -s http://localhost:8004/orders -H "Authorization: Bearer $ACCESS_TOKEN"
```
Your orders, newest first.

---

## payment-service (8005)

### `POST /payments/charge` — internal only (`order-service`)
```bash
curl -s -X POST http://localhost:8005/payments/charge \
  -H "X-Internal-Token: dev-internal-token-change-me" \
  -H "X-Internal-Caller: order-service" \
  -H 'Content-Type: application/json' \
  -d '{"orderId":"O-demo","amount":4599,"currency":"INR","idempotencyKey":"docs-charge-key"}'
```
```json
{
  "orderId": "O-demo", "amount": 4599, "currency": "INR",
  "idempotencyKey": "docs-charge-key", "status": "CHARGED",
  "chargedAt": "2026-08-19T16:57:59.203151+00:00"
}
```
`200` on success. A **synthetic decline** (see `FAILURE_RATE` below)
returns `402 PAYMENT_DECLINED`. Re-POSTing the **same** `idempotencyKey`
— even with a different `amount`/`orderId` — replays the original cached
result byte-for-byte rather than charging again (see "Payment idempotency"
flow below).

### `GET /payments/{idempotencyKey}` — internal only (`order-service`)
```bash
curl -s http://localhost:8005/payments/docs-charge-key \
  -H "X-Internal-Token: dev-internal-token-change-me" -H "X-Internal-Caller: order-service"
```
Looks up a prior charge result without re-processing. `404 NOT_FOUND` if
that key was never charged; `409 CHARGE_IN_PROGRESS` if a concurrent charge
for that key hasn't resolved yet.

### Chaos-testing env vars (`.env`)
| Var | Effect |
|---|---|
| `PAYMENT_FAILURE_RATE` (0.0–1.0) | probability a charge synthetically declines |
| `PAYMENT_LATENCY_MS_MIN` / `_MAX` | artificial processing delay range |
| `UNSAFE_IDEMPOTENCY_MODE` | `true` swaps the safe `SET NX` claim for the naive GET-then-process-then-SET race — see `scripts/test_unsafe_idempotency.sh`, **never enable outside that script** |

Flip any of these in `.env`, then `docker compose up -d payment-service` to
apply (see the "Payment decline & compensation" flow below for a worked
example).

---

## notification-service (8006)

No business HTTP endpoints — pure Kafka consumer (`notification-service-group`
on `order-events`), only `/healthz/*` and `/metrics`. To observe it working,
tail its logs while placing an order:
```bash
docker compose logs -f notification-service
```
See "Async fan-out" flow below.

---

## review-service (8007)

### `GET /reviews/product/{productId}` — no auth
```bash
curl -s http://localhost:8007/reviews/product/P002
```
```json
{"items": [{"id": "6a85dc7533bea29ea93dfb48", "productId": "P002", "userId": "...",
            "orderId": "...", "rating": 5, "comment": "great", "createdAt": "..."}]}
```

### `POST /reviews` — JWT, gated on delivery
```bash
curl -s -X POST http://localhost:8007/reviews \
  -H "Authorization: Bearer $ACCESS_TOKEN" -H 'Content-Type: application/json' \
  -d '{"productId":"P002","orderId":"3e7cc71d-...","rating":5,"comment":"nice"}'
```
`201` once eligible (see "Delivery + review eligibility" flow); before
delivery, or for a product you never ordered: `403 NOT_ELIGIBLE`. A second
review for the same product: also `403 NOT_ELIGIBLE` (already reviewed).

---

## Flows

### 1. Auth lifecycle
`register` → `login` → (use `accessToken` for 15min) → `refresh` before it
expires (rotates both tokens; the old refresh token stops working) →
`logout` (revokes the current refresh token). Each step's exact request/
response is above.

### 2. Browse → cart → checkout (two ways)
**Explicit items** (works standalone, no cart needed):
```bash
curl -s -X POST http://localhost:8004/orders -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: k1" -H 'Content-Type: application/json' \
  -d '{"items":[{"productId":"P001","quantity":2},{"productId":"P002","quantity":1}]}'
```
**From the cart** (empty body — matches the K6 reference script):
```bash
curl -s -X POST http://localhost:8003/cart/items -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"productId":"P001","quantity":2}'
curl -s -X POST http://localhost:8004/orders -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: k2" -H 'Content-Type: application/json' -d '{}'
```
Both end the same way: order-service prices every line item against
catalog-service, persists a `PENDING` order, reserves stock, charges
payment-service, and returns the terminal state.

### 3. Checkout saga — internals and every failure branch
Inside `POST /orders` (see `services/order-service/app/routers/orders.py`):
1. Price snapshot from catalog-service (`amount_cents = Σ price × qty`);
   unknown product id → `404 PRODUCT_NOT_FOUND` before anything is persisted.
2. Insert `Order(status=PENDING)` + line items. Publishes `order.created`
   to Kafka immediately (before the saga resolves).
3. Reserve stock per line item. **Any** failure (insufficient stock,
   product vanished) → release whatever was already reserved in this same
   request, mark `FAILED`, publish `order.failed`, **clear the cart of the
   attempted items anyway** (see flow 5), return `201` with
   `status: "FAILED"`.
4. Charge payment-service, same `Idempotency-Key`. Decline/timeout →
   release **all** reserved stock, mark `FAILED`, publish `order.failed`,
   clear the cart, return `201` `FAILED`.
5. Success → mark `PAID`, publish `order.paid`, clear the cart, return
   `201` `PAID`.

Reproduce the insufficient-stock branch:
```bash
curl -s -X POST http://localhost:8004/orders -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: k3" -H 'Content-Type: application/json' \
  -d '{"items":[{"productId":"P003","quantity":99999}]}'
# -> status: "FAILED", failureReason: "INSUFFICIENT_STOCK", stock unchanged
```

### 4. Payment decline & compensation
```bash
sed -i.bak 's/PAYMENT_FAILURE_RATE=0.0/PAYMENT_FAILURE_RATE=1.0/' .env
docker compose up -d payment-service
curl -s -X POST http://localhost:8004/orders -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: k4" -H 'Content-Type: application/json' \
  -d '{"items":[{"productId":"P004","quantity":1}]}'
# -> status: "FAILED", failureReason: "PAYMENT_DECLINED"
mv .env.bak .env && docker compose up -d payment-service
```
Check catalog stock for `P004` before/after — it's unchanged (reserved,
then released as compensation).

### 5. Cart clearing (both outcomes)
Add something to the cart, then check out via cart-sourced empty-body
`POST /orders`. `GET /cart` afterward is `{"items": []}` whether the order
ended `PAID` or `FAILED` (including a reconciliation-job-forced failure —
this is a deliberate, uniform rule, not just the happy path).

### 6. Idempotent retry (order-level)
```bash
KEY="retry-demo-1"
curl -s -X POST http://localhost:8004/orders -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"items":[{"productId":"P005","quantity":1}]}'
curl -s -X POST http://localhost:8004/orders -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"items":[{"productId":"P005","quantity":1}]}'
# -> identical order id both times; stock decremented ONCE
```

### 7. Concurrency: no overselling
```bash
docker compose exec mariadb mariadb -uroot -prootpass \
  -e "UPDATE catalog_db.products SET stock=1 WHERE id='P004';"
TOK=dev-internal-token-change-me
for i in 1 2 3 4 5; do
  curl -s -X POST http://localhost:8002/catalog/stock/reserve \
    -H "X-Internal-Token: $TOK" -H "X-Internal-Caller: order-service" \
    -H 'Content-Type: application/json' \
    -d "{\"productId\":\"P004\",\"quantity\":1,\"orderId\":\"race-$i\"}" &
done; wait
# -> exactly one 200, four 409 INSUFFICIENT_STOCK; final stock: 0
```
Guaranteed by `SELECT ... FOR UPDATE` row-locking in
`catalog-service/app/routers/catalog.py`.

### 8. Payment idempotency under concurrency
Same pattern as above, 5 concurrent `POST /payments/charge` calls with the
**same** `idempotencyKey` → all 5 responses have byte-identical
`chargedAt`. See `services/payment-service/tests/test_payment.py` for the
exact assertion, and `scripts/test_unsafe_idempotency.sh` for a controlled
demonstration of what happens **without** the atomic claim (double charge).

### 9. Async fan-out: order events → notification-service
Any `POST /orders` call publishes `order.created`, then `order.paid` or
`order.failed`, to the `order-events` Kafka topic. notification-service
(consumer group `notification-service-group`) picks these up independently
and logs (and optionally Slack-webhooks, if `SLACK_WEBHOOK_URL` is set) a
line per event:
```bash
docker compose logs notification-service --tail 20
# {"...", "message": "notification: Order <id> paid — 3998", ...}
```

### 10. Delivery simulation → review eligibility → review
There's no real fulfillment pipeline — order-service simulates it.
`DELIVERY_SIMULATION_DELAY_SECONDS` (default 30s) after an order goes
`PAID`, a background loop flips it to delivered and publishes
`order.delivered`, which review-service consumes to populate its
eligibility collection.
```bash
# immediately after a PAID order: rejected
curl -s -X POST http://localhost:8007/reviews -H "Authorization: Bearer $TOKEN" \
  -d '{"productId":"P002","orderId":"<order-id>","rating":5,"comment":"too soon"}'
# -> 403 NOT_ELIGIBLE

sleep 35   # wait out the simulated delivery delay

curl -s -X POST http://localhost:8007/reviews -H "Authorization: Bearer $TOKEN" \
  -d '{"productId":"P002","orderId":"<order-id>","rating":5,"comment":"great"}'
# -> 201
```
To iterate faster locally, temporarily lower
`DELIVERY_SIMULATION_DELAY_SECONDS`/`DELIVERY_CHECK_INTERVAL_SECONDS` in
`.env` and restart order-service.

### 11. Poison-pill → dead-letter queue
Publish a deliberately malformed message directly onto `order-events` and
watch notification-service retry it exactly `MAX_PROCESSING_ATTEMPTS`
(default 3) times before forwarding to `order-events-dlq`:
```bash
echo "not valid json {{{" | docker compose exec -T kafka \
  /opt/kafka/bin/kafka-console-producer.sh --bootstrap-server localhost:9092 \
  --topic order-events --property "parse.key=false"

docker compose logs notification-service --tail 10
# 3x "processing_failed", then "sending_to_dlq"

docker compose exec -T kafka /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 --topic order-events-dlq --from-beginning --timeout-ms 5000
```
review-service has the identical retry→DLQ behavior on its own consumer
group for anything on `order-events` it can't parse.

### 12. Cart abandonment
A background job in cart-service (`CART_ABANDONMENT_SCAN_INTERVAL_SECONDS`,
default 5min) scans for carts with items untouched for
`CART_ABANDONMENT_THRESHOLD_SECONDS` (default 30min) and increments
`cart_abandonment_total` once per cart. Lower both in `.env` to observe it
quickly:
```bash
# .env: CART_ABANDONMENT_THRESHOLD_SECONDS=8, CART_ABANDONMENT_SCAN_INTERVAL_SECONDS=3
docker compose up -d cart-service
curl -s -X POST http://localhost:8003/cart/items -H "Authorization: Bearer $TOKEN" \
  -d '{"productId":"P001","quantity":1}'
sleep 15
curl -s http://localhost:8003/metrics | grep cart_abandonment_total
```

### 13. Reconciliation: crashed-mid-saga orders
Not directly triggerable via the API (it's for a process crash between
reserving stock and charging), but the job itself runs every
`RECONCILIATION_INTERVAL_SECONDS` (default 30s) and force-fails any order
stuck `PENDING` longer than `PENDING_ORDER_TIMEOUT_SECONDS` (default 60s),
releasing any reserved stock and clearing the cart, same as a live
`FAILED` outcome. See `services/order-service/app/reconciliation.py`.

### 14. Business metrics — what to look at
```bash
curl -s http://localhost:8004/metrics | grep -E "^orders_|^saga_|^order_amount"
curl -s http://localhost:8005/metrics | grep -E "^payment_"
curl -s http://localhost:8002/metrics | grep -E "^stock_reservation|^catalog_cache"
curl -s http://localhost:8003/metrics | grep -E "^cart_"
```
Full list and exact increment points: `NEXT_STEP_REQUIREMENTS.md` §1.

### 15. Request tracing across services (no Jaeger needed yet)
Pass your own `x-request-id`, then grep every service's logs for it:
```bash
RID="trace-demo-1"
curl -s -X POST http://localhost:8004/orders -H "Authorization: Bearer $TOKEN" \
  -H "x-request-id: $RID" -H "Idempotency-Key: k5" -H 'Content-Type: application/json' \
  -d '{"items":[{"productId":"P001","quantity":1}]}' >/dev/null
docker compose logs order-service catalog-service payment-service | grep "$RID"
```
Every hop (order-service, catalog reserve/release, payment charge) carries
the same id — see `AUTHZ_BASELINE.md`'s note and
`gestalt_shared/http_client.py` for how.
