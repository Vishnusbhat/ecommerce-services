# notification-service

## Responsibility
Pure async consumer — listens to `order-events` and sends notifications. No synchronous API surface at all, which makes it a good example of a service that exists entirely to demonstrate consumer-group and offset-commit semantics.

## Tech stack
- Kafka consumer group: `notification-service-group`
- Notification "delivery": for this project, real delivery channels are out of scope — log the notification content and, more usefully, **post it to a real Slack webhook**, reusing your existing Slack alerting bot pattern from Dealshare rather than building a fake email sender. This is both less work and a more authentic artifact, since you already know this integration cold.

## Consumption logic
```
consumer.subscribe(["order-events"])
for message in consumer:
    event = parse(message)
    match event.type:
        case "order.created": notify(f"Order {event.orderId} received")
        case "order.paid": notify(f"Order {event.orderId} paid — {event.amount}")
        case "order.failed": notify(f"Order {event.orderId} failed: {event.reason}")
    consumer.commit(message.offset)   # commit AFTER processing, not on receipt
```

The `commit-after-processing` placement is the whole point of this service's design. If the process crashes mid-`notify()` call, Kafka redelivers that message on restart because the offset was never committed — you get at-least-once delivery, meaning a notification might theoretically be sent twice in a crash-during-send edge case, but never zero times. Committing on receipt instead would give you at-most-once — faster, but silently loses messages on a crash. This tradeoff is worth being able to state explicitly, since it's a real design decision with a real consequence, not a default you fell into.

## Dependencies
- **Called by:** nobody synchronously — this is deliberate; a client-facing service should never be able to block waiting on notification delivery
- **Consumes from:** `order-events` Kafka topic

## Failure modes owned
- Consumer lag under sustained load or downtime — see chaos scenario 4 in [../docs/06-resilience-and-chaos-engineering.md](../docs/06-resilience-and-chaos-engineering.md)
- Poison-pill messages (a malformed event that crashes the consumer on every attempt, forever) — worth adding a dead-letter-topic pattern: after N failed processing attempts on the same message, publish it to `order-events-dlq` instead of retrying forever and blocking the whole partition. This is a small addition that signals real production awareness.

## Resource footprint (suggested)
`requests: { cpu: 50m, memory: 64Mi }`, `limits: { cpu: 200m, memory: 128Mi }` — single replica is fine for local/demo scale; Kafka's consumer-group model means adding replicas later is just a partition-count question, not a redesign.
