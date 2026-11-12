from argon2 import PasswordHasher as _Argon2
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError


class Argon2PasswordHasher:
    """`merchant_admin.password_hash` has a DB CHECK requiring `$argon2id$` — this is the only
    hasher that may produce or consume values for that column (migrations/versions/
    0002_legacy_schema_port.py, `ck_admin_password_hash`)."""

    def __init__(self) -> None:
        self._argon2 = _Argon2()

    def hash(self, plain: str) -> str:
        return self._argon2.hash(plain)

    def verify(self, hashed: str, plain: str) -> bool:
        try:
            return self._argon2.verify(hashed, plain)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False
