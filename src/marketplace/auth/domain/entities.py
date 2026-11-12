import unicodedata
from dataclasses import dataclass
from typing import Literal

SubjectKind = Literal["MERCHANT_ADMIN", "CUSTOMER", "PLATFORM_ADMIN"]


@dataclass(frozen=True)
class Subject:
    kind: SubjectKind
    id: str
    merchant_id: str | None = None


def normalize_email(raw: str) -> str:
    """NFC + trim + lowercase, matching the `ck_admin_email_normalized` DB constraint."""
    return unicodedata.normalize("NFC", raw).strip().lower()
