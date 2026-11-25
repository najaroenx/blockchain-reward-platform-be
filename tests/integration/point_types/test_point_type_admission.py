import json
import os
from base64 import urlsafe_b64encode
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import jwt
import pytest
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from sqlalchemy import Connection, event, insert, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError

from marketplace.idempotency import fingerprint
from marketplace.infrastructure.database import Database
from marketplace.infrastructure.operation_history import advance_operation, record_operation_created
from marketplace.infrastructure.operation_models import (
    IdempotencyKey,
    Operation,
    OperationEvent,
    Outbox,
)
from marketplace.main import create_app
from marketplace.merchants.infrastructure.models import Merchant, MerchantAdmin, Wallet
from marketplace.point_types.infrastructure.models import PointType
from marketplace.settings import ApiSettings

URL = "/v1/point-types"
BODY = {"name": " แต้มสะสมสุขใจ ", "symbol": "SUKJAI", "decimals": 18}
HEADERS = {"Authorization": "Bearer mock-redeem-token", "Idempotency-Key": "point-test-key"}


@pytest.fixture
def admission_db(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    postgres = os.environ.get("POINT_TYPE_TEST_DATABASE_URL")
    if not postgres:
        yield request.getfixturevalue("database_url")
        return

    admin = Database(postgres)
    schema = f"test_point_type_{uuid4().hex}"
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
def http(admission_db: str) -> Iterator[TestClient]:
    settings = ApiSettings(
        APP_ENV="test",
        database_url=admission_db,
        mock_auth_enabled=True,
        point_type_admission_enabled=True,
    )
    with TestClient(create_app(settings)) as client:
        yield client


def seed_merchant(
    connection: Connection,
    merchant_id: str,
    admin_id: str,
    *,
    active: bool = True,
    wallet: bool = True,
) -> None:
    now = datetime.now(UTC)
    connection.execute(insert(Merchant).values(id=merchant_id, name=merchant_id, created_at=now))
    email = f"{admin_id}@example.com"
    connection.execute(
        insert(MerchantAdmin).values(
            id=admin_id,
            merchant_id=merchant_id,
            email=email,
            email_normalized=email,
            password_hash="$argon2id$fixture",
            display_name=admin_id,
            created_at=now,
        )
    )
    if wallet:
        connection.execute(
            insert(Wallet).values(
                id=f"wallet_{merchant_id}",
                address=merchant_id.encode().rjust(20, b"0"),
                key_ref=f"mock-kms://{merchant_id}",
                owner_kind="MERCHANT",
                owner_id=merchant_id,
                created_at=now,
            )
        )
    if active:
        operation_id = f"op_provision_{merchant_id}"
        connection.execute(
            insert(Operation).values(
                id=operation_id,
                kind="MERCHANT_PROVISION",
                actor_user_id=admin_id,
                subject_id=merchant_id,
                status="PENDING",
                payload={},
                created_at=now,
            )
        )
        record_operation_created(connection, operation_id, now)
        advance_operation(connection, operation_id, "SUBMITTED")
        advance_operation(connection, operation_id, "CONFIRMED")
        advance_operation(connection, operation_id, "FINALIZED")
        connection.execute(
            update(Merchant)
            .where(Merchant.id == merchant_id)
            .values(status="ACTIVE", activated_at=now)
        )


def test_admission_and_poll(http: TestClient) -> None:
    with http.app.state.database.engine.begin() as connection:
        seed_merchant(connection, "mock-user", "mock-user")
    admitted = http.post(URL, json=BODY, headers=HEADERS)
    assert admitted.status_code == 202, admitted.text
    value = admitted.json()
    assert value == {
        "pointTypeId": value["pointTypeId"],
        "name": "แต้มสะสมสุขใจ",
        "symbol": "SUKJAI",
        "decimals": 18,
        "status": "REQUESTED",
        "operationId": value["operationId"],
    }
    replay = http.post(URL, json={**BODY, "name": value["name"]}, headers=HEADERS)
    assert replay.status_code == 202
    assert replay.content == admitted.content
    assert replay.headers["location"] == admitted.headers["location"]
    assert admitted.headers["location"] == f"/v1/operations/{value['operationId']}"
    operation = http.get(admitted.headers["location"], headers=HEADERS)
    assert operation.status_code == 200
    assert operation.json()["kind"] == "POINT_TYPE_CREATE"
    assert operation.json()["status"] == "PENDING"
    assert operation.json()["events"][0]["toStatus"] == "PENDING"
    assert operation.json()["steps"] == []
    with http.app.state.database.sessions() as session:
        outbox = session.scalars(select(Outbox)).one()
        assert outbox.operation_id == value["operationId"]
        assert outbox.event_type == "point-type.create.v1" and outbox.published_at is None
        op = session.get(Operation, value["operationId"])
        assert op.subject_id == value["pointTypeId"] and op.actor_user_id == "mock-user"
        assert op.payload == {
            "pointTypeId": value["pointTypeId"],
            "merchantId": "mock-user",
            "name": value["name"],
            "symbol": value["symbol"],
        }


@pytest.mark.parametrize(
    ("active", "wallet", "status", "code"),
    [(False, True, 403, "MERCHANT_NOT_ACTIVE"), (True, False, 422, "MERCHANT_WALLET_NOT_READY")],
)
def test_merchant_wallet_prerequisites(
    http: TestClient, active: bool, wallet: bool, status: int, code: str
) -> None:
    with http.app.state.database.engine.begin() as connection:
        seed_merchant(connection, "mock-user", "mock-user", active=active, wallet=wallet)
    response = http.post(URL, json=BODY, headers=HEADERS)
    assert (response.status_code, response.json()["error"]["code"]) == (status, code)
    with http.app.state.database.sessions() as session:
        assert session.scalar(select(IdempotencyKey.id)) is None
        assert session.scalar(select(PointType.id)) is None
    if not active:
        with (
            pytest.raises(IntegrityError, match="active"),
            http.app.state.database.engine.begin() as conn,
        ):
            conn.execute(
                insert(PointType).values(
                    id="pt_direct",
                    merchant_id="mock-user",
                    name="Direct",
                    symbol="DCT",
                    decimals=18,
                    created_at=datetime.now(UTC),
                )
            )


def test_validation_and_authentication(http: TestClient) -> None:
    assert http.post(URL, json=BODY).status_code == 401
    auth = {"Authorization": HEADERS["Authorization"]}
    missing = http.post(URL, json=BODY, headers=auth)
    assert (missing.status_code, missing.json()["error"]["code"]) == (
        400,
        "IDEMPOTENCY_KEY_REQUIRED",
    )
    invalid = http.post(URL, json=BODY, headers={**auth, "Idempotency-Key": "short"})
    assert (invalid.status_code, invalid.json()["error"]["code"]) == (
        400,
        "IDEMPOTENCY_KEY_INVALID",
    )
    malformed = (
        {"symbol": "suKJAI"},
        {"symbol": " SPO"},
        {"symbol": "S O"},
        {"symbol": "SPO\n"},
        {"symbol": "A"},
        {"symbol": "ABCDEFGHIJKL"},
        {"decimals": -1},
        {"decimals": 31},
        {"decimals": 0},
        {"decimals": "18"},
        {"decimals": 18.0},
        {"name": " "},
        {"name": "a" * 61},
        {"name": "b\x00c"},
        {"merchantId": "another-merchant"},
    )
    for changes in malformed:
        response = http.post(URL, json={**BODY, **changes}, headers=HEADERS)
        assert (response.status_code, response.json()["error"]["code"]) == (
            400,
            "VALIDATION_FAILED",
        )


def test_conflicts_release_failed_names_and_symbols(http: TestClient) -> None:
    engine = http.app.state.database.engine
    with engine.begin() as connection:
        seed_merchant(connection, "mock-user", "mock-user")
    body = {**BODY, "name": "  Café  "}
    first = http.post(URL, json=body, headers=HEADERS)
    assert first.status_code == 202
    for change, key, code in (
        ({"symbol": "NEW"}, HEADERS["Idempotency-Key"], "IDEMPOTENCY_KEY_REUSED"),
        ({"symbol": "NEW", "name": "  cafe\u0301  "}, "changed-key", "NAME_TAKEN"),
        ({"name": "Different name"}, "changed-key", "SYMBOL_TAKEN"),
    ):
        response = http.post(
            URL, json={**body, **change}, headers={**HEADERS, "Idempotency-Key": key}
        )
        assert (response.status_code, response.json()["error"]["code"]) == (409, code)
    with engine.begin() as connection:
        connection.execute(
            update(PointType)
            .where(PointType.id == first.json()["pointTypeId"])
            .values(status="FAILED")
        )
    second = http.post(URL, json=body, headers={**HEADERS, "Idempotency-Key": "changed-key"})
    assert second.status_code == 202
    assert second.json()["pointTypeId"] != first.json()["pointTypeId"]
    assert http.post(URL, json=body, headers=HEADERS).content == first.content


def test_inflight_deploy_is_unique_and_point_type_insert_is_atomic(http: TestClient) -> None:
    engine = http.app.state.database.engine
    with engine.begin() as connection:
        seed_merchant(connection, "mock-user", "mock-user")

    def fail_outbox(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO outbox"):
            raise OperationalError(statement, parameters, RuntimeError("fixture failure"))

    event.listen(engine, "before_cursor_execute", fail_outbox)
    try:
        unavailable = http.post(URL, json=BODY, headers=HEADERS)
        assert (unavailable.status_code, unavailable.json()["error"]["code"]) == (
            503,
            "POINT_TYPE_UNAVAILABLE",
        )
    finally:
        event.remove(engine, "before_cursor_execute", fail_outbox)
    with http.app.state.database.sessions() as session:
        assert session.scalar(select(PointType.id)) is None
        assert session.scalar(select(IdempotencyKey.id)) is None
        assert (
            session.scalar(select(Operation.id).where(Operation.kind == "POINT_TYPE_CREATE"))
            is None
        )

    retry = http.post(URL, json=BODY, headers=HEADERS)
    assert retry.status_code == 202
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            insert(Operation).values(
                id="op_secondary_deploy",
                subject_id=retry.json()["pointTypeId"],
                kind="POINT_TYPE_CREATE",
                actor_user_id="mock-user",
                status="PENDING",
                payload={},
                created_at=datetime.now(UTC),
            )
        )


def test_twenty_concurrent_requests_commit_one_admission(http: TestClient) -> None:
    with http.app.state.database.engine.begin() as connection:
        seed_merchant(connection, "mock-user", "mock-user")
    barrier = Barrier(20)

    def create(_: int):
        barrier.wait(timeout=15)
        return http.post(URL, json=BODY, headers=HEADERS)

    with ThreadPoolExecutor(max_workers=20) as executor:
        responses = list(executor.map(create, range(20)))
    assert {response.status_code for response in responses} == {202}
    assert len({response.content for response in responses}) == 1
    with http.app.state.database.sessions() as session:
        point_type = session.scalars(select(PointType)).one()
        operation = session.scalars(
            select(Operation).where(Operation.subject_id == point_type.id)
        ).one()
        assert session.scalars(select(Outbox.operation_id)).one() == operation.id
        assert session.scalars(select(IdempotencyKey.operation_id)).one() == operation.id
        assert (
            session.scalars(
                select(OperationEvent.to_status).where(OperationEvent.operation_id == operation.id)
            ).one()
            == "PENDING"
        )


def test_different_keys_racing_for_one_name(http: TestClient) -> None:
    with http.app.state.database.engine.begin() as connection:
        seed_merchant(connection, "mock-user", "mock-user")
    barrier = Barrier(2)

    def create(index: int):
        barrier.wait(timeout=10)
        return http.post(
            URL,
            json={**BODY, "symbol": f"POINT{index}"},
            headers={**HEADERS, "Idempotency-Key": f"point-race-{index}"},
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = sorted(executor.map(create, range(2)), key=lambda row: row.status_code)
    assert [response.status_code for response in responses] == [202, 409]
    assert responses[1].json()["error"]["code"] == "NAME_TAKEN"
    with http.app.state.database.sessions() as session:
        assert session.scalars(select(PointType.id)).one() == responses[0].json()["pointTypeId"]
        assert session.scalars(select(IdempotencyKey.key)).one().startswith("point-race-")


def test_unfinished_idempotency_claim_returns_conflict(http: TestClient) -> None:
    with http.app.state.database.engine.begin() as connection:
        seed_merchant(connection, "mock-user", "mock-user")
        now = datetime.now(UTC)
        connection.execute(
            insert(IdempotencyKey).values(
                id="ik_prior",
                scope="point-type:create:mock-user",
                key=HEADERS["Idempotency-Key"],
                fingerprint=fingerprint(
                    {
                        "merchantId": "mock-user",
                        "name": BODY["name"].strip(),
                        "symbol": BODY["symbol"],
                        "decimals": "18",
                    }
                ),
                created_at=now,
                expires_at=now + timedelta(hours=72),
            )
        )
    result = http.post(URL, json=BODY, headers=HEADERS)
    assert (result.status_code, result.json()["error"]["code"]) == (
        409,
        "IDEMPOTENCY_KEY_IN_PROGRESS",
    )


def test_jwt_subject_and_merchant_are_checked_on_admission_and_replay(
    admission_db: str, tmp_path: Path
) -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    public_key_path = tmp_path / "public.pem"
    public_key_path.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(key.public_key()))
    canonical = json.dumps(
        {field: jwk[field] for field in ("crv", "kty", "x", "y")},
        sort_keys=True,
        separators=(",", ":"),
    )
    kid = urlsafe_b64encode(sha256(canonical.encode()).digest()).rstrip(b"=").decode()
    now = int(datetime.now(UTC).timestamp())

    def headers(sub: str, mid: str) -> dict[str, str]:
        token = jwt.encode(
            {
                "sub": sub,
                "mid": mid,
                "aud": "api",
                "iss": "issuer",
                "sid": "session",
                "jti": "token",
                "kind": "MERCHANT_ADMIN",
                "iat": now - 1,
                "exp": now + 300,
            },
            key,
            algorithm="ES256",
            headers={"kid": kid},
        )
        return {**HEADERS, "Authorization": f"Bearer {token}"}

    settings = ApiSettings(
        APP_ENV="test",
        database_url=admission_db,
        point_type_admission_enabled=True,
        jwt_public_key_file=public_key_path,
        jwt_issuer="issuer",
        jwt_audience="api",
    )
    with TestClient(create_app(settings)) as client:
        engine = client.app.state.database.engine
        with engine.begin() as connection:
            seed_merchant(connection, "merchant1", "admin1")
            seed_merchant(connection, "merchant2", "admin2")
        first = client.post(URL, json=BODY, headers=headers("admin1", "merchant1"))
        assert first.status_code == 202
        cross = client.post(URL, json=BODY, headers=headers("admin1", "merchant2"))
        assert (cross.status_code, cross.json()["error"]["code"]) == (403, "FORBIDDEN")
        second = client.post(URL, json=BODY, headers=headers("admin2", "merchant2"))
        assert second.status_code == 202
        assert second.json()["pointTypeId"] != first.json()["pointTypeId"]
        assert (
            client.get(
                first.headers["location"], headers=headers("admin2", "merchant2")
            ).status_code
            == 404
        )
        with engine.begin() as connection:
            connection.execute(
                update(MerchantAdmin)
                .where(MerchantAdmin.id == "admin1")
                .values(disabled_at=datetime.now(UTC))
            )
        assert (
            client.post(URL, json=BODY, headers=headers("admin1", "merchant1")).status_code == 403
        )


def test_disabled_feature_is_not_routed(admission_db: str) -> None:
    settings = ApiSettings(APP_ENV="test", database_url=admission_db)
    with TestClient(create_app(settings)) as client:
        assert client.post(URL, json=BODY, headers=HEADERS).status_code == 404
        assert URL not in client.get("/openapi.json").json()["paths"]
