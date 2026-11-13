from marketplace.auth.application.dto import AccessClaims
from marketplace.auth.application.exceptions import LoginFailure, LoginRefusal


class UnavailableTokenSigner:
    def sign(self, claims: AccessClaims) -> str:
        raise LoginFailure(LoginRefusal("AUTH_UNAVAILABLE"))
