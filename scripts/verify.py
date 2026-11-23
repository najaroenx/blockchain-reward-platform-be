"""Run the checks against the installed project, stopping on the first failure."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BIN = Path(sys.executable).parent

for command in (
    [str(BIN / "ruff"), "check", "."],
    [str(BIN / "ruff"), "format", "--check", "."],
    [str(BIN / "mypy")],
    [str(BIN / "lint-imports")],
    [str(BIN / "python"), "scripts/verify_login_api.py"],
    [str(BIN / "pytest"), "--cov=marketplace", "--cov-report=term-missing"],
):
    print("Running:", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)
