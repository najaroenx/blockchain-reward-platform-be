import io

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from marketplace.infrastructure.database import Database


def test_migrations_upgrade_check_and_downgrade(database_url: str) -> None:
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.check(config)
    database = Database(database_url)
    try:
        assert "marketplace_listing_projection" in inspect(database.engine).get_table_names()
        command.downgrade(config, "base")
        assert inspect(database.engine).get_table_names() == ["alembic_version"]
        command.upgrade(config, "head")
        assert "marketplace_projection_checkpoint" in inspect(database.engine).get_table_names()
    finally:
        database.close()


def test_migration_compiles_postgres_ddl_without_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://unused:unused@localhost/unused")
    monkeypatch.delenv("KMS_KEY_ARN", raising=False)
    monkeypatch.delenv("KMS_REGION", raising=False)
    output = io.StringIO()
    config = Config("alembic.ini", output_buffer=output)
    command.upgrade(config, "head", sql=True)
    sql = output.getvalue()
    assert "CREATE TABLE marketplace_listing_projection" in sql
    assert "BIGINT" in sql
    assert "unused:unused" not in sql
