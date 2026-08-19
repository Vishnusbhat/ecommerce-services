from __future__ import annotations

import time
import uuid

import bcrypt
import jwt

from app.config import settings
from app.keys import key_material


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def create_access_token(user_id: str, email: str) -> str:
    now = int(time.time())
    claims = {
        "sub": user_id,
        "email": email,
        "iss": settings.jwt_issuer,
        "iat": now,
        "exp": now + settings.jwt_access_token_ttl_seconds,
        "jti": uuid.uuid4().hex,
    }
    headers = {"kid": key_material.kid}
    return jwt.encode(claims, key_material.private_pem, algorithm="RS256", headers=headers)
