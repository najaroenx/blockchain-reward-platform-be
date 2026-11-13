from collections.abc import Iterator
from dataclasses import asdict

import pytest
from fastapi.testclient import TestClient

from marketplace.coupons.application.dto import (
    AuthenticatedWallet,
    CouponDeployment,
    RedeemCouponPayload,
    RedeemCouponResult,
)
from marketplace.coupons.application.exceptions import RedeemErrorCode, RedeemFailure
from marketplace.coupons.application.use_cases import RedeemCoupons
from marketplace.coupons.infrastructure.mock_auth import MockTokenAuthenticator
from marketplace.coupons.presentation.router import RedeemDependencies
from marketplace.main import create_app
from marketplace.settings import ApiSettings

URL = "/v1/coupons/redeem"
WALLET = "0x" + "a" * 40
COUPON = "0x" + "b" * 40
HEADERS = {"Authorization": "Bearer test-token", "Idempotency-Key": "redeem-key-1"}


class FixedDeployment:
    def resolve(self, type_id: int) -> CouponDeployment:
        return CouponDeployment(chain_id=1337, contract_address=COUPON)


class RecordingAdmission:
    """Test double for admission; does not claim durable database writes."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, RedeemCouponPayload]] = []
        self.fail = False

    def admit(
        self, *, user_id: str, idempotency_key: str, payload: RedeemCouponPayload
    ) -> RedeemCouponResult:
        self.calls.append((user_id, idempotency_key, payload))
        if self.fail:
            raise RuntimeError("private admission failure")
        return RedeemCouponResult(operation_id="op_test", type_id=payload.type_id)


@pytest.fixture
def redeem_client(database_url: str) -> Iterator[tuple[TestClient, RecordingAdmission]]:
    admissions = RecordingAdmission()

    def authenticate(token: str) -> AuthenticatedWallet:
        # This fixture substitutes for auth; production token verification remains unwired.
        if token != "test-token":
            raise RedeemFailure(RedeemErrorCode.UNAUTHENTICATED)
        return AuthenticatedWallet(user_id="user_test", wallet_address=WALLET)

    app = create_app(
        ApiSettings(database_url=database_url, APP_ENV="test"),
        redeem=RedeemDependencies(
            authenticate=authenticate,
            redeemer=RedeemCoupons(FixedDeployment(), admissions),
        ),
    )
    with TestClient(app, raise_server_exceptions=False) as http:
        yield http, admissions


@pytest.mark.parametrize("type_id", ["1", str(2**256 - 1)])
def test_http_request_admits_one_coupon_with_trusted_context(
    redeem_client: tuple[TestClient, RecordingAdmission], type_id: str
) -> None:
    http, admissions = redeem_client
    response = http.post(URL, json={"typeId": type_id}, headers=HEADERS)

    assert response.status_code == 202
    assert response.json() == {
        "operationId": "op_test",
        "status": "PENDING",
        "typeId": type_id,
        "amount": "1",
    }
    [(user_id, key, payload)] = admissions.calls
    assert user_id == "user_test"
    assert key == "redeem-key-1"
    assert asdict(payload) == {
        "chain_id": 1337,
        "coupon_contract_address": COUPON,
        "wallet_address": WALLET,
        "type_id": int(type_id),
        "amount": 1,
    }


@pytest.mark.parametrize(
    "body",
    [
        {},
        [],
        {"typeId": None},
        {"typeId": 1},
        {"typeId": True},
        {"typeId": "0"},
        {"typeId": "01"},
        {"typeId": "-1"},
        {"typeId": "1.0"},
        {"typeId": " 1"},
        {"typeId": "๑"},
        {"typeId": str(2**256)},
    ],
)
def test_invalid_http_body_is_rejected_before_admission(
    redeem_client: tuple[TestClient, RecordingAdmission], body: object
) -> None:
    http, admissions = redeem_client
    response = http.post(URL, json=body, headers=HEADERS)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"
    assert admissions.calls == []


@pytest.mark.parametrize(
    "field", ["walletAddress", "from", "amount", "couponCode", "redeemedAtMerchantId", "chainId"]
)
def test_http_caller_cannot_override_the_job_context(
    redeem_client: tuple[TestClient, RecordingAdmission], field: str
) -> None:
    http, admissions = redeem_client
    response = http.post(URL, json={"typeId": "1", field: "2"}, headers=HEADERS)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"
    assert admissions.calls == []


def test_malformed_json_does_not_echo_input_or_create_work(
    redeem_client: tuple[TestClient, RecordingAdmission],
) -> None:
    http, admissions = redeem_client
    response = http.post(
        URL,
        content='{"typeId": "private-invalid-input",',
        headers={**HEADERS, "Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"
    assert "private-invalid-input" not in response.text
    assert admissions.calls == []


@pytest.mark.parametrize("key", [None, "", "short", "x" * 256])
def test_idempotency_header_is_required_and_validated(
    redeem_client: tuple[TestClient, RecordingAdmission], key: str | None
) -> None:
    http, admissions = redeem_client
    headers = {"Authorization": HEADERS["Authorization"]}
    if key is not None:
        headers["Idempotency-Key"] = key
    response = http.post(URL, json={"typeId": "1"}, headers=headers)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == (
        "IDEMPOTENCY_KEY_REQUIRED" if key is None else "VALIDATION_FAILED"
    )
    assert admissions.calls == []


@pytest.mark.parametrize("authorization", [None, "Basic anything", "Bearer", "Bearer invalid"])
def test_auth_rejection_never_reaches_admission(
    redeem_client: tuple[TestClient, RecordingAdmission], authorization: str | None
) -> None:
    http, admissions = redeem_client
    headers = {"Idempotency-Key": HEADERS["Idempotency-Key"]}
    if authorization is not None:
        headers["Authorization"] = authorization
    response = http.post(URL, json={"typeId": "1"}, headers=headers)
    assert response.status_code == 401
    assert admissions.calls == []


def test_failed_admission_never_returns_an_acknowledgement(
    redeem_client: tuple[TestClient, RecordingAdmission],
) -> None:
    http, admissions = redeem_client
    admissions.fail = True
    response = http.post(URL, json={"typeId": "1"}, headers=HEADERS)
    assert response.status_code == 500
    assert "operationId" not in response.text
    assert "private admission failure" not in response.text


def test_unconfigured_app_cannot_accept_redeem_work(client: TestClient) -> None:
    response = client.post(URL, json={"typeId": "1"}, headers=HEADERS)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "REDEEM_UNAVAILABLE"


def test_openapi_documents_the_single_type_request_and_required_headers(
    client: TestClient,
) -> None:
    document = client.get("/openapi.json").json()
    route = document["paths"][URL]["post"]
    request_schema = document["components"]["schemas"]["RedeemCouponRequest"]
    assert set(request_schema["properties"]) == {"typeId"}
    assert request_schema["additionalProperties"] is False
    assert route["security"] == [{"HTTPBearer": []}]
    assert route["parameters"][0]["name"] == "Idempotency-Key"
    assert route["parameters"][0]["required"] is True
    assert "202" in route["responses"]
    assert "400" in route["responses"]
    assert "503" in route["responses"]


@pytest.mark.parametrize(
    ("token", "expected_status"),
    [(None, 401), ("wrong-token", 401), ("test-mock-token", 503)],
)
def test_configured_mock_auth_runs_before_the_missing_admission_check(
    database_url: str, token: str | None, expected_status: int
) -> None:
    app = create_app(
        ApiSettings(
            database_url=database_url,
            APP_ENV="test",
            mock_auth_enabled=True,
            mock_auth_token="test-mock-token",
        )
    )
    headers = {"Idempotency-Key": "mock-redeem-key"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    with TestClient(app) as http:
        response = http.post(URL, json={"typeId": "1"}, headers=headers)
    assert response.status_code == expected_status
    if expected_status == 401:
        assert response.json()["error"]["code"] == "UNAUTHENTICATED"
        assert response.headers["www-authenticate"] == "Bearer"
    else:
        assert response.json()["error"] == {
            "code": "REDEEM_UNAVAILABLE",
            "message": "Redeem admission adapters are not configured.",
        }
    assert "test-mock-token" not in response.text
    assert "operationId" not in response.text


def test_mock_auth_profile_reaches_the_job_without_persisting_the_token(database_url: str) -> None:
    admissions = RecordingAdmission()
    wallet = AuthenticatedWallet(user_id="mock-user", wallet_address="0x" + "c" * 40)
    app = create_app(
        ApiSettings(database_url=database_url, APP_ENV="test"),
        redeem=RedeemDependencies(
            authenticate=MockTokenAuthenticator("fixture-token", wallet),
            redeemer=RedeemCoupons(FixedDeployment(), admissions),
        ),
    )
    with TestClient(app) as http:
        response = http.post(
            URL,
            json={"typeId": "1"},
            headers={"Authorization": "Bearer fixture-token", "Idempotency-Key": "mock-redeem-key"},
        )
    assert response.status_code == 202
    [(user_id, _, payload)] = admissions.calls
    assert user_id == wallet.user_id
    assert payload.wallet_address == wallet.wallet_address
    assert payload.amount == 1
    assert "fixture-token" not in repr(payload)


@pytest.mark.parametrize(
    ("body", "key", "expected_code"),
    [
        ({"typeId": "0"}, "mock-redeem-key", "VALIDATION_FAILED"),
        ({"typeId": "1", "amount": "2"}, "mock-redeem-key", "VALIDATION_FAILED"),
        ({"typeId": "1"}, None, "IDEMPOTENCY_KEY_REQUIRED"),
    ],
)
def test_mock_auth_allows_input_validation_before_admission_is_available(
    database_url: str, body: dict[str, str], key: str | None, expected_code: str
) -> None:
    app = create_app(ApiSettings(database_url=database_url, APP_ENV="test", mock_auth_enabled=True))
    headers = {"Authorization": "Bearer mock-redeem-token"}
    if key is not None:
        headers["Idempotency-Key"] = key
    with TestClient(app) as http:
        response = http.post(URL, json=body, headers=headers)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == expected_code
