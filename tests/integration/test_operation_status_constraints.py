"""Proves the FEAT-13 database guarantees from migration 0016 that hold on both dialects.

Test numbers follow tests/sql/feat13_acceptance.sql (from reference/feat-13, with the gaps of the
pack closed: see docs/reference-findings.md). Here: the first delivery of an event to an endpoint
happens once, while a replay of it is a new row (T18, T19, T24b, T24e). The status machine, the
operation history the database writes itself, the endpoint checks, delivery admission, ordering,
immutability and the attempt counter are PostgreSQL triggers and checks, and are checked here only
in the generated DDL; tests/sql/feat13_acceptance.sql proves their behaviour.
"""

import io
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError

from marketplace.infrastructure.database import Database
from marketplace.infrastructure.legacy_schema import (
    merchant,
    operation,
    webhook_delivery,
    webhook_endpoint,
)


def _insert(database: Database, table: Any, **values: Any) -> None:
    with database.sessions.begin() as session:
        session.execute(table.insert().values(**values))


def _endpoint(id_: str) -> dict[str, Any]:
    return {
        "id": id_,
        "merchant_id": "mch_a",
        "url": f"https://{id_}.example/hook",
        "secret_ref": f"kms://webhook/mch_a/{id_}",
        "event_types": ["operation.status"],
    }


def _delivery(id_: str, endpoint_id: str, event_seq: int, delivery_seq: int, **values: Any) -> dict:
    return {
        "id": id_,
        "endpoint_id": endpoint_id,
        "operation_id": "op_1",
        "event_seq": event_seq,
        "delivery_seq": delivery_seq,
        "event_type": "operation.status",
        "payload": {"event": event_seq},
        "signature": f"sha256={id_}",
        "signed_at": datetime.now(UTC),
        **values,
    }


@pytest.fixture
def database(database_url: str) -> Iterator[Database]:
    database = Database(database_url)
    _insert(
        database, merchant, id="mch_a", name="A", status="ACTIVE", activated_at=datetime.now(UTC)
    )
    _insert(database, webhook_endpoint, **_endpoint("ep_1"))
    _insert(database, webhook_endpoint, **_endpoint("ep_2"))
    _insert(
        database,
        operation,
        id="op_1",
        actor_scope="merchant:mch_a",
        kind="POINT_TRANSFER",
        subject_id="x",
        payload={},
    )
    _insert(database, webhook_delivery, **_delivery("dl_1", "ep_1", 1, 1))
    yield database
    database.close()


# --- T18 T19 T24b T24e · the first delivery of an event happens once per endpoint -----------


def test_t18_first_delivery_of_an_event_to_the_same_endpoint_again_is_rejected(
    database: Database,
) -> None:
    with pytest.raises(IntegrityError, match="event_seq"):
        _insert(database, webhook_delivery, **_delivery("dl_2", "ep_1", 1, 2))


def test_t19_the_same_event_goes_to_another_endpoint(database: Database) -> None:
    _insert(database, webhook_delivery, **_delivery("dl_2", "ep_2", 1, 1))


def test_t24b_a_replay_of_the_event_is_a_new_row(database: Database) -> None:
    _insert(database, webhook_delivery, **_delivery("dl_r", "ep_1", 1, 2, replay_of="dl_1"))


def test_t20_delivery_seq_is_unique_per_endpoint_and_operation(database: Database) -> None:
    with pytest.raises(IntegrityError, match="delivery_seq"):
        _insert(database, webhook_delivery, **_delivery("dl_2", "ep_1", 2, 1))


# --- PostgreSQL-only parts, checked in the generated DDL ------------------------------------


def test_postgresql_only_parts_are_emitted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://unused:unused@localhost/unused")
    monkeypatch.delenv("KMS_KEY_ARN", raising=False)
    monkeypatch.delenv("KMS_REGION", raising=False)
    output = io.StringIO()
    command.upgrade(Config("alembic.ini", output_buffer=output), "head", sql=True)
    sql = output.getvalue()

    for name in (
        "CREATE TRIGGER trg_operation_status_transition",
        "CREATE TRIGGER trg_operation_event_write",
        "CREATE TRIGGER trg_operation_event_guard",
        "current_setting('app.operation_actor', true)",
        "ck_webhook_endpoint_url_https",
        "ck_webhook_endpoint_secret_ref",
        "ck_webhook_endpoint_event_types",
        "CREATE TRIGGER trg_delivery_admissible",
        "CREATE TRIGGER trg_replay_valid",
        "CREATE TRIGGER trg_delivery_immutable",
        "CREATE TRIGGER trg_delivery_order_gate",
        "CREATE CONSTRAINT TRIGGER trg_attempt_count_matches",
        "CREATE TRIGGER trg_attempt_append_only",
        "CREATE FUNCTION f_backoff(",
        '{"backfill": true}',
    ):
        assert name in sql, name
