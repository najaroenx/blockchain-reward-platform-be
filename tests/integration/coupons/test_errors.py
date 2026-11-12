from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from sqlalchemy.exc import OperationalError

from marketplace.coupons.application.dto import (
    AuthenticatedWallet,
    CouponDeployment,
    RedeemCouponPayload,
    RedeemCouponResult,
)
from marketplace.coupons.application.exceptions import RedeemErrorCode, RedeemFailure
from marketplace.coupons.application.use_cases import RedeemCoupons
from marketplace.coupons.presentation.router import RedeemDependencies
from marketplace.main import create_app
from marketplace.settings import ApiSettings

URL = "/v1/coupons/redeem"
HEADERS = {"Authorization": "Bearer fixture-token", "Idempotency-Key": "error-contract-key"}


class FailureAdapters:
    def __init__(self, stage: str, failure: Exception) -> None:
        self.stage = stage
        self.failure = failure
        self.admissions = 0

    def authenticate(self, token: str) -> AuthenticatedWallet:
        if self.stage == "auth":
            raise self.failure
        return AuthenticatedWallet(user_id="test-user", wallet_address="0x" + "a" * 40)

    def resolve(self, type_id: int) -> CouponDeployment:
        if self.stage == "deployment":
            raise self.failure
        return CouponDeployment(chain_id=1337, contract_address="0x" + "b" * 40)

    def admit(
        self, *, user_id: str, idempotency_key: str, payload: RedeemCouponPayload
    ) -> RedeemCouponResult:
        self.admissions += 1
        raise self.failure


@pytest.mark.parametrize(
    ("code", "status", "stage"),
    [
        ("UNAUTHENTICATED", 401, "auth"),
        ("WALLET_FORBIDDEN", 403, "auth"),
        ("COUPON_TYPE_NOT_FOUND", 404, "deployment"),
        ("IDEMPOTENCY_KEY_IN_PROGRESS", 409, "admission"),
        ("IDEMPOTENCY_KEY_REUSED", 409, "admission"),
        ("INSUFFICIENT_BALANCE", 422, "admission"),
        ("COUPON_NOT_STARTED", 422, "admission"),
        ("COUPON_EXPIRED", 422, "admission"),
        ("NO_REDEEM_CODE_AVAILABLE", 422, "admission"),
        ("RATE_LIMITED", 429, "auth"),
        ("REDEEM_UNAVAILABLE", 503, "deployment"),
        ("SIGNER_UNAUTHORIZED", 503, "admission"),
        ("CONTRACT_CALL_FAILED", 503, "admission"),
        ("ADMISSION_UNAVAILABLE", 503, "admission"),
    ],
)
def test_adapter_failures_match_the_published_http_contract(
    database_url: str, code: str, status: int, stage: str
) -> None:
    adapters = FailureAdapters(stage, RedeemFailure(RedeemErrorCode(code), retry_after=7))
    app = create_app(
        ApiSettings(database_url=database_url, APP_ENV="test"),
        redeem=RedeemDependencies(
            authenticate=adapters.authenticate, redeemer=RedeemCoupons(adapters, adapters)
        ),
    )
    with TestClient(app) as http:
        response = http.post(URL, json={"typeId": "1"}, headers=HEADERS)
        document = http.get("/openapi.json").json()
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    schema = document["paths"][URL]["post"]["responses"][str(status)]["content"][
        "application/json"
    ]["schema"]
    Draft202012Validator({**schema, "components": document["components"]}).validate(response.json())
    examples = document["paths"][URL]["post"]["responses"][str(status)]["content"][
        "application/json"
    ]["examples"]
    assert examples[code]["value"] == response.json()
    assert "operationId" not in response.text
    assert "fixture-token" not in response.text
    assert adapters.admissions == (1 if stage == "admission" else 0)
    if status == 401:
        assert response.headers["www-authenticate"] == "Bearer"
    if status == 429:
        assert response.headers["retry-after"] == "7"


@pytest.mark.parametrize("stage", ["auth", "deployment", "admission"])
def test_unexpected_infrastructure_failures_are_sanitized_at_every_boundary(
    database_url: str, stage: str
) -> None:
    failure = OperationalError("SELECT private-sql", {}, RuntimeError("private-db-password"))
    adapters = FailureAdapters(stage, failure)
    app = create_app(
        ApiSettings(database_url=database_url, APP_ENV="test"),
        redeem=RedeemDependencies(
            authenticate=adapters.authenticate, redeemer=RedeemCoupons(adapters, adapters)
        ),
    )
    with TestClient(app) as http:
        response = http.post(URL, json={"typeId": "1"}, headers=HEADERS)
    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "INTERNAL_ERROR", "message": "The request could not be completed."}
    }
    assert "private-" not in response.text
    assert "operationId" not in response.text


def test_unreadable_json_bytes_are_a_sanitized_validation_error(client: TestClient) -> None:
    response = client.post(
        URL, content=b"\xff", headers={**HEADERS, "Content-Type": "application/json"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


def test_success_response_required_fields_are_in_openapi(client: TestClient) -> None:
    document = client.get("/openapi.json").json()
    schema = document["components"]["schemas"]["RedeemCouponResponse"]
    assert set(schema["required"]) == {"operationId", "status", "typeId", "amount"}
    assert schema["properties"]["status"]["const"] == "PENDING"
    assert schema["properties"]["amount"]["const"] == "1"


def test_catalog_validation_stays_422(client: TestClient) -> None:
    response = client.get("/api/v1/marketplaces/1337/invalid/listings")
    assert response.status_code == 422


def test_postman_and_openapi_artifacts_exist_and_are_plain_json() -> None:
    import json

    root = Path(__file__).resolve().parents[3]
    collection = json.loads((root / "postman/redeem.postman_collection.json").read_text())
    document = json.loads((root / "docs/api/redeem.openapi.json").read_text())
    assert document["openapi"].startswith("3.1")
    assert set(document["paths"]) == {URL}
    assert {folder["name"] for folder in collection["item"]} == {"Local mock", "Adapter contract"}
