# order-service

## Responsibility
The saga orchestrator for checkout. This is the most important service in the system to understand deeply — it's where the distributed-transaction reasoning lives, and it's the service most likely to come up in interview follow-up questions.

## Tech stack
- Datastore: MariaDB (orders, order line items, saga state machine)
- Kafka producer: publishes `order.created`, `order.paid`, `order.failed` to the `order-events` topic

## API surface
See [../docs/02-api-contracts.md](../docs/02-api-contracts.md#order-service).

## Data model (MariaDB)
```sql
CREATE TABLE orders (
  id CHAR(36) PRIMARY KEY,
  user_id CHAR(36) NOT NULL,
  status ENUM('PENDING','PAID','FAILED') NOT NULL DEFAULT 'PENDING',
  amount_cents INT NOT NULL,
  idempotency_key VARCHAR(64) UNIQUE NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
CREATE TABLE order_items (
  order_id CHAR(36) NOT NULL,
  product_id VARCHAR(20) NOT NULL,
  quantity INT NOT NULL,
  FOREIGN KEY (order_id) REFERENCES orders(id)
);
```
Note `idempotency_key UNIQUE` at the database level — this is a second, independent layer of idempotency protection beyond the Redis check in `payment-service`. If a client retries the entire `POST /orders` call (not just the internal payment call), the DB unique constraint is what stops a duplicate order row from ever being created, even under a race between two near-simultaneous retries.

## The saga (full detail)
See the sequence diagram in [../docs/01-architecture-overview.md](../docs/01-architecture-overview.md#checkout-flow-the-core-saga). Summary of the state machine:

```
PENDING --(stock reserved, payment succeeds)--> PAID
PENDING --(stock reserve fails)--> FAILED (no compensating action needed, nothing was reserved)
PENDING --(stock reserved, payment fails)--> FAILED (compensating action: release stock)
```

Orchestration was chosen over choreography (pure event-driven, no central coordinator) specifically because the flow is short (two dependencies) and needs deterministic, immediately-visible failure — a client waiting on `POST /orders` needs a synchronous answer, not a webhook later. Choreography would be the better fit for a longer chain (e.g., an actual multi-day shipping pipeline), which is a good distinction to be able to draw if asked "why not do this with events only."

## Dependencies
- **Called by:** Istio ingress gateway
- **Calls:** `catalog-service` (reserve/release stock), `payment-service` (charge)
- **Publishes to:** `order-events` Kafka topic

## Events produced
| Event | When |
|---|---|
| `order.created` | immediately on receiving the checkout request, before the saga resolves — lets `notification-service` send an "order received" confirmation fast |
| `order.paid` | saga completes successfully |
| `order.failed` | saga fails at either step |

## Failure modes owned
- Partial saga failure (stock reserved, then the process crashes before calling payment) — this needs either a reconciliation job that scans for orders stuck in `PENDING` past a timeout and force-fails them (releasing stock), or a more sophisticated outbox pattern. For this project, a simple periodic reconciliation job is the right scope — document it as a known simplification versus a full transactional outbox.
- Double-charge under retry — mitigated by the idempotency key flowing through to `payment-service`; see chaos scenario 5.

## Resource footprint (suggested)
`requests: { cpu: 100m, memory: 128Mi }`, `limits: { cpu: 500m, memory: 256Mi }`.
