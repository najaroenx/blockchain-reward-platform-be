from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import jwt
import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from marketplace.auth.infrastructure.models import (
    AuthSession,
    LoginAttempt,
    Merchant,
    MerchantAdmin,
)
from marketplace.auth.infrastructure.signing import SigningConfigurationError
from marketplace.main import create_app
from marketplace.settings import ApiSettings, JwtSigningSettings

EMAIL = "owner@shop.example"
PASSWORD = "jwt-integration-password"
JWKS = "/.well-known/jwks.json"


@dataclass
class JwtFixture:
    http: TestClient
    settings: JwtSigningSettings


@pytest.fixture
def jwt_app(database_url: str, jwt_settings: JwtSigningSettings) -> Iterator[JwtFixture]:
    app = create_app(
        ApiSettings(database_url=database_url, APP_ENV="test", jwt_signing=jwt_settings)
    )
    with TestClient(app, base_url="https://testserver") as http:
        with app.state.database.sessions.begin() as session:
            session.add(Merchant(id="mch_jwt", name="JWT merchant"))
            session.flush()
            session.add(
                MerchantAdmin(
                    id="adm_jwt",
                    merchant_id="mch_jwt",
                    email=EMAIL,
                    email_normalized=EMAIL,
                    password_hash=PasswordHasher(
                        time_cost=3, memory_cost=65536, parallelism=4
                    ).hash(PASSWORD),
                    display_name="Owner",
                )
            )
        yield JwtFixture(http, jwt_settings)


def verify(http: TestClient, token: str, settings: JwtSigningSettings) -> dict:
    response = http.get(JWKS)
    assert response.status_code == 200
    header = jwt.get_unverified_header(token)
    public = next(key for key in response.json()["keys"] if key["kid"] == header["kid"])
    return jwt.decode(
        token,
        jwt.PyJWK.from_dict(public).key,
        algorithms=["ES256"],
        issuer=settings.issuer,
        audience=settings.audience,
    )


def test_login_returns_real_jwt_verifiable_from_http_jwks(jwt_app: JwtFixture) -> None:
    f = jwt_app
    response = f.http.post("/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 200
    token = response.json()["accessToken"]
    decoded = verify(f.http, token, f.settings)
    assert decoded["sub"] == "adm_jwt" and decoded["mid"] == "mch_jwt"
    assert decoded["exp"] - decoded["iat"] == 900
    assert not {"role", "permission", "refreshToken"} & decoded.keys()
    assert "HttpOnly" in response.headers["set-cookie"]
    with f.http.app.state.database.sessions() as session:
        stored = session.scalars(select(AuthSession)).one()
        assert stored.id == decoded["sid"]


def test_jwks_is_public_contains_only_public_fields_and_does_not_reserve_attempt(
    jwt_app: JwtFixture,
) -> None:
    response = jwt_app.http.get(JWKS)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=60"
    assert "set-cookie" not in response.headers
    key = response.json()["keys"][0]
    assert set(key) == {"kid", "kty", "crv", "alg", "use", "x", "y"}
    assert key["alg"] == "ES256" and key["crv"] == "P-256"
    assert "PRIVATE KEY" not in response.text
    assert str(jwt_app.settings.private_key_file.get_secret_value()) not in response.text
    with jwt_app.http.app.state.database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(LoginAttempt)) == 0


def test_real_signer_failure_returns_503_without_session(
    jwt_app: JwtFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args, **kwargs):
        raise jwt.InvalidKeyError("SECRET signing failure")

    monkeypatch.setattr(jwt, "encode", fail)
    response = jwt_app.http.post("/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AUTH_UNAVAILABLE"
    assert "SECRET" not in response.text and "set-cookie" not in response.headers
    with jwt_app.http.app.state.database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(AuthSession)) == 0


def test_twenty_parallel_logins_are_signed_and_have_distinct_sessions(jwt_app: JwtFixture) -> None:
    f = jwt_app
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(
            pool.map(
                lambda _: f.http.post(
                    "/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
                ),
                range(20),
            )
        )
    assert [response.status_code for response in responses] == [200] * 20
    decoded = [verify(f.http, r.json()["accessToken"], f.settings) for r in responses]
    assert len({c["jti"] for c in decoded}) == 20
    assert len({c["sid"] for c in decoded}) == 20


def test_jwks_without_signing_configuration_is_503(client: TestClient) -> None:
    response = client.get(JWKS)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AUTH_UNAVAILABLE"
    assert response.headers["cache-control"] == "no-store"


def test_invalid_secret_fails_startup_without_echoing_input(
    database_url: str, jwt_settings: JwtSigningSettings
) -> None:
    Path(jwt_settings.private_key_file.get_secret_value()).write_text("SECRET not a private key")
    app = create_app(
        ApiSettings(database_url=database_url, APP_ENV="test", jwt_signing=jwt_settings)
    )
    with pytest.raises(SigningConfigurationError) as result, TestClient(app):
        pass
    assert "SECRET" not in str(result.value)


def test_jwks_openapi_is_public_and_documents_unavailability(jwt_app: JwtFixture) -> None:
    route = jwt_app.http.get("/openapi.json").json()["paths"][JWKS]["get"]
    assert route.get("security", []) == []
    assert {"200", "503"} <= route["responses"].keys()
