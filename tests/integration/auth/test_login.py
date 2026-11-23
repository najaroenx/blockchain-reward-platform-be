from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from http.cookies import SimpleCookie

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select

from marketplace.auth.application.dto import AccessClaims
from marketplace.auth.domain.entities import LoginPolicy
from marketplace.auth.infrastructure.models import (
    AuthEvent,
    AuthSession,
    LoginAttempt,
    LoginLockout,
    Merchant,
    MerchantAdmin,
)
from marketplace.auth.infrastructure.passwords import Argon2PasswordVerifier
from marketplace.auth.presentation.router import AuthDependencies
from marketplace.infrastructure.database import Database
from marketplace.main import create_app
from marketplace.settings import ApiSettings

URL = "/v1/auth/login"
EMAIL = "owner@shop.example"
PASSWORD = "correct-horse-battery-staple"


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int = 1) -> None:
        self.now += timedelta(seconds=seconds)


class FixtureSigner:
    """A #182 adapter fixture, not JWT signing or production authentication."""

    def __init__(self) -> None:
        self.claims: list[AccessClaims] = []
        self.failure = False
        self.on_sign: Callable[[], None] | None = None

    def sign(self, claims: AccessClaims) -> str:
        if self.on_sign:
            self.on_sign()
        if self.failure:
            raise RuntimeError("private-key-or-provider-detail")
        self.claims.append(claims)
        return "fixture-access-" + claims.jti


@dataclass
class LoginFixture:
    http: TestClient
    database: Database
    hasher: PasswordHasher
    signer: FixtureSigner
    clock: Clock


@pytest.fixture
def login_fixture(database_url: str) -> Iterator[LoginFixture]:
    hasher = PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1)
    signer = FixtureSigner()
    clock = Clock()
    app = create_app(
        ApiSettings(database_url=database_url, APP_ENV="test"),
        auth=AuthDependencies(
            Argon2PasswordVerifier(hasher), signer, LoginPolicy(ip_limit=100), clock
        ),
    )
    with TestClient(app, base_url="https://testserver") as http:
        database = app.state.database
        with database.sessions.begin() as session:
            session.add(Merchant(id="mch_test", name="Test merchant", status="PENDING"))
            session.flush()
            session.add(
                MerchantAdmin(
                    id="adm_test",
                    merchant_id="mch_test",
                    email=EMAIL,
                    email_normalized=EMAIL,
                    password_hash=hasher.hash(PASSWORD),
                    display_name="Owner",
                )
            )
        yield LoginFixture(http, database, hasher, signer, clock)


def post(fixture: LoginFixture, *, email: str = EMAIL, password: str = PASSWORD):
    return fixture.http.post(URL, json={"email": email, "password": password})


def test_login_commits_only_hashed_refresh_and_returns_secure_cookie(
    login_fixture: LoginFixture,
) -> None:
    f = login_fixture
    response = post(f, email="  OWNER@SHOP.EXAMPLE  ")
    assert response.status_code == 200
    result = response.json()
    assert set(result) == {"accessToken", "tokenType", "expiresIn", "subject"}
    assert result["tokenType"] == "Bearer"
    assert result["expiresIn"] == 900
    assert result["subject"] == {
        "kind": "MERCHANT_ADMIN",
        "id": "adm_test",
        "merchantId": "mch_test",
        "merchantStatus": "PENDING",
    }
    assert response.headers["cache-control"] == "no-store"
    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    refresh = cookie["refreshToken"]
    assert refresh["secure"] is True
    assert refresh["httponly"] is True
    assert refresh["samesite"] == "strict"
    assert refresh["path"] == "/v1/auth"
    assert int(refresh["max-age"]) == 30 * 86400
    assert len(refresh.value) == 43
    assert refresh.value not in response.text
    with f.database.sessions() as session:
        stored = session.scalars(select(AuthSession)).one()
        assert stored.refresh_token_hash == sha256(refresh.value.encode()).digest()
        assert (stored.expires_at - stored.created_at).total_seconds() == 30 * 86400
        assert session.scalar(select(func.count()).select_from(AuthEvent)) == 1
        attempt = session.scalars(select(LoginAttempt)).one()
        assert attempt.succeeded is True and attempt.failure_code is None
        assert attempt.identifier_hash == sha256(EMAIL.encode()).digest()
        assert len(attempt.ip_hash) == 32
        assert session.get(MerchantAdmin, "adm_test").last_login_at is not None
        assert f.signer.claims[0].sid == stored.id
    claims = asdict(f.signer.claims[0])
    assert set(claims) == {"sub", "mid", "sid", "jti", "iat", "exp", "kind"}
    assert claims["exp"] - claims["iat"] == 900
    assert "role" not in claims and "permission" not in claims


def test_unknown_wrong_password_and_disabled_admin_share_the_same_failure(
    login_fixture: LoginFixture,
) -> None:
    f = login_fixture
    unknown = post(f, email="unknown@shop.example")
    wrong = post(f, password="wrong-password")
    with f.database.sessions.begin() as session:
        session.get(MerchantAdmin, "adm_test").disabled_at = f.clock()
    disabled = post(f)
    for response in [unknown, wrong, disabled]:
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"
        assert response.json()["error"]["message"] == "อีเมลหรือรหัสผ่านไม่ถูกต้อง"
        assert response.json()["error"]["requestId"].startswith("req_")
        assert "set-cookie" not in response.headers
        assert "password" not in response.text
    with f.database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(AuthSession)) == 0
        assert session.scalar(select(func.count()).select_from(LoginAttempt)) == 3


def test_lockout_at_five_failures_and_expiry(login_fixture: LoginFixture) -> None:
    f = login_fixture
    for _ in range(5):
        assert post(f, password="wrong").status_code == 401
        f.clock.advance()
    locked = post(f)
    assert locked.status_code == 423
    assert int(locked.headers["retry-after"]) > 0
    with f.database.sessions() as session:
        lock = session.scalars(select(LoginLockout)).one()
        assert lock.attempt_count == 5
    f.clock.advance(901)
    assert post(f).status_code == 200


def test_success_resets_failure_window(login_fixture: LoginFixture) -> None:
    f = login_fixture
    for _ in range(4):
        assert post(f, password="wrong").status_code == 401
        f.clock.advance()
    assert post(f).status_code == 200
    f.clock.advance()
    assert post(f, password="wrong").status_code == 401
    f.clock.advance()
    assert post(f).status_code == 200


def test_success_resets_failures_even_when_timestamps_are_equal(
    login_fixture: LoginFixture,
) -> None:
    f = login_fixture
    for _ in range(4):
        assert post(f, password="wrong").status_code == 401
    assert post(f).status_code == 200
    assert post(f, password="wrong").status_code == 401
    assert post(f).status_code == 200


def test_twenty_parallel_failures_do_not_lose_the_lockout(login_fixture: LoginFixture) -> None:
    f = login_fixture
    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(lambda _: post(f, password="wrong").status_code, range(20)))
    assert statuses.count(401) == 5
    assert statuses.count(423) == 15
    with f.database.sessions() as session:
        assert session.scalars(select(LoginLockout)).one().attempt_count == 5
        assert session.scalar(select(func.count()).select_from(AuthSession)) == 0
        assert session.scalar(select(func.count()).select_from(LoginAttempt)) == 20


def test_signer_failure_creates_no_session_or_cookie(login_fixture: LoginFixture) -> None:
    f = login_fixture
    f.signer.failure = True
    response = post(f)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AUTH_UNAVAILABLE"
    assert "private-key" not in response.text
    assert "set-cookie" not in response.headers
    with f.database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(AuthSession)) == 0
        assert session.scalar(select(func.count()).select_from(AuthEvent)) == 0
        assert session.scalars(select(LoginAttempt)).one().failure_code == "AUTH_UNAVAILABLE"
        assert session.get(MerchantAdmin, "adm_test").last_login_at is None


def test_database_failure_rolls_back_session_attempt_success_and_admin_update(
    login_fixture: LoginFixture,
) -> None:
    f = login_fixture

    def reject_audit(mapper, connection, target) -> None:
        raise RuntimeError("private audit-storage failure")

    event.listen(AuthEvent, "before_insert", reject_audit)
    try:
        response = post(f)
    finally:
        event.remove(AuthEvent, "before_insert", reject_audit)
    assert response.status_code == 503
    assert "private" not in response.text
    assert "set-cookie" not in response.headers
    with f.database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(AuthSession)) == 0
        assert session.scalar(select(func.count()).select_from(AuthEvent)) == 0
        assert session.get(MerchantAdmin, "adm_test").last_login_at is None
        attempt = session.scalars(select(LoginAttempt)).one()
        assert attempt.succeeded is False
        assert attempt.failure_code == "PENDING"
    assert post(f).status_code == 200


def test_account_is_rechecked_after_password_and_signing(login_fixture: LoginFixture) -> None:
    f = login_fixture

    def disable() -> None:
        with f.database.sessions.begin() as session:
            session.get(MerchantAdmin, "adm_test").disabled_at = f.clock()

    f.signer.on_sign = disable
    response = post(f)
    assert response.status_code == 401
    with f.database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(AuthSession)) == 0


@pytest.mark.parametrize("change", ["merchant", "email", "password"])
def test_identity_changes_during_signing_cannot_create_a_session(
    login_fixture: LoginFixture, change: str
) -> None:
    f = login_fixture

    def change_identity() -> None:
        with f.database.sessions.begin() as session:
            admin = session.get(MerchantAdmin, "adm_test")
            if change == "merchant":
                session.add(Merchant(id="mch_other", name="Other merchant"))
                session.flush()
                admin.merchant_id = "mch_other"
            elif change == "email":
                admin.email_normalized = "different@shop.example"
            else:
                admin.password_hash = f.hasher.hash("new-password")

    f.signer.on_sign = change_identity
    assert post(f).status_code == 401
    with f.database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(AuthSession)) == 0


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"email": "bad", "password": "secret"},
        {"email": EMAIL, "password": ""},
        {"email": EMAIL, "password": "p", "extra": "private-input"},
    ],
)
def test_request_validation_does_not_echo_passwords(
    login_fixture: LoginFixture, body: dict
) -> None:
    response = login_fixture.http.post(URL, json=body)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"
    assert "private-input" not in response.text and "secret" not in response.text


def test_openapi_includes_login_without_a_bearer_requirement(login_fixture: LoginFixture) -> None:
    document = login_fixture.http.get("/openapi.json").json()
    route = document["paths"][URL]["post"]
    assert route.get("security", []) == []
    assert {"200", "400", "401", "423", "429", "503"} <= set(route["responses"])
    assert set(document["components"]["schemas"]["LoginRequest"]["properties"]) == {
        "email",
        "password",
    }
    assert document["components"]["schemas"]["LoginRequest"]["additionalProperties"] is False


def test_each_parallel_success_gets_its_own_session(login_fixture: LoginFixture) -> None:
    f = login_fixture
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: post(f), range(20)))
    assert [r.status_code for r in responses] == [200] * 20
    with f.database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(AuthSession)) == 20
        assert (
            session.scalar(select(func.count(func.distinct(AuthSession.refresh_token_hash)))) == 20
        )
        assert session.scalar(select(func.count()).select_from(AuthEvent)) == 20


def test_rate_limit_uses_peer_ip_and_ignores_spoofed_forwarded_headers(
    login_fixture: LoginFixture,
) -> None:
    f = login_fixture
    app = create_app(
        ApiSettings(
            database_url=f.database.engine.url.render_as_string(hide_password=False), APP_ENV="test"
        ),
        auth=AuthDependencies(
            Argon2PasswordVerifier(f.hasher), f.signer, LoginPolicy(ip_limit=2), f.clock
        ),
    )
    with TestClient(app) as http:
        for n in range(2):
            response = http.post(
                URL,
                json={"email": f"unknown{n}@shop.example", "password": "wrong"},
                headers={"X-Forwarded-For": f"10.0.0.{n}"},
            )
            assert response.status_code == 401
        limited = http.post(
            URL,
            json={"email": "new@shop.example", "password": "wrong"},
            headers={"X-Forwarded-For": "10.0.0.100"},
        )
        assert limited.status_code == 429
        assert limited.headers["retry-after"] == "60"
        f.clock.advance(61)
        assert (
            http.post(URL, json={"email": "new@shop.example", "password": "wrong"}).status_code
            == 401
        )


def test_default_signer_remains_unavailable_without_creating_a_session(
    login_fixture: LoginFixture,
) -> None:
    f = login_fixture
    with f.database.sessions.begin() as session:
        session.get(MerchantAdmin, "adm_test").password_hash = PasswordHasher(
            time_cost=3, memory_cost=65536, parallelism=4
        ).hash(PASSWORD)
    app = create_app(
        ApiSettings(
            database_url=f.database.engine.url.render_as_string(hide_password=False), APP_ENV="test"
        )
    )
    with TestClient(app) as http:
        response = http.post(URL, json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AUTH_UNAVAILABLE"
    assert "set-cookie" not in response.headers
    with f.database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(AuthSession)) == 0
