import json
from base64 import urlsafe_b64encode
from hashlib import sha256
from pathlib import Path

import jwt
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from marketplace.identity import MerchantIdentity


class JwtVerificationConfigurationError(ValueError):
    """Invalid verification configuration, without file paths or key contents."""


class InvalidAccessToken(Exception):
    pass


class JwtMerchantAuthenticator:
    """Verify the #182 access token before using its merchant identity."""

    def __init__(
        self,
        public_key_file: Path,
        *,
        issuer: str,
        audience: str,
    ) -> None:
        try:
            key = load_pem_public_key(public_key_file.read_bytes())
            if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(
                key.curve, ec.SECP256R1
            ):
                raise ValueError("ES256 requires a P-256 public key")
        except (OSError, ValueError, TypeError, UnsupportedAlgorithm):
            raise JwtVerificationConfigurationError(
                "JWT verification requires a readable P-256 public key"
            ) from None
        self._key = key
        self._issuer = issuer
        self._audience = audience
        numbers = key.public_numbers()
        public = {
            "crv": "P-256",
            "kty": "EC",
            "x": urlsafe_b64encode(numbers.x.to_bytes(32, "big")).rstrip(b"=").decode("ascii"),
            "y": urlsafe_b64encode(numbers.y.to_bytes(32, "big")).rstrip(b"=").decode("ascii"),
        }
        canonical = json.dumps(public, sort_keys=True, separators=(",", ":")).encode("ascii")
        self._kid = urlsafe_b64encode(sha256(canonical).digest()).rstrip(b"=").decode("ascii")

    def __call__(self, access_token: str) -> MerchantIdentity:
        try:
            decoded = jwt.decode_complete(
                access_token,
                self._key,
                algorithms=["ES256"],
                issuer=self._issuer,
                audience=self._audience,
                options={
                    "require": ["sub", "mid", "sid", "jti", "kind", "iat", "exp", "iss", "aud"],
                    "strict_aud": True,
                },
            )
            header, claims = decoded["header"], decoded["payload"]
            if header.get("kid") != self._kid or header.get("typ") != "JWT" or header.get("crit"):
                raise jwt.InvalidTokenError("Unsupported access token header")
            for name in ("sub", "mid", "sid", "jti"):
                if not isinstance(claims[name], str) or not claims[name].strip():
                    raise jwt.InvalidTokenError("Invalid access token identity")
            if claims["kind"] != "MERCHANT_ADMIN":
                raise jwt.InvalidTokenError("Unsupported access token kind")
            if (
                type(claims["iat"]) is not int
                or type(claims["exp"]) is not int
                or claims["exp"] <= claims["iat"]
            ):
                raise jwt.InvalidTokenError("Invalid access token timestamps")
        except (jwt.PyJWTError, ValueError, TypeError, OverflowError):
            raise InvalidAccessToken from None

        return MerchantIdentity(user_id=claims["sub"], merchant_id=claims["mid"])
