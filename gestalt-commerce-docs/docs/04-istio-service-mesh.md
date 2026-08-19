# Istio Service Mesh Configuration

## mTLS — mesh-wide, strict

```yaml
apiVersion: security.istio.io/v1
kind: PeerAuthentication
metadata:
  name: default
  namespace: gestalt-commerce
spec:
  mtls:
    mode: STRICT
```

Applied at the namespace level so it covers every service without per-service config. Any plaintext connection attempt into a pod in this namespace is rejected outright — including from a compromised pod elsewhere in the cluster that isn't part of the mesh's trust domain.

## JWT validation at the edge (RequestAuthentication)

Rather than every service independently verifying JWTs, Envoy at the ingress gateway validates the signature once, using `auth-service`'s published JWKS endpoint. This is a deliberate mesh-offload decision — application code never sees an invalid token.

```yaml
apiVersion: security.istio.io/v1
kind: RequestAuthentication
metadata:
  name: jwt-auth
  namespace: istio-system
spec:
  selector:
    matchLabels: { istio: ingressgateway }
  jwtRules:
    - issuer: "gestalt-commerce-auth"
      jwksUri: "http://auth-service.gestalt-commerce.svc.cluster.local/auth/.well-known/jwks.json"
```

`RequestAuthentication` alone only *validates* a token if present — it doesn't *require* one. Requiring it is a separate `AuthorizationPolicy`:

```yaml
apiVersion: security.istio.io/v1
kind: AuthorizationPolicy
metadata:
  name: require-jwt
  namespace: istio-system
spec:
  selector:
    matchLabels: { istio: ingressgateway }
  action: ALLOW
  rules:
    - from:
        - source: { requestPrincipals: ["gestalt-commerce-auth/*"] }
      to:
        - operation: { notPaths: ["/auth/login", "/auth/register", "/catalog/products*"] }
```
This allows unauthenticated access to login/register/public catalog browsing, and requires a valid JWT principal for everything else — a realistic mixed-auth surface, not an all-or-nothing gate.

## AuthorizationPolicy matrix (service-to-service)

| Caller identity | Allowed callee | Denied by default |
|---|---|---|
| istio-ingressgateway | auth, catalog, cart, order, review | payment, notification (never called directly by clients) |
| cart-service | catalog-service (read-only price/stock) | payment, order, auth |
| order-service | catalog-service (reserve/release), payment-service (charge) | auth, cart, review |
| notification-service | *(none — only consumes Kafka)* | every service |
| review-service | *(none — only consumes Kafka + serves reads)* | every service |

Example enforcing the tightest rule in that table — only `order-service` may call `payment-service`:

```yaml
apiVersion: security.istio.io/v1
kind: AuthorizationPolicy
metadata:
  name: payment-service-authz
  namespace: gestalt-commerce
spec:
  selector:
    matchLabels: { app: payment-service }
  action: ALLOW
  rules:
    - from:
        - source:
            principals: ["cluster.local/ns/gestalt-commerce/sa/order-service"]
      to:
        - operation: { methods: ["POST"], paths: ["/payments/charge"] }
```

This is the concrete answer to "can a compromised pod use its own valid certificate to attack another service" — it can authenticate as itself, but `payment-service`'s AuthorizationPolicy only accepts requests whose principal is literally `order-service`'s service account identity, so a compromised `notification-service` pod, even with a completely valid certificate for itself, gets a 403.

## Resilience: timeouts, retries, circuit breaking

`DestinationRule` for `payment-service` — outlier detection acts as the circuit breaker:

```yaml
apiVersion: networking.istio.io/v1
kind: DestinationRule
metadata:
  name: payment-service-dr
  namespace: gestalt-commerce
spec:
  host: payment-service.gestalt-commerce.svc.cluster.local
  trafficPolicy:
    connectionPool:
      tcp: { maxConnections: 50 }
      http: { http1MaxPendingRequests: 20, maxRequestsPerConnection: 10 }
    outlierDetection:
      consecutive5xxErrors: 3
      interval: 10s
      baseEjectionTime: 30s
      maxEjectionPercent: 50
```

`VirtualService` for the `order-service → payment-service` call — timeout and retry budget:

```yaml
apiVersion: networking.istio.io/v1
kind: VirtualService
metadata:
  name: payment-service-vs
  namespace: gestalt-commerce
spec:
  hosts: ["payment-service.gestalt-commerce.svc.cluster.local"]
  http:
    - timeout: 3s
      retries:
        attempts: 2
        perTryTimeout: 1s
        retryOn: "5xx,reset,connect-failure"
      route:
        - destination: { host: payment-service.gestalt-commerce.svc.cluster.local }
```

Worth being precise about in an interview: retries here are safe specifically *because* `payment-service` is idempotent on the `Idempotency-Key` header — retrying a non-idempotent charge endpoint would be a real bug, not a resilience feature.

## Fault injection (chaos testing via the mesh)

```yaml
apiVersion: networking.istio.io/v1
kind: VirtualService
metadata:
  name: payment-service-fault-injection
  namespace: gestalt-commerce
spec:
  hosts: ["payment-service.gestalt-commerce.svc.cluster.local"]
  http:
    - fault:
        delay: { percentage: { value: 30 }, fixedDelay: 2s }
        abort: { percentage: { value: 10 }, httpStatus: 503 }
      route:
        - destination: { host: payment-service.gestalt-commerce.svc.cluster.local }
```

This is applied temporarily, during a chaos exercise, then removed — see [06-resilience-and-chaos-engineering.md](06-resilience-and-chaos-engineering.md) for the full scenario writeups this feeds into.

## Canary release (traffic shifting)

`DestinationRule` subsets by pod label, `VirtualService` controls the split — pod count and traffic percentage are fully decoupled, exactly as discussed:

```yaml
apiVersion: networking.istio.io/v1
kind: DestinationRule
metadata:
  name: order-service-dr
  namespace: gestalt-commerce
spec:
  host: order-service.gestalt-commerce.svc.cluster.local
  subsets:
    - name: v1
      labels: { version: v1 }
    - name: v2
      labels: { version: v2 }
---
apiVersion: networking.istio.io/v1
kind: VirtualService
metadata:
  name: order-service-vs
  namespace: gestalt-commerce
spec:
  hosts: ["order-service.gestalt-commerce.svc.cluster.local"]
  http:
    - route:
        - destination: { host: order-service.gestalt-commerce.svc.cluster.local, subset: v1 }
          weight: 90
        - destination: { host: order-service.gestalt-commerce.svc.cluster.local, subset: v2 }
          weight: 10
```

Ramp this 10 → 25 → 50 → 100 manually the first time (to build the muscle memory of watching Kiali/Grafana during each step), then automate it with Argo Rollouts — see [07-gitops-and-progressive-delivery.md](07-gitops-and-progressive-delivery.md).
