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
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from secrets import token_urlsafe
from typing import Any
from uuid import uuid4

import jwt
import psycopg
import uvicorn
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from openapi_spec_validator import validate
from psycopg import sql
from sqlalchemy import func, select
from sqlalchemy.engine import make_url

from marketplace.auth.application.dto import AccessClaims, CustomerAccessClaims
from marketplace.auth.domain.entities import LoginPolicy
from marketplace.auth.infrastructure.models import AuthSession, Merchant, MerchantAdmin
from marketplace.auth.infrastructure.passwords import Argon2PasswordVerifier
from marketplace.auth.infrastructure.signing import Es256TokenSigner
from marketplace.auth.presentation.router import AuthDependencies
from marketplace.infrastructure.database import Database
from marketplace.main import create_app
from marketplace.settings import ApiSettings

ROOT = Path(__file__).resolve().parents[1]
URL = "/v1/auth/login"
JWKS_URL = "/.well-known/jwks.json"
REFRESH_URL = "/v1/auth/refresh"
JWT_ISSUER = "https://auth.example.test"
JWT_AUDIENCE = "reward-api"
PASSWORD = "fixture-only-password"
SPEC = ROOT / "docs/api/login.openapi.json"
COLLECTION = ROOT / "postman/login.postman_collection.json"
ENVIRONMENT = ROOT / "postman/login.local.postman_environment.json"


def document() -> dict[str, Any]:
    schema = create_app(ApiSettings(database_url="sqlite:///:memory:", APP_ENV="test")).openapi()
    schema["paths"] = {path: schema["paths"][path] for path in (URL, JWKS_URL, REFRESH_URL)}
    schema["info"]["title"] = "Authentication API"
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
            'pm.collectionVariables.set("loginRefresh",',
            '  pm.response.headers.get("Set-Cookie").match(/refreshToken=([^;]+)/)[1]);',
            'pm.test("JWT envelope", function () {',
            "  pm.expect(pm.response.json().accessToken)",
            "    .to.match(/^[\\w-]+\\.[\\w-]+\\.[\\w-]+$/); });",
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


def refresh_item(
    name: str, token: str, status: int, code: str | None = None, save: str | None = None
) -> dict[str, Any]:
    tests = [
        f'pm.test("HTTP {status}", function () {{ pm.response.to.have.status({status}); }});',
        'pm.test("No cache", function () {',
        '  pm.expect(pm.response.headers.get("Cache-Control")).to.eql("no-store"); });',
    ]
    if code:
        tests += [
            'pm.test("Refresh error", function () { const data=pm.response.json();',
            f'  pm.expect(data.error.code).to.eql("{code}");',
            "  pm.expect(data.error.requestId).to.match(/^req_/); });",
        ]
        if status == 401:
            tests += [
                'pm.test("Clear rejected cookie", function () {',
                '  pm.expect(pm.response.headers.get("Set-Cookie")).to.include("Max-Age=0"); });',
            ]
        else:
            tests += [
                'pm.test("Retain cookie on infrastructure failure", function () {',
                '  pm.expect(pm.response.headers.has("Set-Cookie")).to.eql(false); });',
            ]
    else:
        tests += [
            'pm.test("Refresh body", function () { const data=pm.response.json();',
            "  pm.expect(Object.keys(data).sort())",
            '    .to.eql(["accessToken","expiresIn","tokenType"]);',
            "  pm.expect(data.expiresIn).to.eql(900);",
            '  pm.expect(data.tokenType).to.eql("Bearer"); });',
            'pm.test("Rotated secure cookie", function () {',
            '  const cookie=pm.response.headers.get("Set-Cookie");',
            '  ["Secure","HttpOnly","SameSite=strict","Path=/v1/auth","Max-Age=2592000"]',
            "    .forEach(function (flag) { pm.expect(cookie).to.include(flag); }); });",
            f'pm.collectionVariables.set("{save}",',
            '  pm.response.headers.get("Set-Cookie").match(/refreshToken=([^;]+)/)[1]);',
        ]
    return {
        "name": name,
        "protocolProfileBehavior": {"disableCookies": True},
        "request": {
            "method": "POST",
            "auth": {"type": "noauth"},
            "url": "{{baseUrl}}" + REFRESH_URL,
            "header": [{"key": "Cookie", "value": "refreshToken=" + token}],
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
        {
            "name": "Public signing keys",
            "request": {
                "method": "GET",
                "auth": {"type": "noauth"},
                "url": "{{baseUrl}}" + JWKS_URL,
            },
            "event": [
                {
                    "listen": "test",
                    "script": {
                        "type": "text/javascript",
                        "exec": [
                            'pm.test("JWKS HTTP 200", function () {',
                            "  pm.response.to.have.status(200); });",
                            'pm.test("Public ES256 key", function () {',
                            "  const key=pm.response.json().keys[0];",
                            '  pm.expect(key.alg).to.eql("ES256");',
                            '  pm.expect(key.crv).to.eql("P-256");',
                            "  pm.expect(Object.keys(key).sort()).to.eql(",
                            '    ["alg","crv","kid","kty","use","x","y"]); });',
                            'pm.test("Public cache", function () {',
                            '  pm.expect(pm.response.headers.get("Cache-Control"))',
                            '    .to.eql("public, max-age=60"); });',
                        ],
                    },
                }
            ],
        },
        item(
            "Login succeeds with ES256 JWT",
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
            "name": "Authentication login, refresh and ES256 signing",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            "description": (
                "Local validation needs migrated tables. Fixture login must run through "
                "verify_login_api.py against its isolated seeded SQLite or PostgreSQL app. "
                "Access tokens use real ES256 signatures with an ephemeral PEM key. "
                "The verifier checks signatures using the public key returned by HTTP JWKS. "
                "The signer-fail account injects a failure before calling the real signer."
            ),
        },
        "item": [
            {"name": "Local validation", "item": local},
            {"name": "Fixture login", "item": fixtures},
            {
                "name": "Fixture refresh",
                "item": [
                    refresh_item(
                        "Refresh rotates login session",
                        "{{loginRefresh}}",
                        200,
                        save="rotatedRefresh",
                    ),
                    refresh_item(
                        "Refresh rotates again", "{{rotatedRefresh}}", 200, save="descendantRefresh"
                    ),
                    refresh_item(
                        "Refresh detects old token reuse",
                        "{{loginRefresh}}",
                        401,
                        "SESSION_COMPROMISED",
                    ),
                    refresh_item(
                        "Descendant is revoked",
                        "{{descendantRefresh}}",
                        401,
                        "REFRESH_TOKEN_REVOKED",
                    ),
                    refresh_item("Missing refresh cookie", "", 401, "REFRESH_TOKEN_INVALID"),
                    refresh_item("Unknown refresh cookie", "a" * 43, 401, "REFRESH_TOKEN_INVALID"),
                    refresh_item(
                        "Expired refresh cookie", "{{expiredRefresh}}", 401, "REFRESH_TOKEN_EXPIRED"
                    ),
                    refresh_item(
                        "Revoked refresh cookie", "{{revokedRefresh}}", 401, "REFRESH_TOKEN_REVOKED"
                    ),
                    refresh_item(
                        "Disabled account refresh",
                        "{{disabledRefresh}}",
                        401,
                        "REFRESH_TOKEN_REVOKED",
                    ),
                    refresh_item(
                        "Refresh signer failure", "{{failedSignerRefresh}}", 503, "AUTH_UNAVAILABLE"
                    ),
                ],
            },
        ],
    }


class FixtureSigner:
    def __init__(self, signer: Es256TokenSigner) -> None:
        self.signer = signer

    def sign(self, claims: AccessClaims | CustomerAccessClaims) -> str:
        if claims.sub == "adm_signer-fail":
            raise RuntimeError("private signer fixture failure")
        return self.signer.sign(claims)


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
        key_file = path / "jwt-secret.pem"
        key_file.write_bytes(
            ec.generate_private_key(ec.SECP256R1()).private_bytes(
                Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
            )
        )
        key_file.chmod(0o600)
        signer = Es256TokenSigner(key_file, issuer=JWT_ISSUER, audience=JWT_AUDIENCE)
        encoded_password = hasher.hasher.hash(PASSWORD)
        fixture_tokens = {
            name: token_urlsafe(32)
            for name in (
                "expiredRefresh",
                "revokedRefresh",
                "disabledRefresh",
                "failedSignerRefresh",
            )
        }
        database = Database(url)
        try:
            with database.sessions.begin() as session:
                session.add(Merchant(id="mch_fixture", name="Fixture merchant", status="PENDING"))
                session.flush()
                for name in ("owner", "disabled", "signer-fail", "lock"):
                    email = name + "@shop.example"
                    session.add(
                        MerchantAdmin(
                            id="adm_" + name,
                            merchant_id="mch_fixture",
                            email=email,
                            email_normalized=email,
                            password_hash=encoded_password,
                            display_name=name,
                            disabled_at=None,
                        )
                    )
                session.flush()
                now = datetime.now(UTC)
                for name, token in fixture_tokens.items():
                    owner = (
                        "adm_disabled"
                        if name == "disabledRefresh"
                        else ("adm_signer-fail" if name == "failedSignerRefresh" else "adm_owner")
                    )
                    session.add(
                        AuthSession(
                            id="ses_fixture_" + name,
                            subject_kind="MERCHANT_ADMIN",
                            merchant_admin_id=owner,
                            family_id="fam_fixture_" + name,
                            refresh_token_hash=sha256(token.encode()).digest(),
                            created_at=now - timedelta(days=31)
                            if name == "expiredRefresh"
                            else now,
                            expires_at=now - timedelta(seconds=1)
                            if name == "expiredRefresh"
                            else now + timedelta(days=30),
                            revoked_at=now if name == "revokedRefresh" else None,
                            revoked_reason="LOGOUT" if name == "revokedRefresh" else None,
                        )
                    )
                session.flush()
                session.get(MerchantAdmin, "adm_disabled").disabled_at = now
        finally:
            database.close()
        app = create_app(
            ApiSettings(database_url=url, APP_ENV="test"),
            auth=AuthDependencies(
                hasher, FixtureSigner(signer), LoginPolicy(), key_publisher=signer
            ),
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
                        *[
                            arg
                            for name, token in fixture_tokens.items()
                            for arg in ("--env-var", name + "=" + token)
                        ],
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
                executions = run["executions"]
                jwks = next(
                    json.loads(bytes(entry["response"]["stream"]["data"]))
                    for entry in executions
                    if entry["item"]["name"] == "Public signing keys"
                )
                verified = 0
                issued_sessions: set[str] = set()
                for entry in executions:
                    if entry["request"]["method"] != "POST" or entry["response"]["code"] != 200:
                        continue
                    token = json.loads(bytes(entry["response"]["stream"]["data"]))["accessToken"]
                    header = jwt.get_unverified_header(token)
                    public = next(key for key in jwks["keys"] if key["kid"] == header["kid"])
                    decoded = jwt.decode(
                        token,
                        jwt.PyJWK.from_dict(public).key,
                        algorithms=["ES256"],
                        issuer=JWT_ISSUER,
                        audience=JWT_AUDIENCE,
                    )
                    if decoded["exp"] - decoded["iat"] != 900:
                        raise RuntimeError("JWT lifetime does not match the login contract")
                    verified += 1
                    issued_sessions.add(decoded["sid"])
                requests = run["stats"]["requests"]["total"]
                assertions = run["stats"]["assertions"]["total"]
                print(
                    f"Newman: {requests} requests, {assertions} assertions, "
                    f"{len(run['failures'])} failures"
                )
                if (
                    completed.returncode
                    or run["failures"]
                    or requests != 36
                    or not assertions
                    or verified != 3
                ):
                    failures = [
                        {
                            "request": failure.get("source", {}).get("name"),
                            "message": failure.get("error", {}).get("message"),
                        }
                        for failure in run["failures"]
                    ]
                    raise RuntimeError(
                        json.dumps(
                            {"failures": failures, "verifiedJwt": verified}, ensure_ascii=False
                        )
                    )
                print(f"Verified {verified} ES256 JWT using HTTP JWKS and issuer/audience checks")
                with app.state.database.sessions() as session:
                    issued = session.scalars(
                        select(AuthSession).where(AuthSession.id.in_(issued_sessions))
                    ).all()
                    if len(issued) != 3 or any(row.revoked_at is None for row in issued):
                        raise RuntimeError("Replay did not revoke all issued sessions")
                    live = session.scalar(
                        select(func.count())
                        .select_from(AuthSession)
                        .where(
                            AuthSession.family_id == issued[0].family_id,
                            AuthSession.revoked_at.is_(None),
                        )
                    )
                    failed = session.get(AuthSession, "ses_fixture_failedSignerRefresh")
                    if live or failed.used_at is not None or failed.revoked_at is not None:
                        raise RuntimeError("Replay/failure session invariants did not hold")
                print("Verified family revocation and signer-failure rollback in the database")
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
