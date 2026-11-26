"""FEAT-06 (migration 0009) is read functions and triggers on PostgreSQL only.

Their behaviour needs numbers beyond int64 and plpgsql, so it is proved by
tests/sql/feat06_acceptance.sql against a real PostgreSQL. Here: the DDL is emitted, and no read
function asks the clock (FEAT-06 T19), checked in the generated SQL itself.
"""

import io
import re

import pytest
from alembic import command
from alembic.config import Config

READ_FUNCTIONS = (
    "f_available_points",
    "f_points_fefo",
    "f_expiry_forecast",
    "f_expired_total",
    "f_point_lot_active",
    "f_point_lot_expired",
)


@pytest.fixture
def postgres_ddl(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://unused:unused@localhost/unused")
    monkeypatch.delenv("KMS_KEY_ARN", raising=False)
    monkeypatch.delenv("KMS_REGION", raising=False)
    output = io.StringIO()
    command.upgrade(Config("alembic.ini", output_buffer=output), "head", sql=True)
    return output.getvalue()


def _last_definition(ddl: str, name: str) -> str:
    """The body of the latest CREATE [OR REPLACE] FUNCTION <name>, up to its closing $$;."""
    found = re.findall(rf"CREATE (?:OR REPLACE )?FUNCTION {name}\(.*?\$\$;", ddl, flags=re.S)
    assert found, name
    return str(found[-1])


def test_feat06_functions_and_triggers_are_emitted(postgres_ddl: str) -> None:
    for name in (
        "CREATE FUNCTION f_expiry_forecast",
        "CREATE FUNCTION f_expired_total",
        "CREATE FUNCTION f_point_lot_expired",
        "CREATE TRIGGER trg_notice_not_expired",
        "CREATE TRIGGER trg_watermark_monotonic",
        "greatest(v_total - v_held, 0)",
    ):
        assert name in postgres_ddl, name


@pytest.mark.parametrize("name", READ_FUNCTIONS)
def test_t19_read_functions_take_their_time_as_an_argument(postgres_ddl: str, name: str) -> None:
    body = _last_definition(postgres_ddl, name)
    assert not re.search(r"\b(now|current_timestamp|clock_timestamp)\b", body, flags=re.I)
    assert "VOLATILE" not in body
