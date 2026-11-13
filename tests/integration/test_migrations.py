import io
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from marketplace.auth.infrastructure.models import AuthSession, Customer
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


def test_disabled_session_migration_backfills_existing_data_and_preserves_revocation_on_down(
    database_url: str,
) -> None:
    config = Config("alembic.ini")
    command.downgrade(config, "0002_auth_login")
    database = Database(database_url)
    now = datetime.now(UTC)
    try:
        with database.sessions.begin() as session:
            session.add(
                Customer(
                    id="legacy-disabled", tel_hash=sha256(b"legacy-user").digest(), disabled_at=now
                )
            )
            session.flush()
            session.add(
                AuthSession(
                    id="legacy-session",
                    subject_kind="CUSTOMER",
                    customer_id="legacy-disabled",
                    family_id="legacy-family",
                    refresh_token_hash=sha256(b"legacy-token").digest(),
                    created_at=now,
                    expires_at=now + timedelta(days=30),
                )
            )
        command.upgrade(config, "head")
        command.check(config)
        with database.sessions() as session:
            row = session.get(AuthSession, "legacy-session")
            assert row.revoked_at is not None and row.revoked_reason == "ADMIN_REVOKED"
        command.downgrade(config, "0002_auth_login")
        with database.sessions() as session:
            assert session.get(AuthSession, "legacy-session").revoked_reason == "ADMIN_REVOKED"
        command.upgrade(config, "head")
    finally:
        database.close()
