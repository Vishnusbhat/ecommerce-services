"""RSA keypair for RS256 JWT signing, persisted to a volume so `kid` and the
public key stay stable across container restarts (docs/auth-service.md:
asymmetric signing so the public key is safe to publish via JWKS).
"""
from __future__ import annotations

import base64
import hashlib
import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import settings

_PRIVATE_PATH = os.path.join(settings.keys_dir, "private_key.pem")
_PUBLIC_PATH = os.path.join(settings.keys_dir, "public_key.pem")


def _generate() -> None:
    os.makedirs(settings.keys_dir, exist_ok=True)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    with open(_PRIVATE_PATH, "wb") as f:
        f.write(private_pem)
    with open(_PUBLIC_PATH, "wb") as f:
        f.write(public_pem)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class KeyMaterial:
    def __init__(self):
        if not (os.path.exists(_PRIVATE_PATH) and os.path.exists(_PUBLIC_PATH)):
            _generate()

        with open(_PRIVATE_PATH, "rb") as f:
            self.private_pem = f.read()
        with open(_PUBLIC_PATH, "rb") as f:
            self.public_pem = f.read()

        self.private_key = serialization.load_pem_private_key(self.private_pem, password=None)
        self.public_key = self.private_key.public_key()
        self.kid = hashlib.sha256(self.public_pem).hexdigest()[:16]

    def jwks(self) -> dict:
        numbers = self.public_key.public_numbers()
        n = numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")
        e = numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")
        return {
            "keys": [
                {
                    "kty": "RSA",
                    "use": "sig",
                    "alg": "RS256",
                    "kid": self.kid,
                    "n": _b64url(n),
                    "e": _b64url(e),
                }
            ]
        }


key_material = KeyMaterial()
