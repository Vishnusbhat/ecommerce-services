# payment-service

## Responsibility
Mocked payment processing — deliberately **not** integrated with a real payment gateway (no Razorpay/Stripe API keys, no PCI-adjacent compliance surface). Its entire value in this project is demonstrating idempotent handling of a non-idempotent-by-nature operation ("charge a card") under retries and concurrent load.

## Tech stack
- No persistent database — Redis holds only the idempotency-key → result mapping, with a TTL (e.g. 24h) long enough to cover any realistic retry window but not indefinite.
- Configurable failure injection via environment variables, independent of Istio-level fault injection:
  - `FAILURE_RATE` (0.0–1.0) — probability a charge attempt returns a synthetic failure
  - `LATENCY_MS_MIN` / `LATENCY_MS_MAX` — artificial processing delay range

## API surface
See [../docs/02-api-contracts.md](../docs/02-api-contracts.md#payment-service).

## Idempotency design (the core mechanic)

```
on POST /payments/charge with idempotencyKey K:
    existing = REDIS.GET(f"idempotency:{K}")
    if existing exists:
        return existing   # do NOT process again, regardless of what the original result was
    result = process_charge()   # simulated, possibly slow, possibly fails
    REDIS.SET(f"idempotency:{K}", result, EX=86400)
    return result
```

The critical detail: the Redis `SET` must happen **before** returning to the caller, and ideally the check-and-reserve should use `SET key value NX` (set-if-not-exists) as an atomic claim on the key *before* processing starts, not just after — otherwise two near-simultaneous retries can both pass the `GET` check (finding nothing yet), both proceed to charge, and both write a result, defeating the whole point. This is exactly the two-phase-write-across-non-transactional-systems failure class you flagged as a gap: Redis and the "charge" side effect aren't in the same transaction, so the *ordering* of the claim relative to the side effect is what makes it safe or unsafe.

```
# safer version
claimed = REDIS.SET(f"idempotency:{K}", "IN_PROGRESS", NX=True, EX=86400)
if not claimed:
    # someone else already claimed this key — poll or return their result
    return REDIS.GET(f"idempotency:{K}")
result = process_charge()
REDIS.SET(f"idempotency:{K}", result, EX=86400)   # overwrite IN_PROGRESS with final result
return result
```

## Dependencies
- **Called by:** `order-service` only — enforced by `AuthorizationPolicy`, see [../docs/04-istio-service-mesh.md](../docs/04-istio-service-mesh.md)
- **Calls:** none (Redis is a datastore, not a service dependency in the mesh sense)

## Events
None. Payment result is returned synchronously; `order-service` is responsible for publishing the resulting `order.paid`/`order.failed` event.

## Failure modes owned
- The race condition above if the `NX`-claim step is skipped — this is the one bug in the whole project worth deliberately reintroducing once, to see the double-processing happen, then fixing it and confirming it's gone. That before/after pair is the strongest single piece of evidence of distributed-systems understanding in the entire repo.
- Circuit breaking and retry behavior owned by `order-service`'s `VirtualService`/`DestinationRule`, not by this service itself — `payment-service` just needs to be correctly idempotent; the mesh handles the retry policy.

## Resource footprint (suggested)
`requests: { cpu: 100m, memory: 64Mi }`, `limits: { cpu: 300m, memory: 128Mi }`, HPA target 70% CPU — this is the service you deliberately drive into saturation during load testing.
