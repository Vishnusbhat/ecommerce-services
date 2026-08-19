# Authorization baseline — before Istio

Snapshot of exactly what's enforced today by the two app-level mesh
substitutions (`gestalt_shared/security.py`'s per-request JWT verification
and `gestalt_shared/internal_auth.py`'s shared-token internal-caller
scheme — see `PROJECT_STATUS.md` §5), taken **before** either is replaced
by real Istio primitives. Written in the same shape as
`gestalt-commerce-docs/docs/04-istio-service-mesh.md`'s
`RequestAuthentication`/`AuthorizationPolicy` design, specifically so each
row here has a 1:1 target to write and verify against once the mesh goes
in — "this app-level rule → this Istio resource → confirm the behavior in
this doc still holds" — rather than trusting that the migration preserved
behavior.

**How to use this doc during the migration:** for every row below, write
the corresponding Istio resource, then re-run the exact check described
(most of these already have a passing `pytest` case under `services/*/tests/`
— reuse it against the mesh instead of writing a new one). Only delete the
app-level enforcement (JWT check in `gestalt_shared.security`, or an
`internal_auth` dependency) once its replacement is confirmed to produce
the *same or stricter* result, not before.

---

## 1. Client-facing JWT requirements (→ `RequestAuthentication` + ingress `AuthorizationPolicy`)

What each service's own JWT verification enforces today, per endpoint —
this is the target for the ingress-gateway `RequestAuthentication`
(`jwksUri` → auth-service) plus an `AuthorizationPolicy` on
`istio-ingressgateway` with the equivalent `notPaths` exclusion list from
`docs/04`.

| Endpoint | Auth today | Enforced by |
|---|---|---|
| `POST /auth/register` | none | — |
| `POST /auth/login` | none | — |
| `POST /auth/refresh` | none (refresh token in body is the credential) | — |
| `POST /auth/logout` | JWT required | `auth-service`'s own local `_current_user_id` (verifies against `key_material.public_key` directly, in-process — *not* the shared `gestalt_shared.security` JWKS-client dependency every other service uses, since auth-service holds the private key itself and has no need to round-trip to its own JWKS endpoint) |
| `GET /auth/.well-known/jwks.json` | none | — (public by design; this **is** the JWKS the mesh's `RequestAuthentication` will point at) |
| `GET /catalog/products` | none | — |
| `GET /catalog/products/{id}` | none | — |
| `GET /cart` | JWT required | `gestalt_shared.security.current_user_dependency` (JWKS-verified) |
| `POST /cart/items` | JWT required | same |
| `DELETE /cart/items/{id}` | JWT required | same |
| `POST /cart/checkout-intent` | JWT required | same |
| `POST /orders` | JWT required | same |
| `GET /orders/{id}` | JWT required, **and** ownership-checked (`order.user_id == token.user_id`, else `404`) | same, plus an app-level ownership check that has no Istio equivalent — see §4 |
| `GET /orders` | JWT required (implicitly scoped to the caller via `user_id` in the query) | same |
| `POST /reviews` | JWT required | same |
| `GET /reviews/product/{id}` | none | — |

Everything **not** listed here (catalog's `/price`/`/stock/reserve`/`/stock/release`,
payment's `/charge`/`/{key}`, cart's `/items:batch`) never accepts or checks
an end-user JWT at all, in either the current implementation or the target
mesh design — those are pure service-to-service calls, gated by §2 instead.

Target `RequestAuthentication` + `AuthorizationPolicy` `notPaths` list
(mirrors the "JWT-required except these" shape already in `docs/04`):
`/auth/login`, `/auth/register`, `/auth/.well-known/jwks.json`,
`/catalog/products*`, `/reviews/product/*`.

---

## 2. Service-to-service internal-caller matrix (→ `AuthorizationPolicy` per callee)

Same shape as `docs/04`'s table, but at **endpoint** granularity (the real
matrix is coarser — service-level — since mTLS identity is per-workload,
not per-endpoint; each row below is the endpoint-level detail that a single
service-level `AuthorizationPolicy` with per-operation `rules` will need to
reproduce).

| Callee endpoint | Allowed caller(s) today | Enforced by |
|---|---|---|
| `GET /catalog/products/{id}/price` | `cart-service`, `order-service` | `internal_auth.make_internal_caller_dependency(allowed_callers=["cart-service","order-service"])` in `catalog-service/app/routers/catalog.py` (`require_price_reader`) |
| `POST /catalog/stock/reserve` | `order-service` only | same file, `require_order_service` |
| `POST /catalog/stock/release` | `order-service` only | same |
| `POST /payments/charge` | `order-service` only | `payment-service/app/routers/payments.py`, `require_order_service` |
| `GET /payments/{idempotencyKey}` | `order-service` only | same |
| `DELETE /cart/items:batch` | `order-service` only | `cart-service/app/routers/cart.py`, `require_order_service` (plus a required `X-User-Id` header — see §3) |

Collapsed to service-level, to compare directly against `docs/04`'s table:

| Caller identity | Allowed callee | Denied by default |
|---|---|---|
| cart-service | catalog-service (`/price` only) | payment, order, auth, cart's own `/items:batch` |
| order-service | catalog-service (`/price`, `/stock/reserve`, `/stock/release`), payment-service (`/charge`, `/{key}`), cart-service (`/items:batch`) | auth |
| notification-service | *(none — only consumes Kafka)* | every service |
| review-service | *(none — only consumes Kafka + serves reads)* | every service |
| auth-service | *(none — leaf service)* | every service |

This matches `docs/04`'s table exactly for cart-service, order-service,
notification-service, and review-service. **Difference from `docs/04`**:
the ingress-gateway row doesn't apply here (there's no ingress gateway
locally — see §1 instead), and order-service's real-world callee set now
additionally includes cart-service's `/items:batch` (the cart-clearing
call added after `docs/04` was written — not in the original doc's matrix,
worth adding to the real `AuthorizationPolicy` when it's written).

---

## 3. What does *not* map 1:1 — do not assume equivalence

- **Identity is a shared secret, not cryptographic.** Every internal caller
  presents the *same* static `X-Internal-Token` value (one shared secret
  across all 7 services, from `.env`'s `INTERNAL_SERVICE_TOKEN`) plus a
  **self-declared** `X-Internal-Caller` header — e.g. notification-service,
  if it wanted to, could set `X-Internal-Caller: order-service` and pass
  every check in §2, because nothing cryptographically ties the caller's
  actual identity to that header. Istio's `AuthorizationPolicy` principals
  (`cluster.local/ns/gestalt-commerce/sa/order-service`) are derived from
  the mTLS certificate itself — a compromised notification-service pod
  *cannot* forge that. This is the single most important thing this
  baseline does **not** cover: it enforces the correct authorization
  *shape*, not an equivalent *security* guarantee. Say so explicitly if
  this doc is ever used to claim "authorization was already covered."
- **`GET /orders/{id}`'s ownership check has no mesh equivalent.**
  `AuthorizationPolicy` can gate *which service* calls an endpoint, not
  *which end-user's data* a request is allowed to touch — that ownership
  check (`order.user_id == token.user_id` → `404` otherwise) stays
  application code indefinitely, mesh or not. Don't expect an Istio
  resource to replace `order-service/app/routers/orders.py`'s `get_order`.
- **`order-service → cart-service GET /cart` is the one exception to the
  whole internal-caller scheme.** It forwards the *end user's own* JWT
  (`Authorization` header, unmodified) rather than using
  `X-Internal-Token`/`X-Internal-Caller` — see
  `order-service/app/clients.py`'s `get_cart_items`. In the mesh design
  this becomes moot: it's just another authenticated call inside the trust
  domain, but call out that it's JWT-forwarded, not internal-caller
  -authenticated, if the migration checklist assumes every order-service
  outbound call uses the same pattern (it doesn't, today).
- **Health/metrics endpoints (`/healthz/live`, `/healthz/ready`, `/metrics`)
  are unauthenticated everywhere**, today and in the target design alike
  (kubelet probes and Prometheus scraping need direct, unauthenticated
  access) — not part of either matrix above, and no `AuthorizationPolicy`
  row is expected for them.

---

## 4. Verification checklist once Istio resources exist

For each row in §1 and §2, in order:

1. Apply the `RequestAuthentication`/`AuthorizationPolicy` resource.
2. Re-run the matching existing test (`services/*/tests/`) against the
   mesh-fronted endpoint instead of the direct host port, unmodified where
   possible.
3. Specifically re-verify the two denial cases already covered by
   `services/catalog-service/tests/test_catalog.py` (`403` on missing
   caller, `403` on wrong caller for `/stock/reserve`) — under the mesh
   these should become **mTLS-identity-based** denials, not
   header-based ones; confirm a request presenting a *valid but wrong*
   service identity is still denied (the exact "compromised
   notification-service can't call payment-service" scenario
   `docs/04-istio-service-mesh.md` calls out as the key interview story).
4. Only then remove the corresponding `internal_auth`/JWT dependency from
   the app code — not before, and not speculatively for endpoints whose
   `AuthorizationPolicy` hasn't been verified yet.
