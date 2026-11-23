from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from unicodedata import normalize

MerchantStatus = Literal["PENDING", "ACTIVE", "SUSPENDED", "CLOSED"]


def normalize_email(value: str) -> str:
    return normalize("NFC", value).strip().lower()


@dataclass(frozen=True)
class LoginPolicy:
    access_ttl: Literal[900] = field(default=900, init=False)
    refresh_ttl: int = field(default=30 * 24 * 60 * 60, init=False)
    lockout_threshold: int = field(default=5, init=False)
    lockout_seconds: int = field(default=900, init=False)
    ip_limit: int = 20
    ip_window: int = 60


DEFAULT_LOGIN_POLICY = LoginPolicy()


@dataclass(frozen=True)
class MerchantSubject:
    id: str
    merchant_id: str
    merchant_status: MerchantStatus


@dataclass(frozen=True)
class AdminCredentials:
    subject: MerchantSubject
    password_hash: str = field(repr=False)
    disabled: bool = False


@dataclass(frozen=True)
class Attempt:
    id: str
    identifier_hash: bytes
    ip_hash: bytes
    user_agent_hash: bytes | None
    created_at: datetime


@dataclass(frozen=True)
class NewSession:
    id: str
    family_id: str
    refresh_hash: bytes = field(repr=False)
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class LoginGrant:
    credentials: AdminCredentials
    session: NewSession
