# Resilience & Chaos Engineering

Five scenarios, deep rather than broad — each one should end with a screenshot, a fix, and a short written postmortem. Four or five well-understood incidents beat fifteen shallow ones in an interview.

## Postmortem template (use for each scenario)

```markdown
### Scenario: <name>
**Hypothesis:** what I expected to happen
**Injection method:** exact command / config change
**Observed behavior:** what actually happened, with dashboard/trace evidence
**Root cause:** the mechanical reason
**Fix:** the config or code change
**Verification:** how I confirmed the fix worked (repeat the injection)
```

---

## Scenario 1 — Payment latency spike → circuit breaker validation

**Hypothesis:** if `payment-service` slows down, `order-service` should time out per-request rather than piling up threads/connections waiting.

**Injection:** apply the fault-injection `VirtualService` from [04-istio-service-mesh.md](04-istio-service-mesh.md) — 2s fixed delay on 30% of requests, 10% hard aborts.

**Expected observation:** `order-service`'s p99 latency rises but stays bounded near the 3s timeout, not unbounded. Once `payment-service` crosses the `consecutive5xxErrors: 3` threshold from the abort traffic, Envoy's outlier detection ejects it from the load balancing pool for `baseEjectionTime: 30s`, visible in Kiali as the edge to `payment-service` briefly disappearing, and in Grafana as a spike in "circuit breaker ejections."

**Fix if it doesn't behave this way:** usually a missing or too-generous timeout — if `order-service`'s own HTTP client has no timeout, Envoy's timeout gets bypassed by a lower-level connection reuse issue. This is a genuinely common real bug (app-level timeout must be ≥ mesh-level timeout, or the app's own timeout wins and the mesh policy never triggers).

---

## Scenario 2 — Pod kill → rescheduling and load-balancer rerouting

**Hypothesis:** killing one of two `catalog-service` replicas causes zero client-visible errors, just a brief redistribution of load to the surviving pod.

**Injection:** `kubectl delete pod catalog-service-xxxx --grace-period=0 --force` while a K6 load test (see [08-load-testing.md](08-load-testing.md)) is running against it.

**Expected observation:** Kubernetes reschedules a replacement pod immediately (ReplicaSet controller). Envoy on callers stops routing to the dead pod within one health-check interval — the readiness probe is what makes this fast; without a correctly configured `readinessProbe`, Envoy keeps sending requests to a pod that's already gone, and you'd see a real error spike in Grafana instead of a clean handoff. This is worth demonstrating both ways: once with the readiness probe removed (errors visible), once with it correctly configured (clean).

---

## Scenario 3 — MariaDB primary failure → order-service degradation

**Hypothesis:** this is the one that maps most directly to real production experience — a DB primary going away mid-traffic.

**Injection:** kill the `mariadb-0` pod backing `order-service`'s database (or `kubectl cordon` + delete to prevent instant reschedule onto the same node, forcing a real PVC reattach delay).

**Expected observation:** in-flight order creations fail with a 5xx; `order-service`'s readiness probe (if it checks DB connectivity) should flip to not-ready, pulling it out of the mesh's load balancing pool entirely rather than serving errors — this is a materially better failure mode than "serve 500s," and worth implementing deliberately (most tutorials skip DB connectivity checks in readiness probes; doing it here is a differentiator). Once the StatefulSet pod reschedules and reattaches its PVC, connections recover automatically via your DB client's retry/reconnect logic.

**Tie-in:** this is the scenario to narrate using your actual production MariaDB failover runbook — the mechanics (read_only, CHANGE MASTER TO, HAProxy weight-flip) don't apply to a single-instance demo DB, but explicitly stating "in production this is where I'd have a replica and an HAProxy abstraction layer to flip to" turns a toy scenario into a credible extension of real experience.

---

## Scenario 4 — Kafka consumer down → at-least-once delivery replay

**Hypothesis:** if `notification-service` is down for a few minutes, no `order.paid` events are lost — they're replayed from the last committed offset once it comes back.

**Injection:** scale `notification-service` to 0 replicas, place several orders (events pile up in the `order-events` topic, unconsumed), watch Kafka consumer lag climb in Grafana, then scale back to 1+.

**Expected observation:** consumer lag alert fires (from [05-observability-stack.md](05-observability-stack.md)) while it's down. On restart, `notification-service` resumes from its last committed offset and processes the entire backlog — no events lost, some notifications arrive late but all arrive. This is the concrete demonstration of "manual offset commit after processing, not on receipt" actually working under a real outage rather than just being a config line in a doc.

---

## Scenario 5 — Load-induced saturation → HPA response + idempotency under retry storm

**Hypothesis:** under heavy concurrent checkout load, `payment-service` scales out via HPA, and even if `order-service`'s retries fire during the saturation window, no customer gets double-charged.

**Injection:** run the K6 checkout scenario at high VU count (see [08-load-testing.md](08-load-testing.md)) against `payment-service` directly, enough to push CPU utilization past the HPA's 70% threshold.

**Expected observation:** HPA adds replicas (watch `kubectl get hpa -w` and the corresponding Grafana panel). During the saturation window, some `order-service → payment-service` calls will legitimately time out and retry per the `VirtualService` retry policy — verify in Redis (`idempotency:{key}`) that the *same* idempotency key never results in two separate charge records, even though the underlying HTTP call was attempted twice. This is the single best "distributed systems failure mode" story in the whole project, because it's the exact class of bug (two-phase writes across non-transactional systems) you flagged as your generalization gap in SDI prep — you're not reasoning about it abstractly anymore, you watched it not happen because of a specific design decision.
