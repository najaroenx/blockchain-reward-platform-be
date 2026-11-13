import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy.engine import make_url

from marketplace.main import create_app
from marketplace.settings import ApiSettings, JwtSigningSettings


@pytest.fixture
def jwt_key_file(tmp_path: Path) -> Path:
    path = tmp_path / "jwt-secret.pem"
    path.write_bytes(
        ec.generate_private_key(ec.SECP256R1()).private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
        )
    )
    path.chmod(0o600)
    return path


@pytest.fixture
def jwt_settings(jwt_key_file: Path) -> JwtSigningSettings:
    return JwtSigningSettings(
        private_key_file=str(jwt_key_file),
        issuer="https://auth.example.test",
        audience="reward-api",
    )


@contextmanager
def isolated_database(tmp_path: Path) -> Iterator[str]:
    postgres = os.environ.get("TEST_POSTGRES_URL")
    if not postgres:
        yield f"sqlite:///{tmp_path / 'test.db'}"
        return
    parsed = make_url(postgres)
    if parsed.get_backend_name() != "postgresql":
        raise ValueError("TEST_POSTGRES_URL must identify a PostgreSQL admin database")
    name = "reward_test_" + uuid4().hex
    admin_url = parsed.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            yield parsed.set(database=name).render_as_string(hide_password=False)
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def database_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with isolated_database(tmp_path) as url:
        monkeypatch.setenv("DATABASE_URL", url)
        monkeypatch.setenv("APP_ENV", "test")
        monkeypatch.delenv("KMS_KEY_ARN", raising=False)
        monkeypatch.delenv("KMS_REGION", raising=False)
        command.upgrade(Config("alembic.ini"), "head")
        yield url


@pytest.fixture
def client(database_url: str) -> Iterator[TestClient]:
    app = create_app(ApiSettings(database_url=database_url, APP_ENV="test"))
    with TestClient(app) as http:
        yield http
