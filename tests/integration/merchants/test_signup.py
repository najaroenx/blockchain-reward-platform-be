import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import event, func, insert, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError

from marketplace.idempotency import fingerprint
from marketplace.infrastructure.database import Database
from marketplace.infrastructure.operation_models import (
    IdempotencyKey,
    Operation,
    OperationEvent,
    OperationPlanStep,
    OperationStep,
    Outbox,
)
from marketplace.main import create_app
from marketplace.merchants.infrastructure.models import Merchant, MerchantAdmin, Wallet
from marketplace.merchants.infrastructure.wallets import MockWalletProvider
from marketplace.settings import ApiSettings

URL = "/v1/merchants"
HEADERS = {"Idempotency-Key": "signup-test-key-1"}
PASSWORD = "strong-fixture-password"
BODY = {
    "name": " ร้านทดสอบ ",
    "admin": {"email": " Owner@Example.com ", "displayName": " Owner ", "password": PASSWORD},
}
MODELS = (
    Merchant,
    MerchantAdmin,
    Wallet,
    Operation,
    OperationPlanStep,
    OperationEvent,
    Outbox,
    IdempotencyKey,
    OperationStep,
)


@pytest.fixture
def signup_db(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    postgres = os.environ.get("SIGNUP_TEST_DATABASE_URL")
    if not postgres:
        yield request.getfixturevalue("database_url")
        return

    admin = Database(postgres)
    schema = f"test_signup_{uuid4().hex}"
    isolated = make_url(postgres).update_query_dict({"options": f"-csearch_path={schema}"})
    try:
        with admin.engine.begin() as connection:
            connection.exec_driver_sql(f"CREATE SCHEMA {schema}")
        url = isolated.render_as_string(hide_password=False)
        monkeypatch.setenv("DATABASE_URL", url)
        monkeypatch.setenv("APP_ENV", "test")
        command.upgrade(Config("alembic.ini"), "head")
        yield url
    finally:
        with admin.engine.begin() as connection:
            connection.exec_driver_sql(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        admin.close()


@pytest.fixture
def signup(signup_db: str) -> Iterator[TestClient]:
    settings = ApiSettings(
        APP_ENV="test", database_url=signup_db, mock_merchant_wallet_enabled=True
    )
    with TestClient(create_app(settings)) as http:
        yield http


def counts(http: TestClient) -> tuple[int, ...]:
    with http.app.state.database.sessions() as session:
        return tuple(session.scalar(select(func.count()).select_from(model)) for model in MODELS)


def test_signup_commits_provision_and_replays_original_response(
    signup: TestClient, signup_db: str
) -> None:
    first = signup.post(URL, json=BODY, headers=HEADERS)
    assert first.status_code == 201
    value = first.json()
    assert value["status"] == value["provisioning"]["status"] == "PENDING"
    assert value["name"] == "ร้านทดสอบ"
    assert value["admin"]["email"] == "Owner@Example.com"
    assert value["wallet"]["ready"] is False
    assert first.headers["location"] == f"{URL}/{value['merchantId']}"
    assert first.headers["cache-control"] == "no-store"
    assert "key_ref" not in first.text and PASSWORD not in first.text
    assert counts(signup) == (1, 1, 1, 1, 2, 1, 1, 1, 0)

    with signup.app.state.database.sessions() as session:
        admin = session.scalars(select(MerchantAdmin)).one()
        wallet = session.scalars(select(Wallet)).one()
        operation = session.scalars(select(Operation)).one()
        event_row = session.scalars(select(OperationEvent)).one()
        assert admin.email_normalized == "owner@example.com"
        assert admin.password_hash.startswith("$argon2id$")
        assert PasswordHasher().verify(admin.password_hash, PASSWORD)
        assert wallet.address.hex() == value["wallet"]["address"].removeprefix("0x")
        assert wallet.key_ref.startswith("mock-kms://")
        assert "private_key" not in Wallet.__table__.columns
        assert operation.actor_user_id == value["admin"]["adminId"]
        assert operation.subject_id == wallet.owner_id == value["merchantId"]
        assert operation.payload == {
            "merchantId": wallet.owner_id,
            "walletAddress": value["wallet"]["address"],
        }
        assert (event_row.seq, event_row.from_status, event_row.to_status) == (1, None, "PENDING")
        assert session.scalars(select(Outbox.event_type)).one() == "merchant.provision.v1"
        assert session.scalars(select(IdempotencyKey.fingerprint)).one() == fingerprint(
            {
                "name": "ร้านทดสอบ",
                "email": "owner@example.com",
                "displayName": "Owner",
            }
        )

    settings = ApiSettings(
        APP_ENV="test",
        database_url=signup_db,
        mock_auth_enabled=True,
        mock_auth_user_id=value["admin"]["adminId"],
    )
    with TestClient(create_app(settings)) as reopened:
        replay_body = {**BODY, "admin": {**BODY["admin"], "email": "owner@example.com"}}
        replay = reopened.post(URL, json=replay_body, headers=HEADERS)
        assert replay.status_code == 201 and replay.content == first.content
        assert replay.headers["location"] == first.headers["location"]
        operation_view = reopened.get(
            f"/v1/operations/{value['provisioning']['operationId']}",
            headers={"Authorization": "Bearer mock-redeem-token"},
        )
        assert operation_view.status_code == 200
        assert operation_view.json()["steps"] == [
            {"index": 1, "name": "addToWhitelist", "status": "PENDING", "txHash": None},
            {"index": 2, "name": "setApprovalForAll", "status": "PENDING", "txHash": None},
        ]


def test_conflicts_and_validation_create_no_extra_rows(signup: TestClient) -> None:
    assert signup.post(URL, json=BODY).json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"
    short_password = {**BODY, "admin": {**BODY["admin"], "password": "short"}}
    bad_email = {**BODY, "admin": {**BODY["admin"], "email": "x@localhost"}}
    for invalid in (short_password, bad_email, {**BODY, "wallet": "server-owned"}):
        result = signup.post(URL, json=invalid, headers=HEADERS)
        assert result.status_code == 400 and result.json()["error"]["code"] == "VALIDATION_FAILED"
        assert PASSWORD not in result.text
    assert counts(signup) == (0,) * len(MODELS)
    assert signup.post(URL, json=BODY, headers=HEADERS).status_code == 201
    for different in (
        {**BODY, "name": "Another name"},
        {**BODY, "admin": {**BODY["admin"], "password": "another-fixture-password"}},
    ):
        conflict = signup.post(URL, json=different, headers=HEADERS)
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    duplicate = signup.post(URL, json=BODY, headers={"Idempotency-Key": "separate-signup-key"})
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "EMAIL_TAKEN"
    assert counts(signup) == (1, 1, 1, 1, 2, 1, 1, 1, 0)


def test_twenty_concurrent_replays_create_only_one_signup(signup: TestClient) -> None:
    start = Barrier(20, timeout=10)

    def post(index):
        start.wait()
        return signup.post(URL, json=BODY, headers=HEADERS)

    with ThreadPoolExecutor(max_workers=20) as pool:
        replies = list(pool.map(post, range(20)))
    assert {response.status_code for response in replies} == {201}
    assert len({response.content for response in replies}) == 1
    assert counts(signup) == (1, 1, 1, 1, 2, 1, 1, 1, 0)


def test_outbox_failure_rolls_back_entire_signup(signup: TestClient) -> None:
    engine = signup.app.state.database.engine

    def fail(connection, cursor, statement, parameters, context, executemany):
        if "INSERT INTO outbox" in statement:
            raise OperationalError("insert", {}, RuntimeError("private DB details"))

    event.listen(engine, "before_cursor_execute", fail)
    try:
        result = signup.post(URL, json=BODY, headers=HEADERS)
        assert result.status_code == 503 and "private DB details" not in result.text
        assert counts(signup) == (0,) * len(MODELS)
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert signup.post(URL, json=BODY, headers=HEADERS).status_code == 201
    assert counts(signup) == (1, 1, 1, 1, 2, 1, 1, 1, 0)


def test_database_rejects_unhashed_password_unfinalized_activation_and_invalid_job(
    signup: TestClient,
) -> None:
    created = signup.post(URL, json=BODY, headers=HEADERS).json()
    engine = signup.app.state.database.engine
    merchant_id = created["merchantId"]
    now = datetime.now(UTC)
    for invalid_hash in ("password-in-plaintext", "$2b$12$bcrypt"):
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(update(MerchantAdmin).values(password_hash=invalid_hash))
    for updates in ({"status": "ACTIVE"}, {"status": "ACTIVE", "activated_at": now}):
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(update(Merchant).where(Merchant.id == merchant_id).values(**updates))
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(update(Outbox).values(event_type="merchant.provision"))
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            insert(Operation).values(
                id="op_duplicate",
                actor_user_id=created["admin"]["adminId"],
                kind="MERCHANT_PROVISION",
                status="PENDING",
                subject_id=merchant_id,
                created_at=now,
                payload={},
            )
        )


def test_rate_limit_and_mock_is_blocked_in_production(signup_db: str) -> None:
    settings = ApiSettings(APP_ENV="test", database_url=signup_db, merchant_signup_per_minute=1)
    with TestClient(create_app(settings, merchant_wallets=MockWalletProvider())) as http:
        assert http.post(URL, json=BODY, headers=HEADERS).status_code == 201
        result = http.post(URL, json=BODY, headers=HEADERS)
        assert result.status_code == 429 and result.json()["error"]["code"] == "RATE_LIMITED"
        assert 1 <= int(result.headers["retry-after"]) <= 60
    with pytest.raises(ValidationError, match="mock merchant wallets"):
        ApiSettings(
            APP_ENV="production",
            database_url="postgresql+psycopg://test:test@localhost/test",
            mock_merchant_wallet_enabled=True,
        )


def test_missing_wallet_provider_creates_no_signups(signup_db: str) -> None:
    settings = ApiSettings(APP_ENV="test", database_url=signup_db)
    with TestClient(create_app(settings)) as http:
        result = http.post(URL, json=BODY, headers=HEADERS)
        assert result.status_code == 503 and result.json()["error"]["code"] == "WALLET_UNAVAILABLE"
        assert counts(http) == (0,) * len(MODELS)
