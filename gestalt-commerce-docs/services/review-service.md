# review-service

## Responsibility
Product reviews and ratings, gated to verified purchasers only. This service exists in the project mainly to introduce a second data paradigm (document store) and a second async consumption pattern distinct from `notification-service`'s fire-and-forget style — here, the consumed event actually changes what the API is allowed to do next.

## Tech stack
- Datastore: MongoDB — reuses your existing replica-set operational experience, and gives you a legitimate reason to talk about a document model versus MariaDB's relational model in the same project
- Kafka consumer group: `review-service-group`, subscribed to `order-events`, specifically the `order.delivered` event type

## Data model (MongoDB)
```json
{
  "_id": "ObjectId(...)",
  "productId": "P123",
  "userId": "U789",
  "orderId": "O456",
  "rating": 4,
  "comment": "Good product, arrived on time.",
  "createdAt": "2026-08-19T10:00:00Z"
}
```
And a separate collection tracking purchase-verification eligibility, populated by the consumer:
```json
{ "_id": "U789:P123", "orderId": "O456", "deliveredAt": "2026-08-19T09:00:00Z", "reviewed": false }
```

## API surface
See [../docs/02-api-contracts.md](../docs/02-api-contracts.md#review-service). `POST /reviews` checks the eligibility collection before accepting a review — this is the enforcement point for "only verified purchasers can review."

## Dependencies
- **Called by:** Istio ingress gateway (public reads of reviews, authenticated writes)
- **Consumes from:** `order-events` Kafka topic (filters for `order.delivered`)

## Why MongoDB here specifically
Reviews are a genuinely good fit for a document model — variable structure (optional photo attachments, variable-length comments, potential future fields like "verified purchase badge" or "helpful votes" without a schema migration), and no need for cross-document transactions the way `order-service`'s saga needs relational guarantees. Using MariaDB everywhere "because it's what I know" would actually be a worse design choice here — worth being able to articulate that reasoning rather than just defaulting to familiarity.

## Failure modes owned
- Eligibility-collection write failing after the Kafka message is consumed but before the commit — same offset-commit-after-processing discipline as `notification-service` applies here, for the same reason.
- MongoDB replica set failover — this is a direct, real reuse of your zero-downtime MongoDB replica set rotation experience; worth explicitly testing a primary step-down here and confirming reads/writes recover without application-level changes.

## Resource footprint (suggested)
`requests: { cpu: 50m, memory: 96Mi }`, `limits: { cpu: 200m, memory: 192Mi }`.
