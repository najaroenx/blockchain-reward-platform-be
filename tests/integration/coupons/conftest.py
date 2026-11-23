import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url

from marketplace.infrastructure.database import Database


@pytest.fixture
def admission_database_url(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    postgres = os.environ.get("REDEEM_ADMISSION_TEST_DATABASE_URL")
    if postgres is None:
        yield database_url
        return
    url = make_url(postgres)
    assert url.get_backend_name() == "postgresql"
    schema = f"redeem_admission_{uuid4().hex}"
    admin = Database(postgres)
    try:
        with admin.engine.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA {schema}"))
        isolated = url.update_query_dict({"options": f"-csearch_path={schema}"}).render_as_string(
            hide_password=False
        )
        monkeypatch.setenv("DATABASE_URL", isolated)
        command.upgrade(Config("alembic.ini"), "head")
        yield isolated
    finally:
        with admin.engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        admin.close()
