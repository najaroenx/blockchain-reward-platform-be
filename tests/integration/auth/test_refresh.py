from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from http.cookies import SimpleCookie
from secrets import token_urlsafe
from threading import Event

import jwt
import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select

from marketplace.auth.application.dto import AccessClaims, CustomerAccessClaims
from marketplace.auth.domain.entities import LoginPolicy
from marketplace.auth.infrastructure.models import (
    AuthEvent,
    AuthSession,
    Customer,
    Merchant,
    MerchantAdmin,
)
from marketplace.auth.infrastructure.passwords import Argon2PasswordVerifier
from marketplace.auth.infrastructure.signing import Es256TokenSigner
from marketplace.auth.presentation.router import AuthDependencies
from marketplace.infrastructure.database import Database
from marketplace.main import create_app
from marketplace.settings import ApiSettings, JwtSigningSettings

URL = "/v1/auth/refresh"
EMAIL = "owner@shop.example"
PASSWORD = "refresh-test-password"


class Clock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


class HookSigner:
    def __init__(self, signer: Es256TokenSigner) -> None:
        self.signer = signer
        self.on_sign: Callable[[], None] | None = None
        self.failure = ""

    def sign(self, claims: AccessClaims | CustomerAccessClaims) -> str:
        if self.on_sign:
            self.on_sign()
        if self.failure == "exception":
            raise RuntimeError("SECRET signing failure")
        if self.failure == "empty":
            return ""
        return self.signer.sign(claims)


@dataclass
class RefreshFixture:
    http: TestClient
    db: Database
    clock: Clock
    signer: HookSigner
    jwt: JwtSigningSettings


@pytest.fixture
def refresh_app(database_url: str, jwt_settings: JwtSigningSettings) -> Iterator[RefreshFixture]:
    from pathlib import Path

    clock = Clock()
    real_signer = Es256TokenSigner(
        Path(jwt_settings.private_key_file.get_secret_value()),
        issuer=jwt_settings.issuer,
        audience=jwt_settings.audience,
    )
    signer = HookSigner(real_signer)
    verifier = Argon2PasswordVerifier(PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1))
    app = create_app(
        ApiSettings(database_url=database_url, APP_ENV="test"),
        auth=AuthDependencies(
            verifier, signer, LoginPolicy(ip_limit=100), clock, key_publisher=real_signer
        ),
    )
    with TestClient(app, base_url="https://testserver") as http:
        db = app.state.database
        with db.sessions.begin() as session:
            session.add(Merchant(id="mch", name="Test merchant"))
            session.flush()
            session.add(
                MerchantAdmin(
                    id="adm",
                    merchant_id="mch",
                    email=EMAIL,
                    email_normalized=EMAIL,
                    password_hash=verifier.hasher.hash(PASSWORD),
                    display_name="Owner",
                )
            )
            session.add(Customer(id="cus", tel_hash=sha256(b"fixture-customer").digest()))
        yield RefreshFixture(http, db, clock, signer, jwt_settings)


def cookie(response) -> str:
    value = SimpleCookie()
    value.load(response.headers["set-cookie"])
    return value["refreshToken"].value


def login(f: RefreshFixture) -> str:
    response = f.http.post("/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 200
    return cookie(response)


def refresh(f: RefreshFixture, token: str):
    return f.http.post(URL, headers={"Cookie": "refreshToken=" + token})


def decode(f: RefreshFixture, response) -> dict:
    public = f.http.get("/.well-known/jwks.json").json()["keys"][0]
    return jwt.decode(
        response.json()["accessToken"],
        jwt.PyJWK.from_dict(public).key,
        algorithms=["ES256"],
        issuer=f.jwt.issuer,
        audience=f.jwt.audience,
    )


def test_rotation_keeps_family_and_returns_real_jwt_with_merchant_binding(
    refresh_app: RefreshFixture,
) -> None:
    f = refresh_app
    root_token = login(f)
    first = refresh(f, root_token)
    assert first.status_code == 200
    child_token = cookie(first)
    assert child_token != root_token
    assert set(first.json()) == {"accessToken", "tokenType", "expiresIn"}
    assert first.json()["expiresIn"] == 900 and first.json()["tokenType"] == "Bearer"
    assert root_token not in first.text and child_token not in first.text
    assert first.headers["cache-control"] == "no-store"
    assert all(
        flag in first.headers["set-cookie"]
        for flag in ["Secure", "HttpOnly", "SameSite=strict", "Path=/v1/auth", "Max-Age=2592000"]
    )
    claims = decode(f, first)
    assert claims["mid"] == "mch" and claims["sub"] == "adm"
    assert claims["exp"] - claims["iat"] == 900
    with f.db.sessions() as session:
        root = session.scalars(
            select(AuthSession).where(
                AuthSession.refresh_token_hash == sha256(root_token.encode()).digest()
            )
        ).one()
        child = session.scalars(select(AuthSession).where(AuthSession.parent_id == root.id)).one()
        assert root.used_at is not None and root.revoked_reason == "ROTATED"
        assert child.family_id == root.family_id and child.id == claims["sid"]
        assert child.refresh_token_hash == sha256(child_token.encode()).digest()
        assert (child.expires_at - child.created_at).total_seconds() == 30 * 86400
        assert child.revoked_at is None
        assert (
            session.scalar(
                select(func.count()).select_from(AuthEvent).where(AuthEvent.kind == "REFRESH")
            )
            == 1
        )
    second = refresh(f, child_token)
    assert second.status_code == 200
    assert decode(f, second)["jti"] != claims["jti"]


def test_reuse_revokes_all_descendants_but_preserves_other_families(
    refresh_app: RefreshFixture,
) -> None:
    f = refresh_app
    root = login(f)
    other = login(f)
    child = cookie(refresh(f, root))
    grandchild = cookie(refresh(f, child))
    reused = refresh(f, root)
    assert reused.status_code == 401 and reused.json()["error"]["code"] == "SESSION_COMPROMISED"
    assert "Max-Age=0" in reused.headers["set-cookie"]
    assert refresh(f, grandchild).json()["error"]["code"] == "REFRESH_TOKEN_REVOKED"
    assert refresh(f, other).status_code == 200
    with f.db.sessions() as session:
        event_row = session.scalars(
            select(AuthEvent).where(AuthEvent.kind == "REUSE_DETECTED")
        ).one()
        family = event_row.metadata_["familyId"]
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuthSession)
                .where(AuthSession.family_id == family, AuthSession.revoked_at.is_(None))
            )
            == 0
        )
        assert root not in str(event_row.metadata_) and child not in str(event_row.metadata_)


def test_twenty_parallel_reuses_create_one_child_and_revoke_the_family(
    refresh_app: RefreshFixture,
) -> None:
    f = refresh_app
    root = login(f)
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: refresh(f, root), range(20)))
    assert [r.status_code for r in responses].count(200) == 1
    errors = [r.json()["error"]["code"] for r in responses if r.status_code != 200]
    assert errors == ["SESSION_COMPROMISED"] * 19
    with f.db.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuthSession)
                .where(AuthSession.parent_id.is_not(None))
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuthSession)
                .where(AuthSession.revoked_at.is_(None))
            )
            == 0
        )


def test_replay_while_a_descendant_is_signing_prevents_another_child(
    refresh_app: RefreshFixture,
) -> None:
    f = refresh_app
    root = login(f)
    child = cookie(refresh(f, root))
    entered = Event()
    release = Event()

    def block() -> None:
        entered.set()
        assert release.wait(10)

    f.signer.on_sign = block
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(refresh, f, child)
        try:
            assert entered.wait(10)
            assert refresh(f, root).json()["error"]["code"] == "SESSION_COMPROMISED"
        finally:
            release.set()
        response = pending.result(timeout=10)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "REFRESH_TOKEN_REVOKED"
    with f.db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(AuthSession)) == 2
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuthSession)
                .where(AuthSession.revoked_at.is_(None))
            )
            == 0
        )


@pytest.mark.parametrize("token", [None, "", "bad", "a" * 44, "a" * 43])
def test_missing_malformed_or_unknown_cookie_is_invalid(
    refresh_app: RefreshFixture, token: str | None
) -> None:
    f = refresh_app
    headers = {} if token is None else {"Cookie": "refreshToken=" + token}
    response = f.http.post(URL, headers=headers, json={"refreshToken": "body-is-not-a-cookie"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "REFRESH_TOKEN_INVALID"
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert "body-is-not-a-cookie" not in response.text


@pytest.mark.parametrize("state", ["expired", "revoked"])
def test_expired_and_revoked_tokens_have_distinct_errors(
    refresh_app: RefreshFixture, state: str
) -> None:
    f = refresh_app
    token = login(f)
    if state == "expired":
        f.clock.advance(30 * 86400)
    else:
        with f.db.sessions.begin() as session:
            row = session.scalars(select(AuthSession)).one()
            row.revoked_at = f.clock()
            row.revoked_reason = "LOGOUT"
    response = refresh(f, token)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == (
        "REFRESH_TOKEN_EXPIRED" if state == "expired" else "REFRESH_TOKEN_REVOKED"
    )


@pytest.mark.parametrize("failure", ["exception", "empty"])
def test_signing_failure_does_not_consume_token_or_clear_cookie(
    refresh_app: RefreshFixture, failure: str
) -> None:
    f = refresh_app
    token = login(f)
    f.signer.failure = failure
    response = refresh(f, token)
    assert response.status_code == 503 and response.json()["error"]["code"] == "AUTH_UNAVAILABLE"
    assert "set-cookie" not in response.headers and "SECRET" not in response.text
    with f.db.sessions() as session:
        row = session.scalars(select(AuthSession)).one()
        assert row.used_at is None and row.revoked_at is None
    f.signer.failure = ""
    assert refresh(f, token).status_code == 200


def test_storage_failure_rolls_back_rotation_and_audit(refresh_app: RefreshFixture) -> None:
    f = refresh_app
    token = login(f)

    def fail(mapper, connection, target) -> None:
        if target.kind == "REFRESH":
            raise RuntimeError("SECRET storage error")

    event.listen(AuthEvent, "before_insert", fail)
    try:
        response = refresh(f, token)
    finally:
        event.remove(AuthEvent, "before_insert", fail)
    assert response.status_code == 503 and "SECRET" not in response.text
    assert "set-cookie" not in response.headers
    with f.db.sessions() as session:
        row = session.scalars(select(AuthSession)).one()
        assert row.used_at is None and row.revoked_at is None
        assert (
            session.scalar(
                select(func.count()).select_from(AuthEvent).where(AuthEvent.kind == "REFRESH")
            )
            == 0
        )
    assert refresh(f, token).status_code == 200


def test_replay_audit_failure_does_not_claim_successful_revocation(
    refresh_app: RefreshFixture,
) -> None:
    f = refresh_app
    token = login(f)
    child = cookie(refresh(f, token))

    def fail(mapper, connection, target) -> None:
        if target.kind == "REUSE_DETECTED":
            raise RuntimeError("SECRET audit error")

    event.listen(AuthEvent, "before_insert", fail)
    try:
        response = refresh(f, token)
    finally:
        event.remove(AuthEvent, "before_insert", fail)
    assert response.status_code == 503 and "set-cookie" not in response.headers
    with f.db.sessions() as session:
        row = session.scalars(
            select(AuthSession).where(
                AuthSession.refresh_token_hash == sha256(child.encode()).digest()
            )
        ).one()
        assert row.revoked_at is None
    assert refresh(f, token).json()["error"]["code"] == "SESSION_COMPROMISED"


def test_t14_disabled_owner_refresh_revokes_all_their_families(refresh_app: RefreshFixture) -> None:
    f = refresh_app
    token = login(f)
    login(f)
    with f.db.sessions.begin() as session:
        session.get(MerchantAdmin, "adm").disabled_at = f.clock()
    response = refresh(f, token)
    assert (
        response.status_code == 401 and response.json()["error"]["code"] == "REFRESH_TOKEN_REVOKED"
    )
    with f.db.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuthSession)
                .where(AuthSession.revoked_at.is_(None))
            )
            == 0
        )


@pytest.mark.parametrize("change", ["disabled", "merchant", "expiry", "owner"])
def test_session_and_account_are_rechecked_after_signing(
    refresh_app: RefreshFixture, change: str
) -> None:
    f = refresh_app
    token = login(f)

    def change_state() -> None:
        if change == "expiry":
            f.clock.advance(30 * 86400)
            return
        with f.db.sessions.begin() as session:
            admin = session.get(MerchantAdmin, "adm")
            if change == "disabled":
                admin.disabled_at = f.clock()
            elif change == "owner":
                session.add(
                    MerchantAdmin(
                        id="replacement",
                        merchant_id="mch",
                        email="replacement@shop.example",
                        email_normalized="replacement@shop.example",
                        password_hash=admin.password_hash,
                        display_name="Replacement",
                    )
                )
                session.flush()
                session.scalars(select(AuthSession)).one().merchant_admin_id = "replacement"
            else:
                session.add(Merchant(id="other", name="Other"))
                session.flush()
                admin.merchant_id = "other"

    f.signer.on_sign = change_state
    response = refresh(f, token)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == (
        "REFRESH_TOKEN_EXPIRED" if change == "expiry" else "REFRESH_TOKEN_REVOKED"
    )
    with f.db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(AuthSession)) == 1


def test_unique_parent_violation_from_another_writer_revokes_the_family(
    refresh_app: RefreshFixture,
) -> None:
    f = refresh_app
    token = login(f)

    def add_external_child() -> None:
        with f.db.sessions.begin() as session:
            root = session.scalars(select(AuthSession)).one()
            session.add(
                AuthSession(
                    id="external",
                    subject_kind="MERCHANT_ADMIN",
                    merchant_admin_id="adm",
                    family_id=root.family_id,
                    parent_id=root.id,
                    refresh_token_hash=sha256(b"external").digest(),
                    created_at=f.clock(),
                    expires_at=f.clock() + timedelta(days=30),
                )
            )

    f.signer.on_sign = add_external_child
    response = refresh(f, token)
    assert response.status_code == 401 and response.json()["error"]["code"] == "SESSION_COMPROMISED"
    with f.db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(AuthSession)) == 2
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuthSession)
                .where(AuthSession.revoked_at.is_(None))
            )
            == 0
        )


def test_refresh_hash_collision_rolls_back_without_being_treated_as_parent_reuse(
    refresh_app: RefreshFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = refresh_app
    token = login(f)
    monkeypatch.setattr("marketplace.auth.application.refresh.token_urlsafe", lambda _: token)
    response = refresh(f, token)
    assert response.status_code == 503
    with f.db.sessions() as session:
        root = session.scalars(select(AuthSession)).one()
        assert root.used_at is None and root.revoked_at is None


@pytest.mark.parametrize("disabled", [False, True])
def test_customer_session_refresh_uses_customer_claims_and_fresh_status(
    refresh_app: RefreshFixture, disabled: bool
) -> None:
    f = refresh_app
    token = token_urlsafe(32)
    with f.db.sessions.begin() as session:
        session.add(
            AuthSession(
                id="customer",
                subject_kind="CUSTOMER",
                customer_id="cus",
                family_id="customer-family",
                refresh_token_hash=sha256(token.encode()).digest(),
                created_at=f.clock(),
                expires_at=f.clock() + timedelta(days=30),
            )
        )
        if disabled:
            session.get(Customer, "cus").disabled_at = f.clock()
    response = refresh(f, token)
    if disabled:
        assert (
            response.status_code == 401
            and response.json()["error"]["code"] == "REFRESH_TOKEN_REVOKED"
        )
    else:
        assert response.status_code == 200
        decoded = decode(f, response)
        assert decoded["kind"] == "CUSTOMER" and decoded["sub"] == "cus" and "mid" not in decoded


def test_openapi_requires_cookie_not_bearer_or_json_body(refresh_app: RefreshFixture) -> None:
    route = refresh_app.http.get("/openapi.json").json()["paths"][URL]["post"]
    assert route.get("security", []) == []
    assert "requestBody" not in route
    assert any(p["name"] == "refreshToken" and p["in"] == "cookie" for p in route["parameters"])
    assert {"200", "401", "503"} <= route["responses"].keys()
