from pathlib import Path

import pytest

from marketplace.settings import ConfigurationError, init_env, load_settings


def test_role_files_override_shared_and_shell_overrides_files(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("DATABASE_URL=sqlite:///local.db\nPORT=1000\n")
    (tmp_path / ".env.api").write_text("PORT=2000\n")
    assert load_settings("api", root=tmp_path, environ={}).port == 2000
    assert load_settings("api", root=tmp_path, environ={"PORT": "3000"}).port == 3000


def test_role_files_do_not_leak_to_other_processes(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("DATABASE_URL=sqlite:///local.db\n")
    (tmp_path / ".env.worker").write_text("KMS_KEY_ARN=not-for-api\nKMS_REGION=not-for-api\n")
    assert load_settings("api", root=tmp_path, environ={}).port == 3000


@pytest.mark.parametrize("role", ["api", "indexer"])
@pytest.mark.parametrize("secret", ["KMS_KEY_ARN", "KMS_REGION"])
def test_non_signing_roles_reject_worker_secrets(tmp_path: Path, role: str, secret: str) -> None:
    with pytest.raises(ConfigurationError) as result:
        load_settings(
            role, root=tmp_path, environ={"DATABASE_URL": "sqlite:///db", secret: "SECRET"}
        )
    assert secret in str(result.value)
    assert "SECRET" not in str(result.value)


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"DATABASE_URL": "not-a-database://user:SECRET@host"},
        {"DATABASE_URL": "sqlite:///db", "PORT": "SECRET"},
        {"DATABASE_URL": "sqlite:///db", "PORT": "65536"},
        {"DATABASE_URL": "sqlite:///db", "LOG_LEVEL": "SECRET"},
    ],
)
def test_invalid_configuration_hides_values(tmp_path: Path, values: dict[str, str]) -> None:
    with pytest.raises(ConfigurationError) as result:
        load_settings("api", root=tmp_path, environ=values)
    assert "SECRET" not in str(result.value)


def test_worker_requires_own_configuration(tmp_path: Path) -> None:
    values = {
        "DATABASE_URL": "sqlite:///db",
        "RPC_URL": "http://localhost:8545",
        "CHAIN_ID": "1337",
    }
    with pytest.raises(ConfigurationError, match="kms_key_arn"):
        load_settings("worker", root=tmp_path, environ=values)
    values.update(KMS_KEY_ARN="kms-test", KMS_REGION="ap-southeast-1")
    assert load_settings("worker", root=tmp_path, environ=values).chain_id == 1337


@pytest.mark.parametrize("rpc", ["file:///SECRET", "http://", "ws://localhost"])
def test_rpc_scheme_is_validated(tmp_path: Path, rpc: str) -> None:
    with pytest.raises(ConfigurationError, match="rpc_url"):
        load_settings(
            "indexer",
            root=tmp_path,
            environ={
                "DATABASE_URL": "sqlite:///db",
                "RPC_URL": rpc,
                "CHAIN_ID": "1337",
            },
        )


def test_production_requires_postgres(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="production requires PostgreSQL"):
        load_settings(
            "api",
            root=tmp_path,
            environ={
                "DATABASE_URL": "sqlite:///db",
                "APP_ENV": "production",
            },
        )


def test_init_env_preserves_local_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "pyproject.toml").touch()
    (tmp_path / ".env").write_text("DATABASE_URL=keep-this\n")
    (tmp_path / ".env.example").write_text("DATABASE_URL=replace-this\n")
    (tmp_path / ".env.api.example").write_text("PORT=3000\n")
    monkeypatch.chdir(tmp_path)
    init_env()
    init_env()
    assert (tmp_path / ".env").read_text() == "DATABASE_URL=keep-this\n"
    assert (tmp_path / ".env.api").read_text() == "PORT=3000\n"
