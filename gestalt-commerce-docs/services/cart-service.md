# cart-service

## Responsibility
Ephemeral shopping cart state per user. Deliberately the simplest service in the system — its job is to demonstrate that not every service needs a relational database, and that "stateless from the mesh's perspective" (no local disk state, all state in Redis) is a legitimate and common pattern.

## Tech stack
- Datastore: Redis only. No relational database, no MongoDB.
- Data shape: a Redis hash per user, key `cart:{userId}`, field-value pairs of `productId → quantity`. TTL of 24h refreshed on every write.

## API surface
See [../docs/02-api-contracts.md](../docs/02-api-contracts.md#cart-service).

## Dependencies
- **Called by:** Istio ingress gateway
- **Calls:** `catalog-service` (validate price and stock availability before accepting an add-to-cart — you don't want a stale cart advertising a price or stock level that's no longer true)

## Events
None produced or consumed.

## Failure modes owned
- Redis unavailability means carts are entirely unavailable — there's no fallback datastore, which is a deliberate simplicity tradeoff worth being able to defend: "for a real production cart, I'd consider a short-lived write-behind to a durable store, but for this project the failure mode of 'cart briefly unavailable, nothing lost that mattered for more than a session' is acceptable and I chose simplicity over resilience here on purpose."
- TTL expiry mid-session — worth deciding and documenting the actual UX behavior (silently empty cart vs. explicit "your cart expired" message) rather than leaving it unspecified.

## Resource footprint (suggested)
`requests: { cpu: 50m, memory: 64Mi }`, `limits: { cpu: 200m, memory: 128Mi }` — cheapest service in the system, no DB connection pool to size.
