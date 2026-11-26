"""Copy ABIs from a pinned contract commit, or verify the existing reference pack."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVISION = "1dc42d555046830c7320e46052696f62ab380dad"
CONTRACTS = ("Coupon", "Marketplace", "MarketplaceUpgradeable", "THB", "Vault")
TARGET = ROOT / "src/marketplace/infrastructure/blockchain/abi"


def reference_files(repo: Path) -> dict[str, bytes]:
    revision = subprocess.check_output(["git", "rev-parse", REVISION], cwd=repo, text=True).strip()
    files = {}
    for name in CONTRACTS:
        raw = subprocess.check_output(["git", "show", f"{revision}:abi/{name}.json"], cwd=repo)
        abi = json.loads(raw)
        if not isinstance(abi, list):
            raise ValueError(f"{name}: expected an ABI array")
        files[f"{name}.json"] = (json.dumps(abi, indent=2) + "\n").encode()
    manifest = {
        "repository": "https://github.com/corp-ais/blockchain-coupon-marketplace-loyalty",
        "revision": revision,
        "deployment_status": "unverified",
        "contracts": {
            filename: hashlib.sha256(content).hexdigest() for filename, content in files.items()
        },
    }
    files["manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo", type=Path, default=ROOT.parent / "blockchain-coupon-marketplace-loyalty"
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    files = reference_files(args.repo)
    if args.check:
        different = [
            name
            for name, data in files.items()
            if not (TARGET / name).exists() or (TARGET / name).read_bytes() != data
        ]
        if different:
            parser.exit(1, f"Contract reference drift: {', '.join(different)}\n")
        print(f"Contract reference matches {REVISION}; deployment remains unverified.")
    else:
        TARGET.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (TARGET / name).write_bytes(content)
        print(f"Pinned {len(CONTRACTS)} ABIs at {REVISION}; no deployment is implied.")


if __name__ == "__main__":
    main()
