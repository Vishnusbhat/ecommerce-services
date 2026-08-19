"""JWT issuance (auth-service only) and verification (every protected service).

docs/04-istio-service-mesh.md has Envoy validate the JWT once at the ingress
gateway via RequestAuthentication + auth-service's JWKS endpoint, so app code
"never sees an invalid token." There is no Envoy in local docker-compose, so
each protected service does the same JWKS-based RS256 verification itself via
`get_current_user` below. When the Istio phase of this project is built, this
becomes redundant-but-harmless defense in depth rather than a rewrite.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import httpx
import jwt
from fastapi import Header
from jwt.algorithms import RSAAlgorithm

from gestalt_shared.errors import AppError

ISSUER = "gestalt-commerce-auth"
ALGORITHM = "RS256"


@dataclass
class TokenClaims:
    user_id: str
    email: str
    raw: dict


class JWKSClient:
    """Fetches and caches auth-service's JWKS, keyed by `kid`.

    auth-service.md calls out that a dead auth-service should not break
    verification of already-issued tokens (Envoy caches JWKS at the edge).
    Mirroring that here: keys are cached for `ttl_seconds` and a stale cache
    is served if a refresh attempt fails, instead of hard-failing every
    request the moment auth-service is unreachable.
    """

    def __init__(self, jwks_url: str, ttl_seconds: int = 300):
        self.jwks_url = jwks_url
        self.ttl_seconds = ttl_seconds
        self._keys: dict[str, object] = {}
        self._fetched_at: float = 0.0

    def _refresh(self) -> None:
        try:
            resp = httpx.get(self.jwks_url, timeout=3.0)
            resp.raise_for_status()
            jwks = resp.json()
            self._keys = {
                key["kid"]: RSAAlgorithm.from_jwk(key) for key in jwks.get("keys", [])
            }
            self._fetched_at = time.monotonic()
        except Exception:
            if not self._keys:
                raise

    def get_key(self, kid: str):
        stale = (time.monotonic() - self._fetched_at) > self.ttl_seconds
        if stale or kid not in self._keys:
            self._refresh()
        return self._keys.get(kid)


def decode_token(token: str, jwks_client: JWKSClient) -> TokenClaims:
    try:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        key = jwks_client.get_key(kid) if kid else None
        if key is None:
            raise AppError("INVALID_TOKEN", "Unknown signing key", 401)
        claims = jwt.decode(token, key=key, algorithms=[ALGORITHM], issuer=ISSUER)
        return TokenClaims(user_id=claims["sub"], email=claims.get("email", ""), raw=claims)
    except jwt.PyJWTError as exc:
        raise AppError("INVALID_TOKEN", f"Token verification failed: {exc}", 401) from exc


def make_current_user_dependency(jwks_client: JWKSClient):
    """Returns a FastAPI dependency: `Authorization: Bearer <jwt>` -> TokenClaims."""

    async def get_current_user(authorization: Optional[str] = Header(default=None)) -> TokenClaims:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise AppError("UNAUTHENTICATED", "Missing bearer token", 401)
        token = authorization.split(" ", 1)[1]
        return decode_token(token, jwks_client)

    return get_current_user
