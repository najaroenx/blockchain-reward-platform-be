import json
import os
from base64 import urlsafe_b64encode
from collections.abc import Iterator
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from time import time
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import make_url

from marketplace.coupons.application.dto import (
    AuthenticatedWallet,
    CouponDeployment,
    RedeemCouponPayload,
    RedeemCouponResult,
)
from marketplace.coupons.application.use_cases import RedeemCoupons
from marketplace.coupons.infrastructure.jwt_auth import (
    JwtVerificationConfigurationError,
    JwtWalletAuthenticator,
)
from marketplace.coupons.presentation.router import RedeemDependencies
from marketplace.main import create_app
from marketplace.settings import ApiSettings

URL = "/v1/coupons/redeem"
ISSUER = "https://auth.example.test"
AUDIENCE = "reward-api"


@dataclass(frozen=True)
class TokenIssuer:
    key: ec.EllipticCurvePrivateKey
    public_file: Path
    kid: str

    def claims(self) -> dict[str, object]:
        now = int(time())
        return {
            "sub": "auth-user-1",
            "mid": "merchant-1",
            "sid": "session-1",
            "jti": "access-token-1",
            "kind": "MERCHANT_ADMIN",
            "iat": now - 10,
            "exp": now + 300,
            "iss": ISSUER,
            "aud": AUDIENCE,
        }

    def sign(self, claims: dict[str, object] | None = None, **headers: object) -> str:
        return jwt.encode(
            self.claims() if claims is None else claims,
            self.key,
            algorithm="ES256",
            headers={"kid": self.kid, "typ": "JWT", **headers},
        )


@pytest.fixture(scope="module")
def issuer(tmp_path_factory: pytest.TempPathFactory) -> TokenIssuer:
    key = ec.generate_private_key(ec.SECP256R1())
    public_file = tmp_path_factory.mktemp("jwt-public") / "public.pem"
    public_file.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    # #182 uses the RFC 7638 thumbprint of these four public JWK fields as kid.
    public = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(key.public_key()))
    canonical = json.dumps(
        {name: public[name] for name in ("crv", "kty", "x", "y")},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    kid = urlsafe_b64encode(sha256(canonical).digest()).rstrip(b"=").decode("ascii")
    return TokenIssuer(key, public_file, kid)


class AdmissionProbe:
    """Real SQL write probes; not a production operation/outbox implementation."""

    def __init__(self) -> None:
        self.wallet_reads: list[str] = []
        self.deployment_reads: list[int] = []
        self.admissions = 0
        self.engine: Engine
        self.writes: list[str] = []

    def resolve(self, identity: str | int) -> AuthenticatedWallet | CouponDeployment:
        if isinstance(identity, str):
            self.wallet_reads.append(identity)
            return AuthenticatedWallet(user_id=identity, wallet_address="0x" + "a" * 40)
        self.deployment_reads.append(identity)
        return CouponDeployment(chain_id=1337, contract_address="0x" + "b" * 40)

    def admit(
        self, *, user_id: str, idempotency_key: str, payload: RedeemCouponPayload
    ) -> RedeemCouponResult:
        self.admissions += 1
        with self.engine.begin() as connection:
            connection.execute(text("INSERT INTO ac2_operation_probe (id) VALUES ('op_test')"))
            connection.execute(text("INSERT INTO ac2_outbox_probe (id) VALUES ('job_test')"))
        return RedeemCouponResult(operation_id="op_test", type_id=payload.type_id)

    def row_counts(self) -> list[int]:
        with self.engine.connect() as connection:
            return [
                connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
                for table in ("ac2_operation_probe", "ac2_outbox_probe")
            ]


@pytest.fixture
def jwt_database_url(database_url: str) -> Iterator[str]:
    """Use an isolated PostgreSQL schema when a test database is supplied."""
    postgres = os.environ.get("REDEEM_AUTH_TEST_DATABASE_URL")
    if postgres is None:
        yield database_url
        return
    url = make_url(postgres)
    assert url.get_backend_name() == "postgresql"
    schema = f"redeem_auth_{uuid4().hex}"
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA {schema}"))
        yield url.update_query_dict({"options": f"-csearch_path={schema}"}).render_as_string(
            hide_password=False
        )
    finally:
        with engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        engine.dispose()


@pytest.fixture
def jwt_client(
    jwt_database_url: str, issuer: TokenIssuer
) -> Iterator[tuple[TestClient, AdmissionProbe]]:
    probe = AdmissionProbe()
    app = create_app(
        ApiSettings(database_url=jwt_database_url, APP_ENV="test"),
        redeem=RedeemDependencies(
            authenticate=JwtWalletAuthenticator(
                issuer.public_file, issuer=ISSUER, audience=AUDIENCE, wallets=probe
            ),
            redeemer=RedeemCoupons(probe, probe),
        ),
    )
    with TestClient(app) as http:
        probe.engine = app.state.database.engine
        with probe.engine.begin() as connection:
            connection.execute(text("CREATE TABLE ac2_operation_probe (id TEXT PRIMARY KEY)"))
            connection.execute(text("CREATE TABLE ac2_outbox_probe (id TEXT PRIMARY KEY)"))

        @event.listens_for(probe.engine, "before_cursor_execute")
        def record_writes(connection, cursor, statement, parameters, context, executemany):
            if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
                probe.writes.append(statement)

        yield http, probe


def invalid_token(issuer: TokenIssuer, case: str) -> str | None:
    if case == "missing":
        return None
    if case == "malformed":
        return "private-invalid-token"
    if case == "wrong-signature":
        return jwt.encode(
            issuer.claims(),
            ec.generate_private_key(ec.SECP256R1()),
            algorithm="ES256",
            headers={"kid": issuer.kid},
        )
    if case == "unsigned":
        return jwt.encode(issuer.claims(), key=None, algorithm="none", headers={"kid": issuer.kid})
    if case == "hmac":
        return jwt.encode(
            issuer.claims(),
            key="private-test-secret-which-is-at-least-32-bytes",
            algorithm="HS256",
            headers={"kid": issuer.kid},
        )
    if case == "wrong-kid":
        return issuer.sign(kid="untrusted-key")
    if case == "missing-kid":
        return jwt.encode(issuer.claims(), issuer.key, algorithm="ES256")
    if case == "wrong-typ":
        return issuer.sign(typ="refresh+jwt")
    if case == "critical-header":
        return issuer.sign(crit=["unrecognized"])
    claims = issuer.claims()
    if case.startswith("missing-"):
        del claims[case.removeprefix("missing-")]
    else:
        changes: dict[str, dict[str, object]] = {
            "expired": {"iat": int(time()) - 100, "exp": int(time()) - 1},
            "wrong-issuer": {"iss": "https://attacker.example.test"},
            "wrong-audience": {"aud": "other-api"},
            "audience-list": {"aud": [AUDIENCE]},
            "future-iat": {"iat": int(time()) + 100},
            "future-nbf": {"nbf": int(time()) + 100},
            "string-exp": {"exp": str(int(time()) + 300)},
            "float-exp": {"exp": time() + 300},
            "infinite-exp": {"exp": float("inf")},
            "boolean-iat": {"iat": True},
            "reversed-times": {"exp": int(time()) + 100, "iat": int(time()) + 200},
            "empty-sub": {"sub": ""},
            "non-string-sub": {"sub": 1},
            "empty-mid": {"mid": " "},
            "wrong-kind": {"kind": "CUSTOMER"},
        }
        claims.update(changes[case])
    return issuer.sign(claims)


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "malformed",
        "wrong-signature",
        "unsigned",
        "hmac",
        "wrong-kid",
        "missing-kid",
        "wrong-typ",
        "critical-header",
        "expired",
        "wrong-issuer",
        "wrong-audience",
        "audience-list",
        "future-iat",
        "future-nbf",
        "string-exp",
        "float-exp",
        "infinite-exp",
        "boolean-iat",
        "reversed-times",
        "empty-sub",
        "non-string-sub",
        "empty-mid",
        "wrong-kind",
        *[
            f"missing-{name}"
            for name in ("sub", "mid", "sid", "jti", "kind", "iat", "exp", "iss", "aud")
        ],
    ],
)
def test_rejected_access_tokens_are_401_before_wallet_lookup_or_database_writes(
    jwt_client: tuple[TestClient, AdmissionProbe], issuer: TokenIssuer, case: str
) -> None:
    http, probe = jwt_client
    token = invalid_token(issuer, case)
    headers = {"Idempotency-Key": "jwt-rejection-key"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    response = http.post(URL, json={"typeId": "1"}, headers=headers)
    assert response.status_code == 401
    assert response.json() == {
        "error": {"code": "UNAUTHENTICATED", "message": "A valid access token is required."}
    }
    assert response.headers["www-authenticate"] == "Bearer"
    assert probe.wallet_reads == []
    assert probe.deployment_reads == []
    assert probe.admissions == 0
    assert probe.writes == []
    assert probe.row_counts() == [0, 0]
    assert token is None or token not in response.text


def test_valid_signed_access_token_can_reach_admission(
    jwt_client: tuple[TestClient, AdmissionProbe], issuer: TokenIssuer
) -> None:
    http, probe = jwt_client
    response = http.post(
        URL,
        json={"typeId": "1"},
        headers={"Authorization": f"Bearer {issuer.sign()}", "Idempotency-Key": "valid-jwt-key"},
    )
    assert response.status_code == 202
    assert probe.wallet_reads == ["auth-user-1"]
    assert probe.deployment_reads == [1]
    assert probe.admissions == 1
    assert probe.row_counts() == [1, 1]


@pytest.mark.parametrize(
    "case, status", [("missing", 401), ("expired", 401), ("malformed", 401), ("valid", 503)]
)
def test_app_configures_real_verification_without_inventing_a_wallet(
    jwt_database_url: str, issuer: TokenIssuer, case: str, status: int
) -> None:
    app = create_app(
        ApiSettings(
            database_url=jwt_database_url,
            APP_ENV="test",
            jwt_public_key_file=issuer.public_file,
            jwt_issuer=ISSUER,
            jwt_audience=AUDIENCE,
        )
    )
    token = issuer.sign() if case == "valid" else invalid_token(issuer, case)
    headers = {"Idempotency-Key": "configured-jwt-key"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    with TestClient(app) as http:
        response = http.post(URL, json={"typeId": "1"}, headers=headers)
    assert response.status_code == status
    assert "operationId" not in response.text


@pytest.mark.parametrize("key_type", ["missing", "malformed", "private", "rsa", "p384"])
def test_invalid_verification_keys_stop_startup_without_exposing_configuration(
    tmp_path: Path, key_type: str
) -> None:
    path = tmp_path / "private-config-path.pem"
    if key_type == "malformed":
        path.write_text("private-invalid-key-contents")
    elif key_type in {"private", "rsa", "p384"}:
        key = (
            rsa.generate_private_key(public_exponent=65537, key_size=2048)
            if key_type == "rsa"
            else ec.generate_private_key(ec.SECP384R1())
        )
        path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            if key_type == "private"
            else key.public_key().public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        )
    with pytest.raises(JwtVerificationConfigurationError) as result:
        create_app(
            ApiSettings(
                database_url="sqlite:///:memory:",
                jwt_public_key_file=path,
                jwt_issuer=ISSUER,
                jwt_audience=AUDIENCE,
            )
        )
    assert str(result.value) == "JWT verification requires a readable P-256 public key"
    assert str(path) not in str(result.value)
