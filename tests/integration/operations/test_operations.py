import json
import os
from base64 import urlsafe_b64encode
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import jwt
import pytest
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, delete, insert, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from marketplace.coupons.application.dto import RedeemCouponPayload
from marketplace.coupons.infrastructure.admission import SqlRedeemAdmissionStore
from marketplace.infrastructure.database import Database
from marketplace.infrastructure.operation_history import advance_operation, record_operation_created
from marketplace.infrastructure.operation_models import (
    Operation,
    OperationEvent,
    OperationEventExpected,
    OperationStep,
)
from marketplace.main import create_app
from marketplace.settings import ApiSettings

URL = "/v1/operations"
HEADERS = {"Authorization": "Bearer mock-redeem-token"}
OWNER = "mock-user"
OTHER = "other-user"
TX_HASH = "0x" + "b" * 64


@pytest.fixture
def history_database(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[str]:
    postgres = os.environ.get("OPERATIONS_TEST_DATABASE_URL")
    if postgres is None:
        yield request.getfixturevalue("database_url")
        return

    admin = Database(postgres)
    schema = f"test_operations_{uuid4().hex}"
    options_url = make_url(postgres).update_query_dict({"options": f"-csearch_path={schema}"})
    try:
        with admin.engine.begin() as connection:
            connection.exec_driver_sql(f"CREATE SCHEMA {schema}")
        url = options_url.render_as_string(hide_password=False)
        monkeypatch.setenv("DATABASE_URL", url)
        monkeypatch.setenv("APP_ENV", "test")
        command.upgrade(Config("alembic.ini"), "head")
        yield url
    finally:
        with admin.engine.begin() as connection:
            connection.exec_driver_sql(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        admin.close()


@pytest.fixture
def operations(history_database: str) -> Iterator[TestClient]:
    settings = ApiSettings(APP_ENV="test", database_url=history_database, mock_auth_enabled=True)
    with TestClient(create_app(settings)) as http:
        yield http


def create_operation(
    connection: Connection,
    operation_id: str,
    *,
    user: str = OWNER,
    kind: str = "COUPON_REDEEM",
    stamp: datetime | None = None,
) -> str:
    created_at = datetime.now(UTC) if stamp is None else stamp
    connection.execute(
        insert(Operation).values(
            id=operation_id,
            kind=kind,
            actor_user_id=user,
            status="PENDING",
            created_at=created_at,
            payload={"hidden": "private operation payload"},
        )
    )
    record_operation_created(connection, operation_id, created_at)
    return operation_id


def append_raw(
    connection: Connection,
    operation_id: str,
    seq: int,
    from_status: str | None,
    to_status: str,
) -> None:
    connection.execute(
        insert(OperationEvent).values(
            operation_id=operation_id,
            seq=seq,
            from_status=from_status,
            to_status=to_status,
            created_at=datetime.now(UTC),
        )
    )


def test_redeem_admission_writes_first_event_and_owner_can_poll(operations: TestClient) -> None:
    engine = operations.app.state.database.engine
    store = SqlRedeemAdmissionStore(engine)
    result = store.admit(
        user_id=OWNER,
        idempotency_key="operations-poll-key",
        payload=RedeemCouponPayload(
            chain_id=1337,
            coupon_contract_address="0x" + "1" * 40,
            wallet_address="0x" + "2" * 40,
            type_id=3,
        ),
    )
    url = f"{URL}/{result.operation_id}"
    first = operations.get(url, headers=HEADERS)
    assert first.status_code == 200
    assert first.headers["retry-after"] == "2"
    assert first.headers["etag"] == f'"{result.operation_id}-1"'
    assert first.headers["cache-control"] == "private, no-cache"
    initial = first.json()
    assert initial["eventSeq"] == 1
    assert initial["events"][0]["fromStatus"] is None
    assert initial["events"][0]["toStatus"] == "PENDING"
    assert initial["steps"] == []
    assert "walletAddress" not in first.text

    # A signed but unbroadcast transaction is internal and does not change the ETag.
    with engine.begin() as connection:
        connection.execute(
            insert(OperationStep).values(
                operation_id=result.operation_id,
                chain_id="1337",
                signer_address="0x" + "a" * 40,
                nonce="123",
                raw_tx=b"PRIVATE SIGNED BYTES",
                tx_hash=TX_HASH,
                status="PREPARED",
                created_at=datetime.now(UTC),
            )
        )
    conditional = {**HEADERS, "If-None-Match": f'W/{first.headers["etag"]}, "old"'}
    unchanged = operations.get(url, headers=conditional)
    assert unchanged.status_code == 304 and unchanged.content == b""
    assert operations.get(url, headers=HEADERS).json() == initial

    with engine.begin() as connection:
        connection.execute(
            update(OperationStep)
            .where(OperationStep.operation_id == result.operation_id)
            .values(status="SUBMITTED")
        )
        advance_operation(connection, result.operation_id, "SUBMITTED", tx_hash=TX_HASH)
    submitted = operations.get(url, headers=conditional)
    assert submitted.status_code == 200 and submitted.headers["retry-after"] == "5"
    assert submitted.json()["steps"] == [
        {"index": 1, "name": "redeemFrom", "status": "SUBMITTED", "txHash": TX_HASH}
    ]
    assert "PRIVATE SIGNED BYTES" not in submitted.text and "signer" not in submitted.text


def test_reorg_history_filter_and_cache_validators(operations: TestClient) -> None:
    engine = operations.app.state.database.engine
    with engine.begin() as connection:
        op_id = create_operation(connection, "op_possibleReorg")
        for status in ("SUBMITTED", "CONFIRMED"):
            advance_operation(connection, op_id, status, tx_hash=TX_HASH)
    url = f"{URL}/{op_id}"
    confirmed = operations.get(url, headers=HEADERS)
    assert confirmed.json()["status"] == "CONFIRMED"
    assert confirmed.headers["retry-after"] == "15"

    with engine.begin() as connection:
        advance_operation(connection, op_id, "ORPHANED", tx_hash=TX_HASH)
    orphaned = operations.get(url, headers={**HEADERS, "If-None-Match": confirmed.headers["etag"]})
    assert orphaned.status_code == 200
    assert orphaned.json()["eventSeq"] == 4 and orphaned.json()["status"] == "ORPHANED"
    assert orphaned.headers["retry-after"] == "10"
    assert (
        operations.get(URL, params={"status": "ORPHANED"}, headers=HEADERS).json()["items"][0][
            "operationId"
        ]
        == op_id
    )
    assert (
        operations.get(URL, params={"status": "CONFIRMED"}, headers=HEADERS).json()["items"] == []
    )

    with engine.begin() as connection:
        for status in ("SUBMITTED", "CONFIRMED", "FINALIZED"):
            advance_operation(connection, op_id, status, tx_hash=TX_HASH)
    terminal = operations.get(url, headers=HEADERS)
    assert terminal.json()["terminal"] is True
    assert terminal.json()["finalizedAt"] == terminal.json()["events"][-1]["createdAt"]
    assert "retry-after" not in terminal.headers
    assert [event["seq"] for event in terminal.json()["events"]] == list(range(1, 8))
    assert [event["toStatus"] for event in terminal.json()["events"]] == [
        "PENDING",
        "SUBMITTED",
        "CONFIRMED",
        "ORPHANED",
        "SUBMITTED",
        "CONFIRMED",
        "FINALIZED",
    ]
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(update(Operation).where(Operation.id == op_id).values(payload={}))


def test_detail_and_list_are_scoped_by_actor_and_pagination_is_stable(
    operations: TestClient,
) -> None:
    engine = operations.app.state.database.engine
    stamp = datetime.now(UTC)
    with engine.begin() as connection:
        for suffix in ("a", "b", "c"):
            create_operation(connection, f"op_recent{suffix}", stamp=stamp)
        create_operation(
            connection, "op_olderaa", stamp=stamp - timedelta(seconds=1), kind="THB_MINT"
        )
        foreign = create_operation(connection, "op_foreign", user=OTHER, stamp=stamp)
    missing = operations.get(f"{URL}/op_missing", headers=HEADERS)
    foreign_view = operations.get(f"{URL}/{foreign}", headers={**HEADERS, "If-None-Match": "*"})
    assert foreign_view.status_code == missing.status_code == 404
    assert foreign_view.json() == missing.json()
    assert operations.get(URL).status_code == 401
    assert operations.get(f"{URL}/{foreign}").status_code == 401
    params = {"limit": 2, "kind": "COUPON_REDEEM"}
    first = operations.get(URL, params=params, headers=HEADERS).json()
    second = operations.get(URL, params={**params, "cursor": first["nextCursor"]}, headers=HEADERS)
    assert [item["operationId"] for item in first["items"]] == ["op_recentc", "op_recentb"]
    assert [item["operationId"] for item in second.json()["items"]] == ["op_recenta"]
    assert second.json()["nextCursor"] is None
    assert operations.get(URL, params={"cursor": "!!"}, headers=HEADERS).status_code == 400
    assert operations.get(URL, params={"limit": 0}, headers=HEADERS).status_code == 400


def test_commit_checks_reject_incomplete_transition_in_both_directions(
    operations: TestClient,
) -> None:
    engine: Engine = operations.app.state.database.engine
    with engine.begin() as connection:
        op_id = create_operation(connection, "op_dbchecks")

    for option in ("status_only", "event_only", "wrong_predecessor", "skipped_number"):
        with engine.connect() as connection:
            transaction = connection.begin()
            if option != "event_only":
                connection.execute(
                    update(Operation)
                    .where(Operation.id == op_id)
                    .values(status="SUBMITTED", event_seq=2)
                )
            if option != "status_only":
                append_raw(
                    connection,
                    op_id,
                    5 if option == "skipped_number" else 2,
                    "ORPHANED" if option == "wrong_predecessor" else "PENDING",
                    "SUBMITTED",
                )
            with pytest.raises(IntegrityError):
                transaction.commit()

    with engine.begin() as connection:
        append_raw(connection, op_id, 2, "PENDING", "SUBMITTED")
        connection.execute(
            update(Operation).where(Operation.id == op_id).values(status="SUBMITTED", event_seq=2)
        )
    assert operations.get(f"{URL}/{op_id}", headers=HEADERS).json()["eventSeq"] == 2

    for seq, previous in ((1, None), (3, None)):
        with pytest.raises(IntegrityError), engine.begin() as connection:
            append_raw(connection, op_id, seq, previous, "CONFIRMED")
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(delete(OperationEvent).where(OperationEvent.operation_id == op_id))
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            update(OperationEventExpected)
            .where(OperationEventExpected.operation_id == op_id)
            .values(to_status="FINALIZED")
        )


def test_jwt_read_uses_subject_even_for_admins_of_the_same_merchant(
    history_database: str, tmp_path: Path
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
    valid = {
        "iss": "issuer",
        "aud": "operations",
        "sub": OWNER,
        "mid": "one-merchant",
        "kind": "MERCHANT_ADMIN",
        "sid": "session",
        "jti": "token",
        "iat": now - 1,
        "exp": now + 300,
    }
    settings = ApiSettings(
        APP_ENV="test",
        database_url=history_database,
        jwt_public_key_file=public_key_path,
        jwt_issuer="issuer",
        jwt_audience="operations",
    )
    with TestClient(create_app(settings)) as http:
        with http.app.state.database.engine.begin() as connection:
            op_id = create_operation(connection, "op_sameMerchant")
        owner = jwt.encode(valid, key, algorithm="ES256", headers={"kid": kid})
        peer = jwt.encode(
            {**valid, "sub": "different-admin"}, key, algorithm="ES256", headers={"kid": kid}
        )
        url = f"{URL}/{op_id}"
        assert http.get(url, headers={"Authorization": f"Bearer {owner}"}).status_code == 200
        assert http.get(url, headers={"Authorization": f"Bearer {peer}"}).status_code == 404


def test_populated_upgrade_and_downgrade_preserve_legacy_operation(
    history_database: str, operations: TestClient
) -> None:
    config = Config("alembic.ini")
    engine = operations.app.state.database.engine
    command.downgrade(config, "0008_deposit_admission")
    op_id = "op_legacy1"
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO operation (id, actor_user_id, kind, status, payload, created_at) "
            "VALUES ('op_legacy1','mock-user','COUPON_REDEEM','CONFIRMED','{}',CURRENT_TIMESTAMP)"
        )
    command.upgrade(config, "head")
    with engine.connect() as connection:
        snapshot = connection.execute(
            select(OperationEvent.is_snapshot).where(OperationEvent.operation_id == op_id)
        ).scalar_one()
        assert snapshot is True
    view = operations.get(f"{URL}/{op_id}", headers=HEADERS).json()
    assert view["status"] == "CONFIRMED"
    assert len(view["events"]) == 1 and view["events"][0]["fromStatus"] is None
    command.downgrade(config, "0008_deposit_admission")
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT id, status FROM operation").one() == (
            op_id,
            "CONFIRMED",
        )
    command.upgrade(config, "head")
