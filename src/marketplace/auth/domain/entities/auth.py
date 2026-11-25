from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from unicodedata import normalize

MerchantStatus = Literal["PENDING", "ACTIVE", "SUSPENDED", "CLOSED"]


def normalize_email(value: str) -> str:
    return normalize("NFC", value).strip().lower()


# Defines token lifetimes and the limits used to rate-limit or lock out login attempts.
@dataclass(frozen=True)
class LoginPolicy:
    access_ttl: Literal[900] = field(default=900, init=False)
    refresh_ttl: int = field(default=30 * 24 * 60 * 60, init=False)
    lockout_threshold: int = field(default=5, init=False)
    lockout_seconds: int = field(default=900, init=False)
    ip_limit: int = 20
    ip_window: int = 60


DEFAULT_LOGIN_POLICY = LoginPolicy()


# Identifies the merchant administrator and merchant state used when granting access.
@dataclass(frozen=True)
class MerchantSubject:
    id: str
    merchant_id: str
    merchant_status: MerchantStatus


# Identifies a customer as a subject of an authentication session.
@dataclass(frozen=True)
class CustomerSubject:
    id: str


# Represents a session eligible for rotation, including its family and request binding.
@dataclass(frozen=True)
class RefreshCandidate:
    session_id: str
    family_id: str
    subject: MerchantSubject | CustomerSubject
    user_agent_hash: bytes | None


# Pairs the administrator identity with the stored hash and disabled state used at login.
@dataclass(frozen=True)
class AdminCredentials:
    subject: MerchantSubject
    password_hash: str = field(repr=False)
    disabled: bool = False


# Carries a reserved login attempt into the completion or abort step.
@dataclass(frozen=True)
class Attempt:
    id: str
    identifier_hash: bytes
    ip_hash: bytes
    user_agent_hash: bytes | None
    created_at: datetime


# Describes a session to persist with a refresh token hash and expiration.
@dataclass(frozen=True)
class NewSession:
    id: str
    family_id: str
    refresh_hash: bytes = field(repr=False)
    created_at: datetime
    expires_at: datetime


# Pairs verified credentials with the session that the store should commit.
@dataclass(frozen=True)
class LoginGrant:
    credentials: AdminCredentials
    session: NewSession
