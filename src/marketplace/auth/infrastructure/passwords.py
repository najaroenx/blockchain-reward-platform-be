from argon2 import PasswordHasher, Type, extract_parameters
from argon2.exceptions import Argon2Error, InvalidHashError


class Argon2PasswordVerifier:
    def __init__(self, hasher: PasswordHasher | None = None) -> None:
        self.hasher = hasher or PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
        self.dummy = self.hasher.hash("dummy-login-verification")
        self.parameters = extract_parameters(self.dummy)

    def verify(self, password_hash: str | None, password: str) -> bool:
        usable = False
        if password_hash:
            try:
                parameters = extract_parameters(password_hash)
                usable = parameters.type == Type.ID and parameters == self.parameters
            except (InvalidHashError, ValueError):
                pass
        valid: bool
        try:
            valid = self.hasher.verify(
                password_hash if usable and password_hash else self.dummy, password
            )
        except (Argon2Error, InvalidHashError):
            valid = False
        return valid and usable
