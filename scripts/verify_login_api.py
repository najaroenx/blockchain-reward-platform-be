"""Export/check login API artifacts; optionally run Newman with SQLite or PostgreSQL."""

import argparse
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import uvicorn
from openapi_spec_validator import validate
from psycopg import sql
from sqlalchemy.engine import make_url

from marketplace.auth.application.dto import AccessClaims
from marketplace.auth.domain.entities import LoginPolicy
from marketplace.auth.infrastructure.models import Merchant, MerchantAdmin
from marketplace.auth.infrastructure.passwords import Argon2PasswordVerifier
from marketplace.auth.presentation.router import AuthDependencies
from marketplace.infrastructure.database import Database
from marketplace.main import create_app
from marketplace.settings import ApiSettings

ROOT = Path(__file__).resolve().parents[1]
URL = "/v1/auth/login"
PASSWORD = "fixture-only-password"
SPEC = ROOT / "docs/api/login.openapi.json"
COLLECTION = ROOT / "postman/login.postman_collection.json"
ENVIRONMENT = ROOT / "postman/login.local.postman_environment.json"


def document() -> dict[str, Any]:
    schema = create_app(ApiSettings(database_url="sqlite:///:memory:", APP_ENV="test")).openapi()
    schema["paths"] = {URL: schema["paths"][URL]}
    schema["info"]["title"] = "Merchant login API"
    schema["servers"] = [{"url": "http://127.0.0.1:3000", "description": "Local development"}]
    return schema


def item(
    name: str, body: Any, status: int, code: str | None = None, *, forwarded: str | None = None
) -> dict[str, Any]:
    tests = [
        f'pm.test("HTTP {status}", function () {{ pm.response.to.have.status({status}); }});',
        'pm.test("No cache", function () {',
        '  pm.expect(pm.response.headers.get("Cache-Control")).to.eql("no-store"); });',
        'pm.test("No password echo", function () {',
        '  pm.expect(pm.response.text()).not.to.include("fixture-only-password"); });',
    ]
    if code:
        tests += [
            'pm.test("Error envelope", function () { const data = pm.response.json();',
            f'  pm.expect(data.error.code).to.eql("{code}");',
            "  pm.expect(data.error.requestId).to.match(/^req_/);",
            '  pm.expect(pm.response.headers.has("Set-Cookie")).to.eql(false);',
            "});",
        ]
    else:
        tests += [
            'pm.test("Login body", function () { const data = pm.response.json();',
            "  pm.expect(Object.keys(data).sort()).to.eql(",
            '    ["accessToken", "expiresIn", "subject", "tokenType"]);',
            '  pm.expect(data.expiresIn).to.eql(900); pm.expect(data.tokenType).to.eql("Bearer");',
            '  pm.expect(data.subject.kind).to.eql("MERCHANT_ADMIN");',
            '  pm.expect(data.subject.merchantStatus).to.eql("PENDING");',
            "});",
            'pm.test("Secure refresh cookie", function () {',
            '  const cookie = pm.response.headers.get("Set-Cookie");',
            '  ["HttpOnly", "Secure", "SameSite=strict", "Path=/v1/auth",',
            '    "Max-Age=2592000"].forEach(function (flag) {',
            "      pm.expect(cookie).to.include(flag); });",
            "});",
        ]
    if status in (423, 429):
        tests.append(
            'pm.test("Retry-After", function () { '
            'pm.expect(Number(pm.response.headers.get("Retry-After"))).to.be.above(0); });'
        )
    headers = [{"key": "Content-Type", "value": "application/json"}]
    if forwarded:
        headers.append({"key": "X-Forwarded-For", "value": forwarded})
    return {
        "name": name,
        "request": {
            "method": "POST",
            "auth": {"type": "noauth"},
            "url": "{{baseUrl}}" + URL,
            "header": headers,
            "body": {
                "mode": "raw",
                "raw": json.dumps(body),
                "options": {"raw": {"language": "json"}},
            },
        },
        "event": [{"listen": "test", "script": {"type": "text/javascript", "exec": tests}}],
    }


def collection() -> dict[str, Any]:
    local = [
        item("Missing fields", {}, 400, "VALIDATION_FAILED"),
        item("Invalid email", {"email": "invalid", "password": PASSWORD}, 400, "VALIDATION_FAILED"),
        item(
            "Extra field",
            {"email": "owner@shop.example", "password": PASSWORD, "wallet": "forbidden"},
            400,
            "VALIDATION_FAILED",
        ),
        item(
            "Unknown email",
            {"email": "unknown-{{$guid}}@shop.example", "password": PASSWORD},
            401,
            "INVALID_CREDENTIALS",
        ),
        item(
            "Invalid password type",
            {"email": "owner@shop.example", "password": 123},
            400,
            "VALIDATION_FAILED",
        ),
    ]
    fixtures = [
        item(
            "Login succeeds with signer fixture",
            {"email": "owner@shop.example", "password": PASSWORD},
            200,
        ),
        item(
            "Wrong password",
            {"email": "owner@shop.example", "password": "wrong"},
            401,
            "INVALID_CREDENTIALS",
        ),
        item(
            "Disabled admin",
            {"email": "disabled@shop.example", "password": PASSWORD},
            401,
            "INVALID_CREDENTIALS",
        ),
        item(
            "Unknown account",
            {"email": "missing@shop.example", "password": PASSWORD},
            401,
            "INVALID_CREDENTIALS",
        ),
        item(
            "Signer failure",
            {"email": "signer-fail@shop.example", "password": PASSWORD},
            503,
            "AUTH_UNAVAILABLE",
        ),
    ]
    for n in range(5):
        fixtures.append(
            item(
                f"Lockout failure {n + 1}",
                {"email": "lock@shop.example", "password": "wrong"},
                401,
                "INVALID_CREDENTIALS",
            )
        )
    fixtures.append(
        item(
            "Locked even with correct password",
            {"email": "lock@shop.example", "password": PASSWORD},
            423,
            "ACCOUNT_LOCKED",
        )
    )
    for n in range(8):
        fixtures.append(
            item(
                f"Fill IP window {n + 1}",
                {"email": f"missing{n}@shop.example", "password": PASSWORD},
                401,
                "INVALID_CREDENTIALS",
                forwarded=f"203.0.113.{n + 1}",
            )
        )
    fixtures.append(
        item(
            "Forwarded header cannot bypass peer IP limit",
            {"email": "another@shop.example", "password": PASSWORD},
            429,
            "RATE_LIMITED",
            forwarded="203.0.113.250",
        )
    )
    return {
        "info": {
            "name": "BE-0306 merchant login",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            "description": (
                "Local validation needs migrated tables. Fixture login must run through "
                "verify_login_api.py against its isolated seeded SQLite app. "
                "The signer fixture returns an opaque test value, not a real JWT. "
                "No #182 signing or PostgreSQL deployment is claimed."
            ),
        },
        "item": [
            {"name": "Local validation", "item": local},
            {"name": "Fixture login", "item": fixtures},
        ],
    }


class FixtureSigner:
    def sign(self, claims: AccessClaims) -> str:
        if claims.sub == "adm_signer-fail":
            raise RuntimeError("private signer fixture failure")
        return "fixture-access-" + claims.jti


@contextmanager
def fixture_database(path: Path, postgres: bool) -> Iterator[str]:
    if not postgres:
        yield "sqlite:///" + str(path / "auth.db")
        return
    parsed = make_url(os.environ["TEST_POSTGRES_URL"])
    if parsed.get_backend_name() != "postgresql":
        raise ValueError("TEST_POSTGRES_URL must identify a PostgreSQL admin database")
    name = "reward_http_" + uuid4().hex
    admin_url = parsed.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            yield parsed.set(database=name).render_as_string(hide_password=False)
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def run_newman(executable: str, *, postgres: bool = False) -> None:
    with (
        tempfile.TemporaryDirectory(prefix="be0306-http-") as directory,
        fixture_database(Path(directory), postgres) as url,
    ):
        path = Path(directory)
        environment = dict(os.environ, DATABASE_URL=url, APP_ENV="test")
        for key in ("KMS_KEY_ARN", "KMS_REGION"):
            environment.pop(key, None)
        subprocess.run(
            [str(ROOT / ".venv/bin/alembic"), "upgrade", "head"],
            env=environment,
            cwd=ROOT,
            check=True,
        )
        hasher = Argon2PasswordVerifier()
        encoded_password = hasher.hasher.hash(PASSWORD)
        database = Database(url)
        try:
            with database.sessions.begin() as session:
                session.add(Merchant(id="mch_fixture", name="Fixture merchant", status="PENDING"))
                session.flush()
                for name in ("owner", "disabled", "signer-fail", "lock"):
                    from datetime import UTC, datetime

                    email = name + "@shop.example"
                    session.add(
                        MerchantAdmin(
                            id="adm_" + name,
                            merchant_id="mch_fixture",
                            email=email,
                            email_normalized=email,
                            password_hash=encoded_password,
                            display_name=name,
                            disabled_at=datetime.now(UTC) if name == "disabled" else None,
                        )
                    )
        finally:
            database.close()
        app = create_app(
            ApiSettings(database_url=url, APP_ENV="test"),
            auth=AuthDependencies(hasher, FixtureSigner(), LoginPolicy()),
        )
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen()
            server = uvicorn.Server(
                uvicorn.Config(app, access_log=False, log_level="critical", proxy_headers=False)
            )
            thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
            thread.start()
            try:
                deadline = time.monotonic() + 10
                while not server.started:
                    if not thread.is_alive() or time.monotonic() > deadline:
                        raise RuntimeError("Fixture API did not start")
                    time.sleep(0.01)
                report = path / "newman.json"
                completed = subprocess.run(
                    [
                        executable,
                        "run",
                        str(COLLECTION),
                        "--environment",
                        str(ENVIRONMENT),
                        "--env-var",
                        f"baseUrl=http://127.0.0.1:{sock.getsockname()[1]}",
                        "--reporters",
                        "json",
                        "--reporter-json-export",
                        str(report),
                        "--timeout-request",
                        "10000",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if not report.exists():
                    raise RuntimeError(completed.stderr)
                run = json.loads(report.read_text())["run"]
                requests = run["stats"]["requests"]["total"]
                assertions = run["stats"]["assertions"]["total"]
                print(
                    f"Newman: {requests} requests, {assertions} assertions, "
                    f"{len(run['failures'])} failures"
                )
                if completed.returncode or run["failures"] or requests != 25 or not assertions:
                    raise RuntimeError(json.dumps(run["failures"], ensure_ascii=False))
            finally:
                server.should_exit = True
                thread.join(timeout=10)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", action="store_true")
    parser.add_argument("--newman", help="Path to a Newman executable for isolated fixture checks")
    parser.add_argument(
        "--postgres", action="store_true", help="Use a disposable database on TEST_POSTGRES_URL"
    )
    args = parser.parse_args()
    if args.postgres and (not args.newman or not os.environ.get("TEST_POSTGRES_URL")):
        parser.error("--postgres requires --newman and TEST_POSTGRES_URL")
    schema = document()
    validate(schema)
    artifacts = {
        SPEC: schema,
        COLLECTION: collection(),
        ENVIRONMENT: {
            "name": "Local merchant login",
            "_postman_variable_scope": "environment",
            "values": [{"key": "baseUrl", "value": "http://127.0.0.1:3000", "enabled": True}],
        },
    }
    for path, value in artifacts.items():
        encoded = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        if args.export:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(encoded)
        elif not path.exists() or path.read_text() != encoded:
            raise SystemExit(f"Stale API artifact: {path.relative_to(ROOT)}; run with --export")
    print("Login OpenAPI validated; generated OpenAPI/Postman artifacts match runtime.")
    if args.newman:
        run_newman(args.newman, postgres=args.postgres)


if __name__ == "__main__":
    main()
