"""The router built and tested standalone — not through `marketplace.main.create_app()`.

It is not wired into the live app this round: there is no production `TokenSigner`/`TokenVerifier`
(KMS-backed signing is a deferred decision, see docs/architecture.md). This proves the HTTP shape
is correct against fakes, so wiring it in later is a `main.py` change, not more feature work.
"""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from auth.application.use_cases import AuthService
from auth.infrastructure.hashing import Argon2PasswordHasher
from auth.infrastructure.models import MerchantAdmin
from auth.infrastructure.repository import (
    SqlAdminAccountReader,
    SqlAuthSessionStore,
    SqlLoginAttemptTracker,
)
from auth.presentation.router import create_router
from marketplace.infrastructure.database import Database

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def client(database_url: str, signer, verifier, seed_merchant) -> Iterator[TestClient]:
    database = Database(database_url)
    with database.sessions.begin() as session:
        seed_merchant(session)
        session.add(
            MerchantAdmin(
                id="adm_1",
                merchant_id="mch_1",
                email="owner@shop.co.th",
                email_normalized="owner@shop.co.th",
                password_hash=Argon2PasswordHasher().hash(PASSWORD),
                display_name="Owner",
            )
        )

    def provide_service(request: Request) -> Iterator[AuthService]:
        with database.sessions.begin() as session:
            yield AuthService(
                SqlAdminAccountReader(session),
                SqlAuthSessionStore(session),
                SqlLoginAttemptTracker(session),
                Argon2PasswordHasher(),
                signer,
            )

    def provide_verifier(request: Request):
        return verifier

    app = FastAPI()
    app.include_router(create_router(provide_service, provide_verifier))
    # The refresh cookie is `Secure` (by design — it must never go out over plain HTTP), so the
    # client needs an https base_url or its cookie jar will silently drop it on the next request.
    with TestClient(app, base_url="https://testserver") as http:
        yield http


def test_login_sets_refresh_cookie_and_returns_access_token(client: TestClient) -> None:
    response = client.post(
        "/v1/auth/login", json={"email": "owner@shop.co.th", "password": PASSWORD}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["subject"]["id"] == "adm_1"
    assert "refresh_token" in response.cookies


def test_login_with_wrong_password_is_401(client: TestClient) -> None:
    response = client.post(
        "/v1/auth/login", json={"email": "owner@shop.co.th", "password": "wrong"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_refresh_without_cookie_is_401(client: TestClient) -> None:
    response = client.post("/v1/auth/refresh")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "REFRESH_TOKEN_INVALID"


def test_refresh_rotates_the_cookie(client: TestClient) -> None:
    login = client.post("/v1/auth/login", json={"email": "owner@shop.co.th", "password": PASSWORD})
    old_cookie = login.cookies["refresh_token"]

    refreshed = client.post("/v1/auth/refresh")
    assert refreshed.status_code == 200
    assert refreshed.cookies["refresh_token"] != old_cookie


def test_sessions_endpoint_requires_a_bearer_token(client: TestClient) -> None:
    assert client.get("/v1/auth/sessions").status_code == 401


def test_sessions_endpoint_lists_the_current_session(client: TestClient) -> None:
    login = client.post("/v1/auth/login", json={"email": "owner@shop.co.th", "password": PASSWORD})
    access_token = login.json()["access_token"]

    response = client.get("/v1/auth/sessions", headers={"Authorization": f"Bearer {access_token}"})
    assert response.status_code == 200
    sessions = response.json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["current"] is True


def test_logout_clears_the_cookie_and_revokes_the_session(client: TestClient) -> None:
    login = client.post("/v1/auth/login", json={"email": "owner@shop.co.th", "password": PASSWORD})
    access_token = login.json()["access_token"]
    old_refresh_token = login.cookies["refresh_token"]

    response = client.post("/v1/auth/logout", headers={"Authorization": f"Bearer {access_token}"})
    assert response.status_code == 204
    assert "refresh_token" not in client.cookies

    # The cookie was cleared client-side, but another device that still holds the old refresh
    # token must also be rejected — that's the actual revocation, not just this client forgetting.
    client.cookies.set("refresh_token", old_refresh_token)
    refreshed = client.post("/v1/auth/refresh")
    assert refreshed.status_code == 401
    assert refreshed.json()["error"]["code"] == "REFRESH_TOKEN_REVOKED"
