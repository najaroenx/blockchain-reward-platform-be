from secrets import compare_digest

from marketplace.coupons.application.dto import AuthenticatedWallet
from marketplace.coupons.application.exceptions import RedeemErrorCode, RedeemFailure


class MockTokenAuthenticator:
    """Match a local fixture token to a fixed user and wallet; no JWT verification."""

    def __init__(self, token: str, wallet: AuthenticatedWallet) -> None:
        self._token = token.encode("utf-8")
        self._wallet = wallet

    def __call__(self, access_token: str) -> AuthenticatedWallet:
        if not compare_digest(access_token.encode("utf-8"), self._token):
            raise RedeemFailure(RedeemErrorCode.UNAUTHENTICATED)
        return self._wallet
