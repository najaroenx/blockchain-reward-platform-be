import pytest
from pydantic import ValidationError

from marketplace.coupons.presentation.schemas import RedeemCouponRequest, RedeemCouponResponse


@pytest.mark.parametrize("type_id", ["1", str(2**256 - 1)])
def test_request_preserves_valid_type_ids(type_id: str) -> None:
    request = RedeemCouponRequest.model_validate({"typeId": type_id})
    assert request.model_dump() == {"typeId": type_id}


@pytest.mark.parametrize(
    "type_id",
    [
        "",
        "0",
        "01",
        "-1",
        "+1",
        "1.0",
        " 1",
        "1 ",
        "๑",
        "ct_1",
        str(2**256),
        "1" * 79,
        1,
        1.0,
        True,
        None,
    ],
)
def test_request_rejects_invalid_type_ids(type_id: object) -> None:
    with pytest.raises(ValidationError):
        RedeemCouponRequest.model_validate({"typeId": type_id})


def test_request_requires_type_id() -> None:
    with pytest.raises(ValidationError):
        RedeemCouponRequest.model_validate({})


@pytest.mark.parametrize(
    "field", ["walletAddress", "from", "amount", "couponCode", "redeemedAtMerchantId", "chainId"]
)
def test_request_rejects_extra_fields(field: str) -> None:
    with pytest.raises(ValidationError):
        RedeemCouponRequest.model_validate({"typeId": "1", field: "1"})


def test_response_is_an_admission_acknowledgement() -> None:
    response = RedeemCouponResponse.model_validate({"operationId": "op_123", "typeId": "1"})
    assert response.model_dump() == {
        "operationId": "op_123",
        "status": "PENDING",
        "typeId": "1",
        "amount": "1",
    }


@pytest.mark.parametrize(
    "override",
    [
        {"status": "SUCCEEDED"},
        {"amount": "2"},
        {"couponCode": "CODE"},
        {"settlement": {}},
        {"operationId": ""},
    ],
)
def test_response_rejects_results_outside_admission(override: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        RedeemCouponResponse.model_validate({"operationId": "op_123", "typeId": "1", **override})
