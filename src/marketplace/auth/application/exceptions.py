from dataclasses import dataclass
from typing import Literal

AuthErrorCode = Literal[
    "INVALID_CREDENTIALS",
    "ACCOUNT_LOCKED",
    "RATE_LIMITED",
    "AUTH_UNAVAILABLE",
    "VALIDATION_FAILED",
    "REFRESH_TOKEN_INVALID",
    "REFRESH_TOKEN_EXPIRED",
    "REFRESH_TOKEN_REVOKED",
    "SESSION_COMPROMISED",
]


@dataclass(frozen=True)
class AuthRefusal:
    code: AuthErrorCode
    retry_after: int | None = None


class AuthFailure(Exception):
    def __init__(self, refusal: AuthRefusal) -> None:
        super().__init__(refusal.code)
        self.refusal = refusal
