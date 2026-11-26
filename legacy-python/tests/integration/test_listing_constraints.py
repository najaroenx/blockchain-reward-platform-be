"""Proves the FEAT-08 database guarantees from migration 0011 that hold on both dialects.

Test numbers follow tests/sql/feat08_acceptance.sql (from reference/feat-08, aligned to the
Marketplace ABI: see docs/reference-findings.md). Only uq_listing_inflight (T19; LISTING_BUY joined
in 0012, FEAT-09) exists on SQLite.
The rest is PostgreSQL only and is checked here only in the generated DDL; its behaviour is proved
by tests/sql/feat08_acceptance.sql against a real PostgreSQL:

  - trg_listing_item_valid (T2b T4-T7 T9b), trg_listing_item_max (T3), trg_listing_item_count (T3b),
    trg_listing_chain_immutable (T17), trg_listing_active_has_chain (T23), triggers,
  - ck_listing_unit_price_uint256 (T8 T20) and ck_listing_chain_id_uint256 (T17b), which call
    f_is_uint256.
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
from marketplace.infrastructure.legacy_schema import operation


def _operation(id_: str, kind: str = "LISTING_CREATE", **values: Any) -> dict[str, Any]:
    return {
        "id": id_,
        "actor_scope": "merchant:mch_1",
        "kind": kind,
        "subject_id": "lst_1",
        "payload": {},
        **values,
    }


def _insert(database: Database, **values: Any) -> None:
    with database.sessions.begin() as session:
        session.execute(operation.insert().values(**values))


@pytest.fixture
def database(database_url: str) -> Iterator[Database]:
    database = Database(database_url)
    yield database
    database.close()


# --- T19 · one unfinished create / delete per listing ---------------------------------------


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("LISTING_CREATE", "LISTING_CREATE"),
        ("LISTING_CREATE", "LISTING_DELETE"),
        ("LISTING_DELETE", "LISTING_DELETE"),
    ],
)
def test_t19_second_unfinished_operation_on_a_listing_is_rejected(
    database: Database, first: str, second: str
) -> None:
    _insert(database, **_operation("op_1", first))

    with pytest.raises(IntegrityError, match="subject_id"):
        _insert(database, **_operation("op_2", second))


@pytest.mark.parametrize("status", ["FINALIZED", "CHAIN_REVERTED"])
def test_t19_a_finished_operation_frees_the_listing(database: Database, status: str) -> None:
    extra = {"finalized_at": datetime.now(UTC)} if status == "FINALIZED" else {}
    _insert(database, **_operation("op_1", status=status, **extra))

    _insert(database, **_operation("op_2", "LISTING_DELETE"))


def test_t19_a_purchase_and_a_delist_of_one_listing_cannot_overlap(database: Database) -> None:
    # 0012 (FEAT-09 T13): a purchase counts as unfinished work on the listing.
    _insert(database, **_operation("op_1", "LISTING_BUY", status="SUBMITTED"))

    with pytest.raises(IntegrityError, match="subject_id"):
        _insert(database, **_operation("op_2", "LISTING_DELETE"))


def test_t19_two_purchases_of_one_listing_cannot_overlap(database: Database) -> None:
    _insert(database, **_operation("op_1", "LISTING_BUY"))

    with pytest.raises(IntegrityError, match="subject_id"):
        _insert(database, **_operation("op_2", "LISTING_BUY"))


def test_t19_a_reverted_purchase_frees_the_listing(database: Database) -> None:
    _insert(database, **_operation("op_1", "LISTING_BUY", status="CHAIN_REVERTED"))

    _insert(database, **_operation("op_2", "LISTING_DELETE"))


# --- PostgreSQL-only parts, checked in the generated DDL ------------------------------------


def test_postgresql_only_parts_are_emitted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://unused:unused@localhost/unused")
    monkeypatch.delenv("KMS_KEY_ARN", raising=False)
    monkeypatch.delenv("KMS_REGION", raising=False)
    output = io.StringIO()
    command.upgrade(Config("alembic.ini", output_buffer=output), "head", sql=True)
    sql = output.getvalue()

    for name in (
        "ck_listing_unit_price_uint256",
        "ck_listing_chain_id_uint256",
        "CREATE TRIGGER trg_listing_item_valid",
        "CREATE TRIGGER trg_listing_item_max",
        "CREATE CONSTRAINT TRIGGER trg_listing_item_count",
        "CREATE TRIGGER trg_listing_chain_immutable",
        "CREATE CONSTRAINT TRIGGER trg_listing_active_has_chain",
        "FOR NO KEY UPDATE OF cc",
    ):
        assert name in sql, name
