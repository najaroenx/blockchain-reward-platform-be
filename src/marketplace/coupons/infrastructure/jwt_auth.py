from pathlib import Path

from marketplace.coupons.application.dto import AuthenticatedWallet
from marketplace.coupons.application.exceptions import RedeemErrorCode, RedeemFailure
from marketplace.coupons.application.ports import WalletResolver
from marketplace.infrastructure.jwt_auth import InvalidAccessToken, JwtMerchantAuthenticator
from marketplace.infrastructure.jwt_auth import (
    JwtVerificationConfigurationError as JwtVerificationConfigurationError,
)


class JwtWalletAuthenticator:
    """Verify #182 access tokens before resolving a trusted wallet for #224."""

    def __init__(
        self,
        public_key_file: Path,
        *,
        issuer: str,
        audience: str,
        wallets: WalletResolver | None = None,
    ) -> None:
        self._authenticate = JwtMerchantAuthenticator(
            public_key_file, issuer=issuer, audience=audience
        )
        self._wallets = wallets

    def __call__(self, access_token: str) -> AuthenticatedWallet:
        try:
            identity = self._authenticate(access_token)
        except InvalidAccessToken:
            raise RedeemFailure(RedeemErrorCode.UNAUTHENTICATED) from None

        if self._wallets is None:
            raise RedeemFailure(RedeemErrorCode.REDEEM_UNAVAILABLE)
        wallet = self._wallets.resolve(identity.user_id)
        if wallet is None:
            raise RedeemFailure(RedeemErrorCode.WALLET_FORBIDDEN)
        return wallet
