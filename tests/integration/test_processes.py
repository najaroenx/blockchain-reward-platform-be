import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("role", ["worker", "indexer"])
def test_process_check_is_honest_and_default_execution_is_disabled(
    tmp_path: Path, role: str
) -> None:
    env = {
        **os.environ,
        "DATABASE_URL": "sqlite:///:memory:",
        "RPC_URL": "http://localhost:8545",
        "CHAIN_ID": "1337",
        "APP_ENV": "test",
    }
    env.pop("KMS_KEY_ARN", None)
    env.pop("KMS_REGION", None)
    if role == "worker":
        env.update(KMS_KEY_ARN="SECRET-KMS", KMS_REGION="ap-southeast-1")
    command = str(Path(sys.executable).parent / f"reward-{role}")
    checked = subprocess.run(
        [command, "--check"], cwd=tmp_path, env=env, text=True, capture_output=True
    )
    assert checked.returncode == 0, checked.stderr
    data = json.loads(checked.stdout)
    assert data["configuration_valid"] is True
    assert data.get("transaction_submission_enabled", data.get("indexing_enabled")) is False
    assert "SECRET-KMS" not in checked.stdout + checked.stderr
    refused = subprocess.run([command], cwd=tmp_path, env=env, text=True, capture_output=True)
    assert refused.returncode == 2
    assert "not enabled" in refused.stderr
