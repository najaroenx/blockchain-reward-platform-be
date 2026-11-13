"""Run backend redeem integration against an isolated archive of the pinned contract."""

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVISION = "1dc42d555046830c7320e46052696f62ab380dad"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--contract-repo", type=Path, default=ROOT.parent / "blockchain-coupon-marketplace-loyalty"
    )
    args = parser.parse_args()
    repository = args.contract_repo.resolve()
    if not (repository / "node_modules/hardhat").is_dir():
        parser.error("Contract repository needs its lockfile-installed Hardhat dependencies")
    archive = subprocess.run(
        ["git", "-C", str(repository), "archive", REVISION], check=True, capture_output=True
    ).stdout
    with tempfile.TemporaryDirectory(prefix="redeem-chain-", dir="/private/tmp") as directory:
        workspace = Path(directory)
        with tarfile.open(fileobj=io.BytesIO(archive)) as source:
            source.extractall(workspace, filter="data")
        lock = json.loads((workspace / "package-lock.json").read_text())
        for package in (
            "hardhat",
            "@openzeppelin/contracts",
            "@openzeppelin/contracts-upgradeable",
            "@openzeppelin/hardhat-upgrades",
        ):
            expected = lock["packages"]["node_modules/" + package]["version"]
            installed = json.loads(
                (repository / "node_modules" / package / "package.json").read_text()
            )["version"]
            if installed != expected:
                parser.error(f"Contract dependency differs from pinned lockfile: {package}")
        (workspace / "node_modules").symlink_to(
            repository / "node_modules", target_is_directory=True
        )
        shutil.copy2(ROOT / "tests/chain/redeem_bridge.cjs", workspace / "test/redeem_bridge.cjs")
        env = {k: v for k, v in os.environ.items() if k not in {"DLP_PRIVATE_KEY", "DLP_RPC_URL"}}
        env["REDEEM_CHAIN_WORKSPACE"] = str(workspace)
        env["HARDHAT_NETWORK"] = "hardhat"
        subprocess.run(
            ["node", "node_modules/hardhat/internal/cli/cli.js", "compile"],
            cwd=workspace,
            env=env,
            check=True,
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/integration/coupons/test_execution_chain.py",
                "-q",
            ],
            cwd=ROOT,
            env=env,
            check=True,
        )


if __name__ == "__main__":
    main()
