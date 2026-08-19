# Build Roadmap

Sequenced to match where you already are: comfortable with Docker Desktop Kubernetes, minikube DNAT tracing done, ConfigMap/Secret and NetworkPolicy still owed. Each week ends with something independently demoable — you don't need to finish the whole roadmap to have a strong story for an imminent interview.

## Week 1 — Core services on local Kubernetes, no mesh yet

- Build `auth-service`, `catalog-service`, `order-service`, `payment-service` (the four services on the critical checkout path). Use whatever language/framework you're fastest in — this project is about infra, not app code quality.
- Deploy as plain `Deployment` + `Service` on Docker Desktop Kubernetes, no Istio yet.
- Wire ConfigMap + Secret per [03-kubernetes-deployment.md](03-kubernetes-deployment.md) — this alone closes your deferred K8s gap.
- **Demo checkpoint:** checkout flow works end-to-end via plain `kubectl port-forward`, no mesh.

## Week 2 — Remaining services, data layer, NetworkPolicy

- Add `cart-service`, `notification-service`, `review-service`. Stand up MariaDB, Redis, MongoDB, Kafka as StatefulSets/Deployments in `gestalt-data`.
- Apply default-deny NetworkPolicy + explicit allows — closes the second deferred gap.
- **Demo checkpoint:** full 7-service system, async notification flow working via Kafka, still no mesh.

## Week 3 — Istio mesh: mTLS, JWT-at-edge, AuthorizationPolicy

- Install Istio, enable injection on `gestalt-commerce` and `gestalt-data`.
- Apply strict `PeerAuthentication`, `RequestAuthentication` for JWT-at-edge, and the full `AuthorizationPolicy` matrix from [04-istio-service-mesh.md](04-istio-service-mesh.md).
- **Demo checkpoint:** show a denied call (e.g. `notification-service` trying to hit `payment-service` directly) getting a 403, and a legitimate call succeeding — this is the single most interview-relevant screenshot in the whole project.

## Week 4 — Observability stack

- Prometheus + Grafana + Kiali + Jaeger, all wired to real traffic.
- Build the four dashboards from [05-observability-stack.md](05-observability-stack.md) yourself, don't import templates.
- Fix trace header propagation across at least the checkout path.
- **Demo checkpoint:** a single checkout request traced end to end in Jaeger, and the same request visible as an edge lighting up in Kiali.

## Week 5 — Resilience: circuit breakers, retries, chaos scenarios 1–3

- Apply `DestinationRule` outlier detection and `VirtualService` retry/timeout policy.
- Run chaos scenarios 1 (payment latency), 2 (pod kill), 3 (MariaDB failure) from [06-resilience-and-chaos-engineering.md](06-resilience-and-chaos-engineering.md). Write postmortems for each.
- **Demo checkpoint:** three postmortem documents with before/after dashboard evidence.

## Week 6 — EKS migration via Terraform

- Provision EKS (VPC, node groups, IAM/OIDC) via Terraform — reuse your existing Terraform familiarity.
- Redeploy everything via Argo CD instead of manual `kubectl apply` (see [07-gitops-and-progressive-delivery.md](07-gitops-and-progressive-delivery.md)).
- **Demo checkpoint:** a deployment triggered purely by a merged PR, watched live in the Argo CD UI.

## Week 7 — Load testing, HPA, chaos scenarios 4–5

- Run the K6 checkout scenario from [08-load-testing.md](08-load-testing.md), confirm HPA response.
- Run chaos scenarios 4 (Kafka consumer down) and 5 (saturation + idempotency under retry storm).
- **Demo checkpoint:** the idempotency-under-load postmortem — this is the strongest single artifact in the project for demonstrating distributed-systems reasoning.

## Week 8 — Canary rollout + polish

- Execute one manual canary rollout of `order-service` v2, ramped 10→25→50→100, screenshotted at each step in Kiali/Grafana.
- Optional stretch: automate it with Argo Rollouts + the Prometheus analysis gate.
- Final pass on all `.md` docs — make sure every diagram and config snippet matches what's actually deployed, not what was originally planned. A doc that's slightly wrong is worse than a shorter doc that's accurate.

## What to prioritize if time is short before an interview

If Navi's rounds start before you reach week 8, prioritize in this order: **Week 3 (mesh + AuthorizationPolicy) > Week 5 (circuit breakers + first chaos postmortem) > Week 4 (observability) > everything else.** The mesh security story and one well-written chaos postmortem, even without EKS or canary automation, is enough to carry a strong "tell me about a project" answer.
