"""Deploy the pinned reward contracts and record their addresses and deployment blocks."""

import argparse
import io
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
COUPON_MANIFEST = ROOT / "src/marketplace/infrastructure/blockchain/abi/manifest.json"
POINT_REVISION = "af87d7e05cf73bd457d11ac04796d52b233da6fb"
NETWORKS = {"hardhat": 1337, "besu": 235, "dlp": 116687680}

HARDHAT_CONFIG = """
const source = require("./hardhat.config.js");
const remote = {
  url: process.env.REWARD_DEPLOY_RPC_URL,
  chainId: Number(process.env.DEPLOY_CHAIN_ID),
  accounts: process.env.REWARD_DEPLOY_PRIVATE_KEY
    ? [process.env.REWARD_DEPLOY_PRIVATE_KEY] : "remote",
};
if (process.env.DEPLOY_NETWORK === "dlp") remote.gasPrice = 0;
module.exports = { ...source, networks: { ...source.networks, rewardDeploy: remote } };
"""


def stage_repository(
    repository: Path, revision: str, destination: Path, packages: tuple[str, ...]
) -> None:
    archive = subprocess.run(
        ["git", "-C", str(repository), "archive", revision], check=True, capture_output=True
    ).stdout
    destination.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        source.extractall(destination, filter="data")
    lock = json.loads((destination / "package-lock.json").read_text(encoding="utf-8"))
    dependencies = repository / "node_modules"
    for package in packages:
        expected = lock["packages"][f"node_modules/{package}"]["version"]
        manifest = dependencies / package / "package.json"
        if not manifest.is_file():
            raise ValueError(f"Install the pinned {package} dependency in {repository.name}")
        installed = json.loads(manifest.read_text(encoding="utf-8"))["version"]
        if installed != expected:
            raise ValueError(f"{package} does not match the pinned lockfile in {repository.name}")
    (destination / "node_modules").symlink_to(dependencies, target_is_directory=True)


def deploy(args: argparse.Namespace, endpoint: str, record: Path) -> None:
    coupon_revision = json.loads(COUPON_MANIFEST.read_text(encoding="utf-8"))["revision"]
    base_env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"DLP_RPC_URL", "DLP_PRIVATE_KEY", "HARDHAT_NETWORK"}
    }
    with TemporaryDirectory(prefix="reward-contracts-") as directory:
        workspace = Path(directory)
        point = workspace / "point"
        coupon = workspace / "coupon"
        stage_repository(
            args.point_repo.resolve(),
            POINT_REVISION,
            point,
            ("hardhat", "ethers", "@openzeppelin/contracts"),
        )
        stage_repository(
            args.coupon_repo.resolve(),
            coupon_revision,
            coupon,
            ("hardhat", "ethers", "@openzeppelin/contracts", "@openzeppelin/hardhat-upgrades"),
        )
        subprocess.run(
            ["node", "node_modules/hardhat/dist/src/cli.js", "build"],
            cwd=point,
            env={
                key: value for key, value in base_env.items() if key != "REWARD_DEPLOY_PRIVATE_KEY"
            },
            check=True,
        )
        (coupon / "reward-deploy.config.cjs").write_text(HARDHAT_CONFIG, encoding="utf-8")
        shutil.copy2(ROOT / "scripts/chain_deploy.cjs", coupon / "reward-deploy.cjs")
        record.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "node",
                "node_modules/hardhat/internal/cli/cli.js",
                "--config",
                "reward-deploy.config.cjs",
                "run",
                "reward-deploy.cjs",
                "--network",
                "rewardDeploy",
            ],
            cwd=coupon,
            env={
                **base_env,
                "REWARD_DEPLOY_RPC_URL": endpoint,
                "DEPLOY_NETWORK": args.network,
                "DEPLOY_CHAIN_ID": str(NETWORKS[args.network]),
                "DEPLOY_RECORD": str(record),
                "DEPLOY_COUPON_REVISION": coupon_revision,
                "DEPLOY_POINT_REVISION": POINT_REVISION,
                "DEPLOY_POINT_ARTIFACT": str(
                    point / "artifacts/contracts/pointFactory.sol/PointTokenFactory.json"
                ),
            },
            check=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", choices=NETWORKS, required=True)
    parser.add_argument("--record", type=Path, help="Deployment record path for this release")
    parser.add_argument(
        "--coupon-repo", type=Path, default=ROOT.parent / "blockchain-coupon-marketplace-loyalty"
    )
    parser.add_argument("--point-repo", type=Path, default=ROOT.parent / "blockchain_point_loyalty")
    args = parser.parse_args()
    endpoint = os.environ.get("REWARD_DEPLOY_RPC_URL")
    if not endpoint:
        if args.network == "dlp":
            parser.error("Set REWARD_DEPLOY_RPC_URL to the intended DLP testnet endpoint")
        endpoint = "http://127.0.0.1:8545"
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        parser.error("REWARD_DEPLOY_RPC_URL must be an HTTP(S) RPC endpoint")
    record = (args.record or ROOT / "deployments" / f"{args.network}.json").resolve()
    try:
        deploy(args, endpoint, record)
    except (ValueError, OSError) as error:
        parser.exit(1, f"Deployment failed: {error}\n")
    except subprocess.CalledProcessError:
        parser.exit(1, "Deployment failed while building or running the pinned contracts\n")


if __name__ == "__main__":
    main()
