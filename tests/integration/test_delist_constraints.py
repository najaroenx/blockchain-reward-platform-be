"""Proves the FEAT-09 database guarantees from migration 0012.

Test numbers follow tests/sql/feat09_acceptance.sql (from reference/feat-09, aligned to the
Marketplace ABI: see docs/reference-findings.md). The one rule that exists on SQLite, LISTING_BUY
joining uq_listing_inflight, is proved in test_listing_constraints.py. Everything else is
PostgreSQL only and is checked here in the generated DDL; its behaviour is proved by
tests/sql/feat09_acceptance.sql against a real PostgreSQL:

  - ck_operation_actor_scope (T8), a regex,
  - trg_delist_admissible (T3-T6 T9-T11), trg_listing_status_transition (T23 T26 T26b),
    trg_step_order_gate (T18 T19 T19b) and trg_coupon_hold_until_closed (T21 T22), triggers.
"""

import io

import pytest
from alembic import command
from alembic.config import Config


def _postgresql_ddl(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://unused:unused@localhost/unused")
    monkeypatch.delenv("KMS_KEY_ARN", raising=False)
    monkeypatch.delenv("KMS_REGION", raising=False)
    output = io.StringIO()
    command.upgrade(Config("alembic.ini", output_buffer=output), "head", sql=True)
    return output.getvalue()


def test_postgresql_only_parts_are_emitted(monkeypatch: pytest.MonkeyPatch) -> None:
    sql = _postgresql_ddl(monkeypatch)

    for name in (
        "ck_operation_actor_scope",
        "CREATE TRIGGER trg_delist_admissible",
        "CREATE TRIGGER trg_listing_status_transition",
        "CREATE TRIGGER trg_step_order_gate",
        "CREATE TRIGGER trg_coupon_hold_until_closed",
    ):
        assert name in sql, name


def test_step_order_gate_leaves_independent_mint_batches_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # T19b: only listing operations wait for the previous step; COUPON_ISSUE batches do not.
    sql = _postgresql_ddl(monkeypatch)
    gate = sql[sql.index("CREATE FUNCTION f_step_order_gate") :]
    gate = gate[: gate.index("END $$;")]

    assert "v_kind NOT IN ('LISTING_CREATE', 'LISTING_DELETE', 'LISTING_BUY')" in gate
