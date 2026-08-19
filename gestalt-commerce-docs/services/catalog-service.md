# catalog-service

## Responsibility
Owns product data, pricing, and stock counts. Provides the stock reservation/release API that `order-service` uses as part of the checkout saga. This is the service most worth building carefully — the row-locking behavior here is the direct callback to your TinyURL SDI session.

## Tech stack
- Datastore: MariaDB (source of truth for stock), Redis (cache-aside for product reads — hot products cached, writes go through MariaDB and invalidate the cache key)
- Cache-aside pattern: on `GET /catalog/products/{id}`, check Redis first; on miss, read MariaDB and populate Redis with a TTL; on any stock-affecting write, explicitly delete the cache key rather than trying to update it in place (avoids the class of bugs where cache and DB drift out of sync under concurrent writes)

## API surface
See [../docs/02-api-contracts.md](../docs/02-api-contracts.md#catalog-service).

## Data model (MariaDB)
```sql
CREATE TABLE products (
  id VARCHAR(20) PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  price_cents INT NOT NULL,
  stock INT NOT NULL DEFAULT 0
);
```

## The reservation query (the important part)
```sql
START TRANSACTION;
SELECT stock FROM products WHERE id = ? FOR UPDATE;
-- application checks: is stock >= requested quantity?
UPDATE products SET stock = stock - ? WHERE id = ?;
COMMIT;
```
`FOR UPDATE` row-locks the product row for the transaction duration, so two concurrent checkout requests for the last unit of stock can't both read "1 available" and both succeed — the second transaction blocks until the first commits, then sees the updated (now zero) count. This is the exact overselling race condition your SDI prep flagged as a generalization gap, made concrete and testable here rather than staying abstract.

## Dependencies
- **Called by:** Istio ingress gateway (public reads), `cart-service` (price/stock check), `order-service` (reserve/release — restricted via `AuthorizationPolicy` to `order-service`'s identity only)
- **Calls:** none

## Events
None. Stock reservation is deliberately synchronous — an async "maybe your item is reserved" response would be a worse UX and a harder bug to reason about for no real benefit here.

## Failure modes owned
- Lock contention under high concurrent checkout of the same product — worth deliberately load-testing a single hot product (see chaos scenario framing in [../docs/06-resilience-and-chaos-engineering.md](../docs/06-resilience-and-chaos-engineering.md))
- Cache/DB drift if the cache-invalidation-on-write step is ever skipped in a code path — this is a real bug class worth writing a test for, not just documenting
- MariaDB primary failure — see chaos scenario 3

## Resource footprint (suggested)
`requests: { cpu: 100m, memory: 128Mi }`, `limits: { cpu: 500m, memory: 256Mi }`, HPA target 70% CPU (see [../docs/03-kubernetes-deployment.md](../docs/03-kubernetes-deployment.md)).
