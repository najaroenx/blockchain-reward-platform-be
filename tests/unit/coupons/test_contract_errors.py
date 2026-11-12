import json
from importlib.resources import files

import pytest

from marketplace.coupons.infrastructure.contract_errors import classify_redeem_revert


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("CouponTypeNotFound", "COUPON_TYPE_NOT_FOUND"),
        ("InsufficientBalance", "INSUFFICIENT_BALANCE"),
        ("CouponNotStarted", "COUPON_NOT_STARTED"),
        ("CouponExpired", "COUPON_EXPIRED"),
        ("NoRedeemCodeAvailable", "NO_REDEEM_CODE_AVAILABLE"),
    ],
)
def test_proven_coupon_errors_map_to_public_codes_present_in_the_pinned_abi(
    name: str, code: str
) -> None:
    root = files("marketplace.infrastructure.blockchain") / "abi"
    abi = json.loads((root / "Coupon.json").read_text())
    assert any(entry.get("name") == name and entry["type"] == "error" for entry in abi)
    assert classify_redeem_revert(name, origin="coupon") == code


def test_backend_signer_failure_is_distinct_from_user_wallet_forbidden() -> None:
    assert (
        classify_redeem_revert("Error", origin="coupon", reason="Not authorized to redeem")
        == "SIGNER_UNAUTHORIZED"
    )


@pytest.mark.parametrize("origin", ["callback", "unknown"])
def test_shared_error_names_do_not_prove_the_users_coupon_balance_is_low(origin: str) -> None:
    root = files("marketplace.infrastructure.blockchain") / "abi"
    coupon = json.loads((root / "Coupon.json").read_text())
    vault = json.loads((root / "Vault.json").read_text())
    for abi in (coupon, vault):
        entry = next(e for e in abi if e.get("name") == "InsufficientBalance")
        assert entry["type"] == "error"
        assert entry["inputs"] == []
    assert classify_redeem_revert("InsufficientBalance", origin=origin) == "CONTRACT_CALL_FAILED"


def test_unknown_errors_and_other_string_reverts_remain_infrastructure_failures() -> None:
    assert classify_redeem_revert("Unknown", origin="coupon") == "CONTRACT_CALL_FAILED"
    assert (
        classify_redeem_revert("Error", origin="coupon", reason="private-RPC-data")
        == "CONTRACT_CALL_FAILED"
    )
