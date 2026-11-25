from threading import BoundedSemaphore

from argon2 import PasswordHasher, exceptions, profiles

from marketplace.merchants.application.interfaces.signup import SignupCredentialPort
from marketplace.merchants.domain.exceptions import SignupFailure


# Implements password hashing and verification with Argon2id and bounded concurrency.
class SignupCredentials(SignupCredentialPort):
    def __init__(self) -> None:
        self._hasher = PasswordHasher.from_parameters(profiles.RFC_9106_LOW_MEMORY)
        self._slots = BoundedSemaphore(4)

    def hash(self, password: str) -> str:
        try:
            with self._slots:
                return self._hasher.hash(password)
        except exceptions.Argon2Error:
            raise SignupFailure("SIGNUP_UNAVAILABLE") from None

    def matches(self, password: str, password_hash: str) -> bool:
        try:
            with self._slots:
                return self._hasher.verify(password_hash, password)
        except exceptions.VerifyMismatchError:
            return False
        except exceptions.Argon2Error:
            raise SignupFailure("SIGNUP_UNAVAILABLE") from None
