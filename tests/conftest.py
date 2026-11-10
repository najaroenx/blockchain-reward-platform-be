from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from marketplace.main import create_app
from marketplace.settings import ApiSettings


@pytest.fixture
def database_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    url = f"sqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("KMS_KEY_ARN", raising=False)
    monkeypatch.delenv("KMS_REGION", raising=False)
    command.upgrade(Config("alembic.ini"), "head")
    return url


@pytest.fixture
def client(database_url: str) -> Iterator[TestClient]:
    app = create_app(ApiSettings(database_url=database_url, APP_ENV="test"))
    with TestClient(app) as http:
        yield http
