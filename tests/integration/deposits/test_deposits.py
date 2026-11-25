import json
import os
from base64 import urlsafe_b64encode
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from threading import Barrier, Lock
from uuid import uuid4

import jwt
import pytest
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import event, func, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError

from marketplace.deposits.application.exceptions import (
    PaymentProviderError,
    PaymentReferenceConflict,
)
from marketplace.deposits.domain.entities.deposit import PaymentInstructions
from marketplace.deposits.infrastructure.models import Deposit
from marketplace.infrastructure.database import Database
from marketplace.infrastructure.operation_models import IdempotencyKey, Operation, Outbox
from marketplace.main import create_app
from marketplace.settings import ApiSettings

URL = "/v1/deposits"
HEADERS = {"Authorization": "Bearer mock-redeem-token", "Idempotency-Key": "deposit-key-1"}
BODY = {"amountSatang": "10050"}


@pytest.fixture
def deposit_db(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    postgres = os.environ.get("DEPOSIT_TEST_DATABASE_URL")
    if not postgres:
        yield request.getfixturevalue("database_url")
        return

    database = Database(postgres)
    schema = f"test_deposit_{uuid4().hex}"
    url = make_url(postgres).update_query_dict({"options": f"-csearch_path={schema}"})
    try:
        with database.engine.begin() as connection:
            connection.exec_driver_sql(f"CREATE SCHEMA {schema}")
        database_url = url.render_as_string(hide_password=False)
        monkeypatch.setenv("APP_ENV", "test")
        monkeypatch.setenv("DATABASE_URL", database_url)
        command.upgrade(Config("alembic.ini"), "head")
        yield database_url
    finally:
        with database.engine.begin() as connection:
            connection.exec_driver_sql(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        database.close()


@pytest.fixture
def deposits(deposit_db: str) -> Iterator[TestClient]:
    with TestClient(mock_app(deposit_db)) as http:
        yield http


def mock_app(database_url: str) -> FastAPI:
    return create_app(
        ApiSettings(
            APP_ENV="test",
            database_url=database_url,
            mock_auth_enabled=True,
            mock_deposit_psp_enabled=True,
        )
    )


def counts(http: TestClient) -> tuple[int, ...]:
    with http.app.state.database.sessions() as session:
        return tuple(
            session.scalar(select(func.count()).select_from(model))
            for model in (Deposit, IdempotencyKey, Operation, Outbox)
        )


def test_create_and_replay_after_restart(deposits: TestClient, deposit_db: str) -> None:
    first = deposits.post(URL, json=BODY, headers=HEADERS)
    assert first.status_code == 201
    created = first.json()
    assert created["amountSatang"] == "10050"
    assert created["amountBase"] == "100500000000000000000"
    assert created["status"] == "PENDING_PAYMENT"
    assert created["paymentUrl"].startswith("https://payments.invalid/deposits/")
    assert datetime.fromisoformat(created["expiresAt"]) > datetime.now(UTC)

    with TestClient(mock_app(deposit_db)) as reopened:
        replay = reopened.post(URL, json=BODY, headers=HEADERS)
        assert replay.status_code == 200 and replay.content == first.content
        assert reopened.post(URL, json={"amountSatang": "2000"}, headers=HEADERS).status_code == 409
        detail = reopened.get(f"{URL}/{created['depositId']}", headers=HEADERS)
        assert detail.status_code == 200
        assert detail.json()["status"] == "PENDING_PAYMENT"
        assert all(detail.json()[field] is None for field in ["confirmedAt", "mintedAt", "mint"])
        assert counts(reopened) == (1, 1, 0, 0)
        with reopened.app.state.database.sessions() as session:
            record = session.get_one(Deposit, created["depositId"])
            assert (record.holder_id, record.amount_satang) == ("mock-user", 10050)
            assert record.provider == "mock"
            assert record.provider_ref == created["paymentUrl"].rsplit("/", 1)[-1]


@pytest.mark.parametrize("amount", ["2000", "5000000"])
def test_limits_are_inclusive(deposits: TestClient, amount: str) -> None:
    result = deposits.post(URL, json={"amountSatang": amount}, headers=HEADERS)
    assert result.status_code == 201
    assert result.json()["amountBase"] == amount + "0" * 16


@pytest.mark.parametrize("amount", ["1999", "5000001", "010050", "2000\n", 2000, "20.00", "-2000"])
def test_invalid_amounts_leave_no_rows(deposits: TestClient, amount: object) -> None:
    result = deposits.post(URL, json={"amountSatang": amount}, headers=HEADERS)
    assert result.status_code == 400
    assert result.json()["error"]["code"] == "VALIDATION_FAILED"
    assert counts(deposits) == (0, 0, 0, 0)


def test_missing_key_bad_auth_and_forged_owner_are_rejected(deposits: TestClient) -> None:
    missing = deposits.post(URL, json=BODY, headers={"Authorization": HEADERS["Authorization"]})
    assert (missing.status_code, missing.json()["error"]["code"]) == (
        400,
        "IDEMPOTENCY_KEY_REQUIRED",
    )
    invalid = deposits.post(URL, json=BODY, headers={**HEADERS, "Authorization": "Bearer nope"})
    assert invalid.status_code == 401 and invalid.headers["www-authenticate"] == "Bearer"
    forged = deposits.post(URL, json={**BODY, "holderId": "other-merchant"}, headers=HEADERS)
    assert forged.status_code == 400
    assert counts(deposits) == (0, 0, 0, 0)


def test_verified_jwt_scopes_read_and_replay_to_merchant(deposit_db: str, tmp_path: Path) -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key()
    public_file = tmp_path / "public.pem"
    public_file.write_bytes(
        public.public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(public))
    canonical = json.dumps(
        {name: jwk[name] for name in ("crv", "kty", "x", "y")},
        sort_keys=True,
        separators=(",", ":"),
    )
    kid = urlsafe_b64encode(sha256(canonical.encode()).digest()).rstrip(b"=").decode()
    issuer, audience = "deposit-issuer", "deposit-api"
    now = datetime.now(UTC)

    def headers(mid: str, sub: str, expiry: datetime) -> dict[str, str]:
        token = jwt.encode(
            {
                "sub": sub,
                "mid": mid,
                "sid": "session",
                "jti": "token",
                "kind": "MERCHANT_ADMIN",
                "iat": int((now - timedelta(minutes=2)).timestamp()),
                "exp": int(expiry.timestamp()),
                "iss": issuer,
                "aud": audience,
            },
            key,
            algorithm="ES256",
            headers={"kid": kid},
        )
        return {**HEADERS, "Authorization": f"Bearer {token}"}

    app = create_app(
        ApiSettings(
            APP_ENV="test",
            database_url=deposit_db,
            jwt_public_key_file=public_file,
            jwt_issuer=issuer,
            jwt_audience=audience,
            mock_deposit_psp_enabled=True,
        )
    )
    valid_until = now + timedelta(minutes=2)
    owner = headers("merchant-a", "admin-a", valid_until)
    same_merchant = headers("merchant-a", "admin-b", valid_until)
    other = headers("merchant-b", "admin-c", valid_until)
    expired = headers("merchant-a", "admin-a", now - timedelta(minutes=1))
    with TestClient(app) as http:
        assert http.post(URL, json=BODY, headers=expired).status_code == 401
        created = http.post(URL, json=BODY, headers=owner)
        assert created.status_code == 201
        url = f"{URL}/{created.json()['depositId']}"
        assert http.get(url, headers=same_merchant).status_code == 200
        assert (
            http.get(url, headers=other).json()
            == http.get(f"{URL}/dep_nonexistent", headers=other).json()
        )
        assert http.get(url, headers=other).status_code == 404
        assert http.post(URL, json=BODY, headers=same_merchant).content == created.content
        second = http.post(URL, json=BODY, headers=other)
        assert second.status_code == 201
        assert second.json()["depositId"] != created.json()["depositId"]
        assert counts(http) == (2, 2, 0, 0)


class IdempotentProvider:
    name = "fixture"

    def __init__(self, contenders: int = 1) -> None:
        self.barrier = Barrier(contenders, timeout=10)
        self.lock = Lock()
        self.intents: dict[str, tuple[int, PaymentInstructions]] = {}

    def prepare(self, reference: str, amount_satang: int) -> PaymentInstructions:
        self.barrier.wait()
        with self.lock:
            if reference not in self.intents:
                self.intents[reference] = (
                    amount_satang,
                    PaymentInstructions(
                        reference=f"provider_{reference}",
                        url=f"https://checkout.invalid/{reference}",
                        expires_at=datetime.now(UTC) + timedelta(hours=24),
                    ),
                )
            amount, instructions = self.intents[reference]
            if amount != amount_satang:
                raise PaymentReferenceConflict
            return instructions


def test_20_parallel_requests_create_one_deposit_and_payment_intent(deposit_db: str) -> None:
    provider = IdempotentProvider(20)
    app = create_app(
        ApiSettings(APP_ENV="test", database_url=deposit_db, mock_auth_enabled=True),
        deposit_provider=provider,
    )
    with TestClient(app) as http, ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(lambda _: http.post(URL, json=BODY, headers=HEADERS), range(20)))
        assert sorted(result.status_code for result in results) == [200] * 19 + [201]
        assert len({result.content for result in results}) == 1
        assert len(provider.intents) == 1
        assert counts(http) == (1, 1, 0, 0)


def test_commit_failure_and_retry_reuse_provider_reference(deposit_db: str) -> None:
    provider = IdempotentProvider()
    app = create_app(
        ApiSettings(APP_ENV="test", database_url=deposit_db, mock_auth_enabled=True),
        deposit_provider=provider,
    )

    def fail_commit(connection):
        raise OperationalError("commit", {}, RuntimeError("private DB details"))

    with TestClient(app) as http:
        engine = http.app.state.database.engine
        event.listen(engine, "commit", fail_commit)
        try:
            failed = http.post(URL, json=BODY, headers=HEADERS)
            assert failed.status_code == 503
            assert failed.json()["error"]["code"] == "DEPOSIT_UNAVAILABLE"
            assert "private DB details" not in failed.text
        finally:
            event.remove(engine, "commit", fail_commit)
        assert counts(http) == (0, 0, 0, 0)
        assert http.post(URL, json={"amountSatang": "2000"}, headers=HEADERS).status_code == 409
        retry = http.post(URL, json=BODY, headers=HEADERS)
        assert retry.status_code == 201
        assert len(provider.intents) == 1
        assert counts(http) == (1, 1, 0, 0)


def test_database_rejects_inexact_amount_and_unverified_confirmation(deposits: TestClient) -> None:
    created = deposits.post(URL, json=BODY, headers=HEADERS).json()
    sessions = deposits.app.state.database.sessions
    for invalid in ({"amount_base": "100500000000000000001"}, {"status": "CONFIRMED"}):
        with pytest.raises(IntegrityError), sessions.begin() as session:
            session.execute(
                update(Deposit).where(Deposit.id == created["depositId"]).values(**invalid)
            )
    with sessions.begin() as session:
        session.execute(
            update(Deposit)
            .where(Deposit.id == created["depositId"])
            .values(
                created_at=datetime(2000, 1, 1, tzinfo=UTC),
                expires_at=datetime(2000, 1, 2, tzinfo=UTC),
            )
        )
    assert (
        deposits.get(f"{URL}/{created['depositId']}", headers=HEADERS).json()["status"] == "EXPIRED"
    )
    assert deposits.post(URL, json=BODY, headers=HEADERS).json()["status"] == "PENDING_PAYMENT"


def test_psp_failure_creates_no_rows(deposit_db: str) -> None:
    class OfflineProvider:
        name = "offline"

        def prepare(self, reference: str, amount_satang: int) -> PaymentInstructions:
            raise PaymentProviderError("private provider response")

    settings = ApiSettings(APP_ENV="test", database_url=deposit_db, mock_auth_enabled=True)
    for provider in [None, OfflineProvider()]:
        with TestClient(create_app(settings, deposit_provider=provider)) as http:
            failed = http.post(URL, json=BODY, headers=HEADERS)
            assert (failed.status_code, failed.json()["error"]["code"]) == (
                503,
                "PAYMENT_UNAVAILABLE",
            )
            assert "private provider response" not in failed.text
            assert counts(http) == (0, 0, 0, 0)


def test_mock_psp_is_rejected_in_production() -> None:
    with pytest.raises(ValidationError, match="mock deposit payments"):
        ApiSettings(
            APP_ENV="production",
            database_url="postgresql+psycopg://test:test@localhost/test",
            mock_deposit_psp_enabled=True,
        )
