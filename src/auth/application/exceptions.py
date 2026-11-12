class InvalidCredentials(Exception):
    """Unknown email, wrong password, or a disabled account — all answer identically."""


class AccountLocked(Exception):
    pass


class RefreshTokenInvalid(Exception):
    pass


class RefreshTokenExpired(Exception):
    pass


class RefreshTokenRevoked(Exception):
    pass


class TokenVerificationError(Exception):
    """The access token is missing, malformed, expired, or signed by an unknown key."""


class SessionCompromised(Exception):
    """A refresh token was presented twice. The whole family has been revoked."""

    def __init__(self, family_id: str, revoked_count: int) -> None:
        super().__init__(family_id)
        self.family_id = family_id
        self.revoked_count = revoked_count
