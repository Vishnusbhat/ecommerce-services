# Observability Stack

## Metrics: Prometheus

Two metric sources feed Prometheus, and it's worth being able to name both distinctly:

1. **Istio/Envoy telemetry** — every sidecar exposes `/stats/prometheus` automatically. This gives you golden signals (request rate, error rate, duration, saturation) for every service, for free, with zero app-level instrumentation.
2. **Application-level custom metrics** — business metrics Envoy can't know about: `orders_created_total`, `payment_failures_total{reason=...}`, `cart_abandonment_total`. Each service exposes these on its own `/metrics` endpoint (via a Prometheus client library), scraped the same way as any exporter — same pull model as `node_exporter`, just app-authored instead of node-level.

`ServiceMonitor` (if using the Prometheus Operator) for one service:

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: order-service
  namespace: observability
spec:
  selector:
    matchLabels: { app: order-service }
  namespaceSelector:
    matchNames: ["gestalt-commerce"]
  endpoints:
    - port: http-metrics
      path: /metrics
      interval: 15s
```

## Dashboards: Grafana

Build these yourself rather than importing templates — a self-built dashboard is a much stronger interview artifact than "I imported dashboard #7639."

| Dashboard | Panels |
|---|---|
| **Golden signals (per service)** | Request rate, p50/p95/p99 latency, error rate (4xx/5xx split), in-flight requests — one row per service, all 7 stacked |
| **Mesh health** | mTLS handshake failures, circuit breaker ejections (from `outlierDetection`), retry counts |
| **Business dashboard** | Orders/min, revenue/hour, cart-to-order conversion rate, payment failure rate by reason, checkout saga failure rate (stock-reservation failures vs payment failures, split out — this distinction is exactly the kind of thing a good postmortem calls out) |
| **Data layer** | MariaDB connections in use vs pool size, Redis hit/miss ratio on catalog cache, Kafka consumer lag per topic/partition |

Kafka consumer lag specifically is worth a dedicated panel — it's your leading indicator that `notification-service` or `review-service` is falling behind or down, before anyone files a support ticket.

## Service graph: Kiali

Point Kiali at the same Prometheus instance — it needs no separate instrumentation since it's purely a visualization layer over Envoy's existing metrics. During a canary rollout, Kiali's graph is where you visually watch traffic shift from the `v1` box to the `v2` box in near real time, edge thickness and color scaled to request volume and error rate.

## Distributed tracing: Jaeger

Istio auto-generates trace spans at the Envoy layer for every hop, but there's a nuance worth knowing cold: **Envoy can't stitch spans across an application boundary on its own.** Each service's application code must forward the incoming trace headers (`x-request-id`, `x-b3-traceid`, `x-b3-spanid`, `x-b3-parentspanid`, `x-b3-sampled`) on any outbound call it makes. Miss this in one service and your trace has a gap exactly at that hop — this is a real, common mistake and a good thing to demonstrate you understand by deliberately breaking it once, screenshotting the broken trace, then fixing it.

With header propagation correct, a single checkout request traced end-to-end looks like:

```
gateway (2ms) → order-service (180ms)
                  ├─ catalog-service /stock/reserve (15ms)
                  └─ payment-service /charge (150ms)  ← the bottleneck, visible instantly
```

This is the single most useful artifact for the "how would you debug a slow checkout in production" interview question — you point at the trace, not at logs.

## Alerting

Prometheus Alertmanager routes to Slack — reuse your existing Slack alerting bot pattern from Dealshare rather than building a new integration from scratch.

Minimum alert set:

| Alert | Condition | Severity |
|---|---|---|
| High payment failure rate | `payment_failures_total` rate > 5% over 5m | page |
| Order saga stuck | orders in `PENDING` state > 60s | page |
| Circuit breaker open | Envoy outlier ejection active on any service > 2m | warn |
| Kafka consumer lag | lag > 1000 messages on `order-events` | warn |
| Pod restart loop | any pod restarted > 3 times in 10m | page |

Each of these should be validated by actually triggering it once — an alert rule you've never seen fire is not a tested alert rule. This is exactly what [06-resilience-and-chaos-engineering.md](06-resilience-and-chaos-engineering.md) is for.
