# Kubernetes Deployment

This doc deliberately covers the exact areas flagged as deferred/gap in your K8s prep: ConfigMap/Secret, NetworkPolicy, plus HPA and StatefulSet usage. Building this project is how those stop being deferred.

## Namespace layout

| Namespace | Contents | Istio injection |
|---|---|---|
| `istio-system` | istiod, ingress gateway | n/a |
| `gestalt-commerce` | all 7 business services | enabled (`istio-injection: enabled`) |
| `gestalt-data` | MariaDB, MongoDB, Redis, Kafka | enabled (so mTLS covers data-layer traffic too) |
| `observability` | Prometheus, Grafana, Kiali, Jaeger | enabled |
| `argocd` | Argo CD control plane | disabled (control plane doesn't need mesh) |

Separating `gestalt-data` from `gestalt-commerce` lets you write NetworkPolicy and AuthorizationPolicy rules that are easy to reason about: "only the owning service can reach its database," expressed once per namespace boundary rather than per pod.

## Deployment strategy per service

All 7 business services follow the same shape — stateless `Deployment`, 2 replicas minimum (so you can actually observe load balancing and rolling updates), `RollingUpdate` strategy, readiness + liveness probes.

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: order-service
  namespace: gestalt-commerce
spec:
  replicas: 2
  strategy:
    rollingUpdate:
      maxUnavailable: 0
      maxSurge: 1
  selector:
    matchLabels: { app: order-service }
  template:
    metadata:
      labels: { app: order-service, version: v1 }
    spec:
      containers:
        - name: order-service
          image: gestaltsystem/order-service:v1
          ports: [{ containerPort: 8080 }]
          resources:
            requests: { cpu: "100m", memory: "128Mi" }
            limits: { cpu: "500m", memory: "256Mi" }
          readinessProbe:
            httpGet: { path: /healthz/ready, port: 8080 }
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            httpGet: { path: /healthz/live, port: 8080 }
            initialDelaySeconds: 10
            periodSeconds: 15
          envFrom:
            - configMapRef: { name: order-service-config }
            - secretRef: { name: order-service-secrets }
```

The `version: v1` label on the pod template is not decorative — it's what `DestinationRule` subsets key off during canary rollouts (see [04-istio-service-mesh.md](04-istio-service-mesh.md)).

## ConfigMap and Secret usage

**ConfigMap** — non-sensitive, environment-specific config: log level, Kafka broker address, feature flags, timeout values.

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: order-service-config
  namespace: gestalt-commerce
data:
  LOG_LEVEL: "info"
  KAFKA_BROKERS: "kafka.gestalt-data.svc.cluster.local:9092"
  PAYMENT_TIMEOUT_MS: "3000"
```

**Secret** — DB credentials, JWT signing keys. For local dev, plain Kubernetes `Secret` (base64, not encrypted at rest by default — call this out explicitly, don't pretend otherwise). For the EKS stage, the stretch goal is to mount secrets from AWS Secrets Manager via the Secrets Store CSI Driver instead of native `Secret` objects — worth doing once, since "I know native Secrets aren't actually encrypted and here's how I'd fix that" is a strong answer to a direct interview question.

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: order-service-secrets
  namespace: gestalt-commerce
type: Opaque
stringData:
  DB_PASSWORD: "changeme-in-real-deployment"
  JWT_PUBLIC_KEY_URL: "http://auth-service.gestalt-commerce.svc.cluster.local/auth/.well-known/jwks.json"
```

## StatefulSets (data layer)

MariaDB, MongoDB, and Kafka run as `StatefulSet`s in `gestalt-data`, each with a `PersistentVolumeClaim` template so storage survives pod rescheduling and each replica gets a stable network identity (`mariadb-0`, `mariadb-1`, ...).

```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: mariadb
  namespace: gestalt-data
spec:
  serviceName: mariadb
  replicas: 1   # single instance for local/demo; document the 3-node replication topology from your production runbook as the "how I'd do this for real" extension
  selector:
    matchLabels: { app: mariadb }
  template:
    metadata:
      labels: { app: mariadb }
    spec:
      containers:
        - name: mariadb
          image: mariadb:11
          ports: [{ containerPort: 3306 }]
          volumeMounts:
            - name: data
              mountPath: /var/lib/mysql
  volumeClaimTemplates:
    - metadata: { name: data }
      spec:
        accessModes: ["ReadWriteOnce"]
        resources: { requests: { storage: "5Gi" } }
```

Redis for cart-service and payment-service idempotency keys runs as a plain `Deployment` (single replica is fine — data loss there is acceptable, cart TTLs and idempotency windows are short-lived by design).

## NetworkPolicy (default-deny baseline)

This is the other flagged gap. Apply a default-deny policy per namespace first, then explicit allows — this ordering matters for the story: "I designed this deny-first, not allow-first."

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-all
  namespace: gestalt-commerce
spec:
  podSelector: {}
  policyTypes: ["Ingress", "Egress"]
```

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-gateway-to-order
  namespace: gestalt-commerce
spec:
  podSelector:
    matchLabels: { app: order-service }
  policyTypes: ["Ingress"]
  ingress:
    - from:
        - namespaceSelector:
            matchLabels: { kubernetes.io/metadata.name: istio-system }
      ports: [{ protocol: TCP, port: 8080 }]
```

Note the layering worth being explicit about in an interview: **NetworkPolicy operates at L3/L4** (IP + port, pod selector) and is enforced by the CNI. **Istio's AuthorizationPolicy operates at L7** with cryptographic service identity, enforced by Envoy. Running both isn't redundant — NetworkPolicy is your defense-in-depth if the mesh sidecar itself is ever bypassed or misconfigured.

## HorizontalPodAutoscaler

Applied to `payment-service` and `catalog-service` specifically, since those are the ones you'll drive into saturation during the K6 load test in [08-load-testing.md](08-load-testing.md).

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: payment-service-hpa
  namespace: gestalt-commerce
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: payment-service
  minReplicas: 2
  maxReplicas: 6
  metrics:
    - type: Resource
      resource:
        name: cpu
        target: { type: Utilization, averageUtilization: 70 }
```
