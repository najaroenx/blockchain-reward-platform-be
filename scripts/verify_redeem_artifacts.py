"""Collect reproducible redeem review evidence using disposable local PostgreSQL."""

import argparse
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

ROOT = Path(__file__).resolve().parents[1]
ADMIN_ENV = "REDEEM_VERIFY_POSTGRES_ADMIN_URL"


def local_admin_url(value: str):
    try:
        url = make_url(value)
        if url.get_backend_name() != "postgresql" or url.host not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError
        return url.set(drivername="postgresql")
    except (ArgumentError, ValueError, TypeError):
        raise ValueError("Verification requires a loopback PostgreSQL admin URL") from None


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def junit_counts(path: Path) -> dict[str, int] | None:
    if not path.exists():
        return None
    try:
        suites = [
            suite
            for suite in ET.parse(path).getroot().iter("testsuite")
            if not suite.findall("testsuite")
        ]
    except ET.ParseError:
        return None
    counts = {
        key: sum(int(suite.get(key, "0")) for suite in suites)
        for key in ("tests", "failures", "errors", "skipped")
    }
    counts["passed"] = counts["tests"] - counts["failures"] - counts["errors"] - counts["skipped"]
    return counts


def check(name: str, command: list[str], env: dict[str, str], output: Path) -> dict[str, object]:
    print(f"Running {name}", flush=True)
    junit = output / f"{name}.junit.xml"
    options = ["--junitxml=" + str(junit)]
    if name == "standard":
        options.append("--ignore=tests/integration/coupons/test_execution_chain.py")
    environment = {**env, "PYTEST_ADDOPTS": shlex.join(options)}
    try:
        result = subprocess.run(
            command, cwd=ROOT, env=environment, text=True, capture_output=True, timeout=600
        )
        code, content = result.returncode, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        code, content = 124, "Verification command exceeded its 600-second limit.\n"
    # Credentials are passed only through the environment, never report arguments.
    content = re.sub(
        r"postgres(?:ql)?(?:\+psycopg)?://[^\s\"'<>]+", "postgresql://[REDACTED]", content
    )
    (output / f"{name}.txt").write_text(content)
    if junit.exists():
        junit.write_text(
            re.sub(
                r"postgres(?:ql)?(?:\+psycopg)?://[^\s\"'<>]+",
                "postgresql://[REDACTED]",
                junit.read_text(),
            )
        )
    counts = junit_counts(junit)
    status = (
        "PASS"
        if code == 0
        and counts is not None
        and not counts["failures"]
        and not counts["errors"]
        and not counts["skipped"]
        and counts["passed"]
        else "FAIL"
    )
    print(f"{name}: {status} {counts}", flush=True)
    return {
        "name": name,
        "status": status,
        "exit_code": code,
        "command": [arg.replace(str(ROOT) + "/", "") for arg in command],
        "junit": counts,
        "pytest_options": [option.replace(str(ROOT) + "/", "") for option in options],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="A new directory for this run")
    args = parser.parse_args()
    secret = os.environ.get(ADMIN_ENV)
    if not secret:
        parser.error(f"Set {ADMIN_ENV} to the disposable local PostgreSQL admin connection")
    try:
        admin = local_admin_url(secret)
    except ValueError as error:
        parser.error(str(error))
    output = args.output.resolve()
    if output.with_name(output.name + ".zip").exists():
        parser.error("The output ZIP already exists; choose a new output directory")
    output.mkdir(parents=True, exist_ok=False)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, text=True, capture_output=True
    ).stdout.strip()
    changed = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.splitlines()
    files = subprocess.run(
        [
            "git",
            "ls-files",
            "--",
            "src",
            "migrations",
            "scripts",
            "tests",
            "pyproject.toml",
            "uv.lock",
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.splitlines()
    source_hashes = {name: digest(ROOT / name) for name in files if (ROOT / name).is_file()}
    (output / "source-sha256.json").write_text(
        json.dumps(source_hashes, indent=2, sort_keys=True) + "\n"
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            ADMIN_ENV,
            "REDEEM_ADMISSION_TEST_DATABASE_URL",
            "REDEEM_AUTH_TEST_DATABASE_URL",
            "REDEEM_CHAIN_WORKSPACE",
            "PYTEST_ADDOPTS",
            "DLP_PRIVATE_KEY",
            "DLP_RPC_URL",
        }
    }
    stages = [check("standard", [sys.executable, "scripts/verify.py"], env, output)]
    database = "reward_redeem_artifact_" + uuid4().hex
    created, removed, version = False, False, None
    try:
        with psycopg.connect(
            admin.render_as_string(hide_password=False), autocommit=True, connect_timeout=3
        ) as connection:
            version = connection.execute("SHOW server_version").fetchone()[0]
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
            created = True
        child_url = admin.set(drivername="postgresql+psycopg", database=database).render_as_string(
            hide_password=False
        )
        pg_env = {
            **env,
            "REDEEM_ADMISSION_TEST_DATABASE_URL": child_url,
            "REDEEM_AUTH_TEST_DATABASE_URL": child_url,
        }
        stages.append(
            check(
                "postgres",
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "tests/integration/coupons/test_admission.py",
                    "tests/integration/coupons/test_jwt_auth.py",
                    "-q",
                ],
                pg_env,
                output,
            )
        )
        stages.append(
            check("local-chain", [sys.executable, "scripts/verify_redeem_chain.py"], pg_env, output)
        )
    except psycopg.Error:
        stages.append(
            {
                "name": "postgres-setup",
                "status": "FAIL",
                "reason": "Local PostgreSQL setup or cleanup failed; connection details omitted",
            }
        )
    finally:
        if created:
            try:
                with psycopg.connect(
                    admin.render_as_string(hide_password=False), autocommit=True, connect_timeout=3
                ) as connection:
                    connection.execute(
                        sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database))
                    )
                    removed = (
                        connection.execute(
                            "SELECT 1 FROM pg_database WHERE datname = %s", (database,)
                        ).fetchone()
                        is None
                    )
            except psycopg.Error:
                stages.append(
                    {
                        "name": "database-cleanup",
                        "status": "FAIL",
                        "reason": "Disposable test database cleanup failed",
                    }
                )
    finished_hashes = {name: digest(ROOT / name) for name in source_hashes}
    if finished_hashes != source_hashes:
        stages.append(
            {
                "name": "source-integrity",
                "status": "FAIL",
                "reason": "Source files changed during verification",
            }
        )
    passed = len(stages) == 3 and all(stage["status"] == "PASS" for stage in stages) and removed
    revision = json.loads(
        (ROOT / "src/marketplace/infrastructure/blockchain/abi/manifest.json").read_text()
    )["revision"]
    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "backend_commit": head,
        "tracked_changes_at_start": changed,
        "contract_revision": revision,
        "scope": "Approved local chain; no production deployment",
        "python": platform.python_version(),
        "postgresql_version": version,
        "temporary_database_removed": removed,
        "database_needing_cleanup": database if created and not removed else None,
        "all_automated_checks_passed": passed,
        "acceptance_complete": False,
        "remaining_acceptance": [3],
        "separate_schema_work": "FEAT-11 T8 in issue #213",
        "stages": stages,
    }
    (output / "manifest.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    lines = [
        "# Redeem verification artifact",
        "",
        f"Backend commit: `{head}`",
        f"Contract revision: `{revision}`",
        "",
        f"Automated checks: **{'PASS' if passed else 'FAIL / INCOMPLETE'}**.",
        "AC3 wallet mapping remains pending.",
        "Scope: approved local chain. FEAT-11 T8 is separate schema work in #213.",
        "",
        "| Stage | Result | Tests |",
        "|---|---|---|",
    ]
    for stage in stages:
        lines.append(f"| {stage['name']} | {stage['status']} | {stage.get('junit', '')} |")
    lines += [
        "",
        "| AC | Evidence |",
        "|---|---|",
        "| 1, 15 | standard.junit.xml; schema, API and artifact validation |",
        "| 2 | postgres.junit.xml; signed-JWT rejection tests |",
        "| 3 | PENDING, not claimed complete |",
        "| 4–8 | postgres.junit.xml; actual SQL admission, replay, concurrency and rollback |",
        "| 9–13 | local-chain.junit.xml; handler/projector with pinned EVM and PostgreSQL |",
        "| 14 | Recorded mapping in docs/redeem-state.md and #213. Legacy SQL not rerun. |",
        "",
        "Each stage has its command and exit code in manifest.json, JUnit results, and a text log.",
        "source-sha256.json identifies the tested source files. No environment dump is included.",
        "The standard stage excludes chain cases; the local-chain stage runs them separately.",
        "API/Postman files are copies of committed review assets. Their mock values are fixtures.",
        "Verify extracted files with: `shasum -a 256 -c SHA256SUMS`.",
        "",
    ]
    (output / "README.md").write_text("\n".join(lines))
    for source in (
        "docs/api/redeem.openapi.json",
        "postman/redeem.postman_collection.json",
        "postman/local.postman_environment.json",
    ):
        target = output / source
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / source, target)
    members = sorted(path for path in output.rglob("*") if path.is_file())
    (output / "SHA256SUMS").write_text(
        "".join(f"{digest(path)}  {path.relative_to(output).as_posix()}\n" for path in members)
    )
    archive = output.with_name(output.name + ".zip")
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(output.rglob("*")):
            if path.is_file():
                bundle.write(path, path.relative_to(output).as_posix())
    print(f"Artifact: {archive}\nSHA-256: {digest(archive)}", flush=True)
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
