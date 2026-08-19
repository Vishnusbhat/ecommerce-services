# auth-service

## Responsibility
User registration, login, JWT issuance, refresh-token rotation, and token revocation. Publishes its public keys (JWKS) so Envoy can validate tokens at the mesh edge without calling this service per-request — see [../docs/04-istio-service-mesh.md](../docs/04-istio-service-mesh.md).

## Tech stack
- Language: your choice (Python/FastAPI is a reasonable default given your existing FastAPI experience)
- Datastore: MariaDB (users, hashed passwords), Redis (refresh-token blacklist, TTL-matched to token expiry)
- JWT signing: RS256 (asymmetric — public key is safe to publish via JWKS; HS256 would require sharing a shared secret with every verifier, which defeats the point of edge validation)

## API surface
See [../docs/02-api-contracts.md](../docs/02-api-contracts.md#auth-service) for full endpoint table.

## Data model (MariaDB)
```sql
CREATE TABLE users (
  id CHAR(36) PRIMARY KEY,
  email VARCHAR(255) UNIQUE NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE refresh_tokens (
  token_id CHAR(36) PRIMARY KEY,
  user_id CHAR(36) NOT NULL,
  expires_at TIMESTAMP NOT NULL,
  revoked BOOLEAN DEFAULT FALSE,
  FOREIGN KEY (user_id) REFERENCES users(id)
);
```

## Dependencies
- **Called by:** Istio ingress gateway (login/register/refresh/logout), Envoy `RequestAuthentication` (JWKS endpoint, at the mesh level — not a service-to-service call in the usual sense)
- **Calls:** none — this is a leaf service with no outbound dependencies, which is deliberate: auth should never be blocked by another service's availability

## Events
None produced or consumed. Auth is entirely synchronous by design — token issuance can't be eventually consistent.

## Failure modes owned
- Password hash timing attacks — use a constant-time comparison (bcrypt/argon2 handle this natively)
- Refresh-token replay after logout — the Redis blacklist check must happen before the DB lookup, not after, or there's a window where a revoked token still validates
- JWKS endpoint availability — if this goes down, **no new logins fail, but Envoy can still validate existing tokens** against its cached JWKS (Envoy caches per `jwt_cache_config`), so there's graceful degradation built in. Worth verifying this explicitly by killing auth-service and confirming already-issued tokens still work at the edge.

## Resource footprint (suggested)
`requests: { cpu: 50m, memory: 64Mi }`, `limits: { cpu: 200m, memory: 128Mi }` — lightweight, low-traffic relative to the checkout path.
