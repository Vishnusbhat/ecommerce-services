# API Contracts

## Standard error envelope

Every service returns errors in the same shape, so Envoy/Grafana error-rate dashboards and client handling stay consistent:

```json
{
  "error": {
    "code": "INSUFFICIENT_STOCK",
    "message": "Requested quantity exceeds available stock",
    "requestId": "a1b2c3d4"
  }
}
```

`requestId` is populated from the `x-request-id` header Envoy auto-injects — this is what ties a log line back to a Jaeger trace.

---

## auth-service

| Method | Path | Description | Auth |
|---|---|---|---|
| POST | `/auth/register` | Create user | none |
| POST | `/auth/login` | Returns JWT + refresh token | none |
| POST | `/auth/refresh` | Exchange refresh token for new JWT | refresh token |
| POST | `/auth/logout` | Revoke refresh token (blacklist in Redis) | JWT |
| GET | `/auth/.well-known/jwks.json` | Public keys for JWT verification | none — consumed by Envoy's `RequestAuthentication`, not by other services |

## catalog-service

| Method | Path | Description | Auth |
|---|---|---|---|
| GET | `/catalog/products` | List products (paginated, cache-aside via Redis) | none |
| GET | `/catalog/products/{id}` | Product detail | none |
| POST | `/catalog/stock/reserve` | Reserve stock for an order (row-locked, called only by order-service) | mTLS + AuthorizationPolicy (order-service identity only) |
| POST | `/catalog/stock/release` | Compensating release (saga rollback) | mTLS + AuthorizationPolicy (order-service identity only) |
| GET | `/catalog/products/{id}/price` | Price/stock check | mTLS (cart-service, order-service) |

`POST /catalog/stock/reserve` request:
```json
{ "productId": "P123", "quantity": 2, "orderId": "O456" }
```
Response `409` if `SELECT stock FOR UPDATE` shows insufficient quantity — this is the exact race condition class you flagged as an SDI gap, made concrete here.

## cart-service

| Method | Path | Description | Auth |
|---|---|---|---|
| GET | `/cart` | Get current user's cart | JWT (validated by Envoy) |
| POST | `/cart/items` | Add item (validates price/stock via catalog-service) | JWT |
| DELETE | `/cart/items/{productId}` | Remove item | JWT |
| POST | `/cart/checkout-intent` | Freeze cart, hand off to order-service | JWT |

Cart is stored as a Redis hash keyed by `cart:{userId}`, TTL 24h, no persistence beyond that — deliberately ephemeral to keep this service stateless from the mesh's perspective.

## order-service

| Method | Path | Description | Auth |
|---|---|---|---|
| POST | `/orders` | Create order from cart (runs the saga) | JWT, requires `Idempotency-Key` header |
| GET | `/orders/{id}` | Get order status | JWT (owner only) |
| GET | `/orders` | List current user's orders | JWT |

## payment-service

| Method | Path | Description | Auth |
|---|---|---|---|
| POST | `/payments/charge` | Mock-charge, idempotent | mTLS + AuthorizationPolicy (order-service identity only) |
| GET | `/payments/{idempotencyKey}` | Look up prior result | mTLS (order-service only) |

`POST /payments/charge` request:
```json
{ "orderId": "O456", "amount": 4599, "currency": "INR", "idempotencyKey": "8f14e45f" }
```
Configurable via env vars for chaos testing: `FAILURE_RATE` (0.0–1.0), `LATENCY_MS_MIN` / `LATENCY_MS_MAX`. This is separate from Istio-level fault injection — having both lets you demonstrate app-level and mesh-level failure injection as distinct techniques.

## review-service

| Method | Path | Description | Auth |
|---|---|---|---|
| GET | `/reviews/product/{productId}` | List reviews for a product | none |
| POST | `/reviews` | Submit review | JWT, only if `order.delivered` event was seen for that user+product |

---

## Kafka event schemas

**Topic: `order-events`** (partitioned by `orderId`, retention 7 days)

| Event type | Producer | Consumers | Payload |
|---|---|---|---|
| `order.created` | order-service | notification-service | `{orderId, userId, status: "PENDING", items[], amount}` |
| `order.paid` | order-service | notification-service, review-service | `{orderId, userId, amount, paidAt}` |
| `order.failed` | order-service | notification-service | `{orderId, userId, reason}` |
| `order.delivered` | order-service (simulated via a delay/cron for demo purposes) | review-service | `{orderId, userId, productIds[], deliveredAt}` |

Consumer group semantics: both `notification-service` and `review-service` run as **separate consumer groups** on `order-events`, so each gets every message independently — this is the fan-out pattern, distinct from a queue where one consumer would steal messages from another. Offsets are committed manually, after processing completes, not on receipt — this is what gives at-least-once delivery and is the same offset-semantics discussion from your TinyURL SDI session, reused here in a second context.
