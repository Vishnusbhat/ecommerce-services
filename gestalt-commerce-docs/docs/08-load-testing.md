# Load Testing (K6)

K6 is the right tool here specifically because it integrates natively with the Grafana stack you're already building — the load test's own metrics can flow into the same dashboards as your service telemetry, so you can watch synthetic load and real service behavior on one screen.

## Checkout flow scenario

```javascript
import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE_URL = 'https://gestalt-commerce.local';

export const options = {
  stages: [
    { duration: '1m', target: 20 },   // ramp up
    { duration: '3m', target: 20 },   // steady state
    { duration: '1m', target: 100 },  // spike — this is what pushes payment-service into HPA/circuit-breaker territory
    { duration: '2m', target: 100 },
    { duration: '1m', target: 0 },    // ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<800'],   // checkout should stay under 800ms at p95 even under load
    http_req_failed: ['rate<0.02'],     // less than 2% hard failures
  },
};

export default function () {
  // 1. login
  const loginRes = http.post(`${BASE_URL}/auth/login`, JSON.stringify({
    email: 'loadtest@example.com', password: 'test1234'
  }), { headers: { 'Content-Type': 'application/json' } });
  check(loginRes, { 'login succeeded': (r) => r.status === 200 });
  const token = loginRes.json('accessToken');
  const authHeaders = { headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' } };

  // 2. browse catalog
  const catalogRes = http.get(`${BASE_URL}/catalog/products?limit=10`);
  check(catalogRes, { 'catalog loaded': (r) => r.status === 200 });
  const productId = catalogRes.json('items.0.id');

  // 3. add to cart
  http.post(`${BASE_URL}/cart/items`, JSON.stringify({ productId, quantity: 1 }), authHeaders);

  // 4. checkout
  const idemKey = `${__VU}-${__ITER}-${Date.now()}`;
  const orderRes = http.post(`${BASE_URL}/orders`, JSON.stringify({}), {
    headers: { ...authHeaders.headers, 'Idempotency-Key': idemKey },
  });
  check(orderRes, { 'order created': (r) => r.status === 201 || r.status === 200 });

  sleep(1);
}
```

## What to watch during the run

Run this with three dashboards open side by side:

1. **Grafana golden-signals dashboard** — watch p95/p99 latency and error rate climb during the spike stage. Correlate the exact timestamp of any error-rate jump with the K6 stage transition.
2. **Kiali service graph** — watch edge thickness grow on `order-service → payment-service` and `order-service → catalog-service` during the spike, and watch for red edges if error rates rise.
3. **`kubectl get hpa -w`** or the HPA panel in Grafana — confirm `payment-service` actually scales out during the spike stage, not just after it (a delayed scale-out is itself a useful thing to observe and explain — HPA reacts to a rolling average, not an instant value).

## Suggested test sequence

Run the load test three times, each time changing one variable, and document what changed:

1. **Baseline** — no chaos, no fault injection, HPA enabled. Should stay well within thresholds.
2. **With fault injection active** — apply the payment-service fault-injection `VirtualService` from [04-istio-service-mesh.md](04-istio-service-mesh.md) during the spike stage. Watch the circuit breaker actually open under combined load + injected failures, not just injected failures alone — this is a materially harder scenario than either one individually.
3. **With `payment-service` pinned to 1 replica** (HPA `maxReplicas: 1`) — deliberately remove the safety net and watch it degrade badly. This is the "show, don't tell" version of explaining why HPA matters — a before/after pair of Grafana screenshots is a strong artifact.
