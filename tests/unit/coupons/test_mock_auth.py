import pytest

from marketplace.coupons.application.dto import AuthenticatedWallet
from marketplace.coupons.application.exceptions import RedeemErrorCode, RedeemFailure
from marketplace.coupons.infrastructure.mock_auth import MockTokenAuthenticator

WALLET = AuthenticatedWallet(user_id="local-user", wallet_address="0x" + "a" * 40)


def test_mock_token_resolves_only_the_configured_profile() -> None:
    authenticate = MockTokenAuthenticator("local-test-token", WALLET)
    assert authenticate("local-test-token") == WALLET


@pytest.mark.parametrize(
    "token", ["", "wrong", "local-test-token-extra", "Bearer local-test-token", "๑"]
)
def test_mock_auth_rejects_other_tokens_without_disclosing_the_fixture(token: str) -> None:
    authenticate = MockTokenAuthenticator("local-test-token", WALLET)
    with pytest.raises(RedeemFailure) as error:
        authenticate(token)
    assert error.value.code == RedeemErrorCode.UNAUTHENTICATED
    assert "local-test-token" not in str(error.value)
