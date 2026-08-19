# GitOps & Progressive Delivery

## Why GitOps here

Every manifest in this project lives in Git. Argo CD continuously reconciles live cluster state to match Git — a deployment is a merged pull request, not a `kubectl apply` run from someone's laptop. Rollback is `git revert`. This is the difference between "I can write manifests" and "I understand how manifests are actually operated at a company," which is exactly the distinction that came up when you asked what's expected of you in an interview versus what dashboards are for.

## Repo layout

```
gestalt-commerce-gitops/
├── apps/
│   ├── auth-service/
│   │   ├── Chart.yaml
│   │   ├── values.yaml
│   │   └── templates/ (deployment, service, configmap, secret, hpa)
│   ├── catalog-service/
│   ├── cart-service/
│   ├── order-service/
│   ├── payment-service/
│   ├── notification-service/
│   └── review-service/
├── infra/
│   ├── istio/ (PeerAuthentication, AuthorizationPolicy, VirtualService, DestinationRule)
│   ├── data/ (MariaDB, MongoDB, Redis, Kafka StatefulSets)
│   └── observability/ (Prometheus, Grafana, Kiali, Jaeger)
└── root-app.yaml   # the "app of apps"
```

Each service is its own Helm chart — small, but real, and it's what closes the Helm gap flagged against the Navi JD.

## App-of-apps pattern

`root-app.yaml` is the single Argo CD `Application` you actually apply by hand, once. It points at the `apps/` and `infra/` directories, and Argo CD discovers and manages every child `Application` from there — you never run `kubectl apply` against this cluster again after this one bootstrap step.

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: gestalt-commerce-root
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/gestaltsystem/gestalt-commerce-gitops
    targetRevision: main
    path: apps
    directory: { recurse: true }
  destination:
    server: https://kubernetes.default.svc
  syncPolicy:
    automated:
      prune: true      # deleted from Git => deleted from cluster
      selfHeal: true    # manual kubectl edits get reverted back to Git state
    syncOptions: ["CreateNamespace=true"]
```

`selfHeal: true` is worth calling out specifically in an interview — it means if anyone (including you, debugging under pressure) runs a manual `kubectl edit` against a live resource, Argo CD reverts it back to match Git within seconds. That's a deliberate tradeoff: it enforces Git as the single source of truth, at the cost of making live hotfixes impossible without going through Git first.

## Canary automation with Argo Rollouts

The manual weight-shifting in [04-istio-service-mesh.md](04-istio-service-mesh.md) is worth doing by hand once to build intuition. The stretch goal is replacing the `order-service` `Deployment` with a `Rollout` resource and letting Argo Rollouts drive the `VirtualService` weights automatically, gated by a live Prometheus query:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Rollout
metadata:
  name: order-service
  namespace: gestalt-commerce
spec:
  replicas: 4
  strategy:
    canary:
      canaryService: order-service-canary
      stableService: order-service-stable
      trafficRouting:
        istio:
          virtualService: { name: order-service-vs, routes: ["primary"] }
      steps:
        - setWeight: 10
        - pause: { duration: 2m }
        - analysis:
            templates: [{ templateName: success-rate }]
        - setWeight: 50
        - pause: { duration: 2m }
        - setWeight: 100
```

```yaml
apiVersion: argoproj.io/v1alpha1
kind: AnalysisTemplate
metadata:
  name: success-rate
  namespace: gestalt-commerce
spec:
  metrics:
    - name: success-rate
      interval: 30s
      successCondition: result >= 0.95
      provider:
        prometheus:
          address: http://prometheus.observability.svc.cluster.local:9090
          query: |
            sum(rate(istio_requests_total{destination_service_name="order-service",response_code!~"5.."}[2m]))
            /
            sum(rate(istio_requests_total{destination_service_name="order-service"}[2m]))
```

If the success-rate query drops below 95% during the 10% step, the rollout automatically halts and can be configured to auto-rollback — this is the automated version of exactly the "catch a bug at 10% traffic before it hits everyone" story from your earlier canary discussion, but with a human no longer required to be watching the dashboard at the right moment.
