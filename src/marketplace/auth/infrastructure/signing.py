import json
from base64 import urlsafe_b64encode
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path

import jwt
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from marketplace.auth.application.dto import AccessClaims, CustomerAccessClaims, SigningPublicKey
from marketplace.auth.application.exceptions import AuthFailure, AuthRefusal


class SigningConfigurationError(ValueError):
    """A key-loading error that never includes a secret or file path."""


def base64url(value: bytes) -> str:
    return urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class Es256TokenSigner:
    def __init__(self, private_key_file: Path, *, issuer: str, audience: str) -> None:
        try:
            key = load_pem_private_key(private_key_file.read_bytes(), password=None)
            if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
                key.curve, ec.SECP256R1
            ):
                raise ValueError("ES256 requires a P-256 private key")
        except (OSError, ValueError, TypeError, UnsupportedAlgorithm):
            raise SigningConfigurationError(
                "JWT signing requires a readable P-256 private key"
            ) from None
        self._key = key
        self._issuer = issuer
        self._audience = audience
        numbers = key.public_key().public_numbers()
        public = {
            "crv": "P-256",
            "kty": "EC",
            "x": base64url(numbers.x.to_bytes(32, "big")),
            "y": base64url(numbers.y.to_bytes(32, "big")),
        }
        canonical = json.dumps(public, sort_keys=True, separators=(",", ":")).encode("ascii")
        self._public = SigningPublicKey(
            kid=base64url(sha256(canonical).digest()), x=public["x"], y=public["y"]
        )

    def sign(self, claims: AccessClaims | CustomerAccessClaims) -> str:
        try:
            return jwt.encode(
                {**asdict(claims), "iss": self._issuer, "aud": self._audience},
                self._key,
                algorithm="ES256",
                headers={"kid": self._public.kid, "typ": "JWT"},
            )
        except (jwt.PyJWTError, ValueError, TypeError):
            raise AuthFailure(AuthRefusal("AUTH_UNAVAILABLE")) from None

    def public_keys(self) -> tuple[SigningPublicKey, ...]:
        return (self._public,)


class UnavailableTokenSigner:
    def sign(self, claims: AccessClaims | CustomerAccessClaims) -> str:
        raise AuthFailure(AuthRefusal("AUTH_UNAVAILABLE"))

    def public_keys(self) -> tuple[SigningPublicKey, ...]:
        raise AuthFailure(AuthRefusal("AUTH_UNAVAILABLE"))
