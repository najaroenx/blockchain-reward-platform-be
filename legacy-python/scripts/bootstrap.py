"""Install project dependencies without changing global Python packages."""

import argparse
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--lock", action="store_true", help="Resolve dependencies before installation"
    )
    args = parser.parse_args()
    if sys.version_info < (3, 12):
        parser.exit(2, "Run this installer with Python 3.12 or newer.\n")
    tools = ROOT / ".tools" / "uv"
    if not (tools / "bin" / "uv").exists():
        venv.create(tools, with_pip=True)
        subprocess.run(
            [str(tools / "bin" / "python"), "-m", "pip", "install", "uv==0.13.7"], check=True
        )
    uv = str(tools / "bin" / "uv")
    if args.lock:
        subprocess.run([uv, "lock", "--python", sys.executable], cwd=ROOT, check=True)
    subprocess.run([uv, "sync", "--locked", "--python", sys.executable], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
