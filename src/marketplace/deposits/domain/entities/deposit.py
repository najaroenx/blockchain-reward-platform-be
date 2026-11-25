from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit

MIN_SATANG = 2000
MAX_SATANG = 5000000
BASE_UNITS_PER_SATANG = 10**16


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


# Validates the allowed range in satang and converts the value to exact token base units.
@dataclass(frozen=True)
class DepositAmount:
    satang: int

    def __post_init__(self) -> None:
        if not MIN_SATANG <= self.satang <= MAX_SATANG:
            raise ValueError("Amount must be between 2000 and 5000000 satang")

    @property
    def base_units(self) -> str:
        return str(self.satang * BASE_UNITS_PER_SATANG)


# Carries a provider reference, HTTPS payment URL, and expiry for a checkout.
@dataclass(frozen=True)
class PaymentInstructions:
    reference: str
    url: str
    expires_at: datetime

    def validate(self, now: datetime) -> None:
        url = urlsplit(self.url)
        if (
            not self.reference
            or url.scheme != "https"
            or not url.hostname
            or url.username is not None
            or self.expires_at.tzinfo is None
            or self.expires_at <= now
        ):
            raise ValueError("Invalid payment instructions")


# Represents a saved deposit and derives its current payment status from the supplied time.
@dataclass(frozen=True)
class DepositRecord:
    id: str
    amount_satang: int
    amount_base: str
    created_at: datetime
    expires_at: datetime

    def status_at(self, now: datetime) -> Literal["PENDING_PAYMENT", "EXPIRED"]:
        return "EXPIRED" if utc(self.expires_at) <= now else "PENDING_PAYMENT"
