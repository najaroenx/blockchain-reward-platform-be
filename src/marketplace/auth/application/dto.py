from dataclasses import dataclass, field
from typing import Literal

from marketplace.auth.domain.entities.auth import MerchantSubject


# Carries normalized credentials and hashed request metadata from HTTP into the login use case.
@dataclass(frozen=True)
class LoginCommand:
    normalized_email: str
    password: str = field(repr=False)
    ip_hash: bytes
    user_agent_hash: bytes | None


# Identifies a merchant administrator and their session in a signed access token.
@dataclass(frozen=True)
class AccessClaims:
    sub: str
    mid: str
    sid: str
    jti: str
    iat: int
    exp: int
    kind: Literal["MERCHANT_ADMIN"] = "MERCHANT_ADMIN"


# Identifies a customer session without requiring a merchant identity.
@dataclass(frozen=True)
class CustomerAccessClaims:
    sub: str
    sid: str
    jti: str
    iat: int
    exp: int
    kind: Literal["CUSTOMER"] = "CUSTOMER"


# Supplies the refresh token hash and request IP hash to the token rotation use case.
@dataclass(frozen=True)
class RefreshCommand:
    token_hash: bytes = field(repr=False)
    ip_hash: bytes


# Returns the newly issued tokens and cookie lifetimes after the rotation succeeds.
@dataclass(frozen=True)
class RefreshResult:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_in: int
    refresh_max_age: int


# Returns the authenticated subject and tokens so the route can build the response and cookie.
@dataclass(frozen=True)
class LoginResult:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    subject: MerchantSubject
    expires_in: int
    refresh_max_age: int


# Carries the public part of a signing key from the key publisher to the JWKS route.
@dataclass(frozen=True)
class SigningPublicKey:
    kid: str
    x: str
    y: str
    kty: Literal["EC"] = "EC"
    crv: Literal["P-256"] = "P-256"
    alg: Literal["ES256"] = "ES256"
    use: Literal["sig"] = "sig"
