from dataclasses import dataclass, field
from typing import Literal

from marketplace.auth.domain.entities import MerchantSubject


@dataclass(frozen=True)
class LoginCommand:
    normalized_email: str
    password: str = field(repr=False)
    ip_hash: bytes
    user_agent_hash: bytes | None


@dataclass(frozen=True)
class AccessClaims:
    sub: str
    mid: str
    sid: str
    jti: str
    iat: int
    exp: int
    kind: Literal["MERCHANT_ADMIN"] = "MERCHANT_ADMIN"


@dataclass(frozen=True)
class LoginResult:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    subject: MerchantSubject
    expires_in: int
    refresh_max_age: int


@dataclass(frozen=True)
class SigningPublicKey:
    kid: str
    x: str
    y: str
    kty: Literal["EC"] = "EC"
    crv: Literal["P-256"] = "P-256"
    alg: Literal["ES256"] = "ES256"
    use: Literal["sig"] = "sig"
