"""Export/check AC15 artifacts and optionally run Newman against isolated adapter fixtures."""

import argparse
import json
import socket
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from jsonschema import Draft202012Validator
from openapi_spec_validator import validate
from sqlalchemy.exc import OperationalError

from marketplace.coupons.application.dto import (
    AuthenticatedWallet,
    CouponDeployment,
    RedeemCouponPayload,
    RedeemCouponResult,
)
from marketplace.coupons.application.exceptions import RedeemErrorCode, RedeemFailure
from marketplace.coupons.application.use_cases import RedeemCoupons
from marketplace.coupons.infrastructure.contract_errors import classify_redeem_revert
from marketplace.coupons.presentation.errors import ERRORS
from marketplace.coupons.presentation.router import RedeemDependencies
from marketplace.main import create_app
from marketplace.settings import ApiSettings

ROOT = Path(__file__).resolve().parents[1]
URL = "/v1/coupons/redeem"
SPEC = ROOT / "docs/api/redeem.openapi.json"
COLLECTION = ROOT / "postman/redeem.postman_collection.json"
ENVIRONMENT = ROOT / "postman/local.postman_environment.json"
MAX_TYPE_ID = str(2**256 - 1)
# These are adapter-fixture scenarios, not deployed coupon IDs or database operations.
FAILURES = {
    2: ("COUPON_TYPE_NOT_FOUND", 404),
    3: ("INSUFFICIENT_BALANCE", 422),
    4: ("COUPON_NOT_STARTED", 422),
    5: ("COUPON_EXPIRED", 422),
    6: ("NO_REDEEM_CODE_AVAILABLE", 422),
    7: ("IDEMPOTENCY_KEY_IN_PROGRESS", 409),
    8: ("IDEMPOTENCY_KEY_REUSED", 409),
    9: ("SIGNER_UNAUTHORIZED", 503),
    10: ("RATE_LIMITED", 429),
    11: ("ADMISSION_UNAVAILABLE", 503),
    12: ("CONTRACT_CALL_FAILED", 503),
    13: ("INTERNAL_ERROR", 500),
    14: ("INTERNAL_ERROR", 500),
    15: ("INTERNAL_ERROR", 500),
    16: ("INTERNAL_ERROR", 500),
}


def openapi_document() -> dict[str, Any]:
    document = create_app(ApiSettings(database_url="sqlite:///:memory:", APP_ENV="test")).openapi()
    document["paths"] = {URL: document["paths"][URL]}
    needed: set[str] = set()

    def references(value: Any) -> None:
        if isinstance(value, dict):
            ref = value.get("$ref", "")
            if ref.startswith("#/components/schemas/"):
                needed.add(ref.rsplit("/", 1)[1])
            for child in value.values():
                references(child)
        elif isinstance(value, list):
            for child in value:
                references(child)

    references(document["paths"])
    visited: set[str] = set()
    while needed - visited:
        name = next(iter(needed - visited))
        visited.add(name)
        references(document["components"]["schemas"][name])
    document["components"]["schemas"] = {
        name: document["components"]["schemas"][name] for name in sorted(needed)
    }
    document["info"]["title"] = "Coupon redeem admission API"
    document["servers"] = [{"url": "http://127.0.0.1:3000", "description": "Local development"}]
    return document


def request_item(
    name: str,
    body: dict[str, Any],
    status: int,
    code: str | None = None,
    *,
    token: str | None = "{{accessToken}}",
    key: bool = True,
) -> dict[str, Any]:
    headers = [{"key": "Content-Type", "value": "application/json"}]
    if key:
        headers.append({"key": "Idempotency-Key", "value": "redeem-postman-{{$guid}}"})
    auth = (
        {"type": "noauth"}
        if token is None
        else {"type": "bearer", "bearer": [{"key": "token", "value": token, "type": "string"}]}
    )
    request = {
        "method": "POST",
        "auth": auth,
        "header": headers,
        "url": "{{baseUrl}}" + URL,
        "body": {"mode": "raw", "raw": json.dumps(body), "options": {"raw": {"language": "json"}}},
    }
    expected = (
        {"operationId": "op_fixture", "status": "PENDING", "typeId": body["typeId"], "amount": "1"}
        if code is None
        else {"error": {"code": code, "message": ERRORS[RedeemErrorCode(code)].message}}
    )
    schema = (
        {
            "type": "object",
            "required": ["operationId", "status", "typeId", "amount"],
            "additionalProperties": False,
            "properties": {
                "operationId": {"type": "string", "minLength": 1},
                "status": {"enum": ["PENDING"]},
                "typeId": {"enum": [body["typeId"]]},
                "amount": {"enum": ["1"]},
            },
        }
        if code is None
        else {
            "type": "object",
            "required": ["error"],
            "additionalProperties": False,
            "properties": {
                "error": {
                    "type": "object",
                    "required": ["code", "message"],
                    "additionalProperties": False,
                    "properties": {"code": {"enum": [code]}, "message": {"type": "string"}},
                }
            },
        }
    )
    tests = [
        f'pm.test("HTTP {status}", function () {{ pm.response.to.have.status({status}); }});',
        'pm.test("Response contract", function () { pm.response.to.have.jsonSchema('
        + json.dumps(schema)
        + "); });",
        'pm.test("No infrastructure detail or token", function () {',
        '  pm.expect(pm.response.text()).not.to.include("private-fixture");',
        '  pm.expect(pm.response.text()).not.to.include(pm.environment.get("accessToken"));',
        "});",
    ]
    if status == 401:
        tests.append(
            'pm.test("Bearer challenge", function () { '
            'pm.expect(pm.response.headers.get("WWW-Authenticate")).to.eql("Bearer"); });'
        )
    if status == 429:
        tests.append(
            'pm.test("Retry-After", function () { '
            'pm.expect(pm.response.headers.get("Retry-After")).to.eql("7"); });'
        )
    return {
        "name": name,
        "request": request,
        "event": [{"listen": "test", "script": {"type": "text/javascript", "exec": tests}}],
        "response": [
            {
                "name": name,
                "originalRequest": request,
                "code": status,
                "header": [{"key": "Content-Type", "value": "application/json"}],
                "body": json.dumps(expected, indent=2),
            }
        ],
    }


def postman_collection() -> dict[str, Any]:
    local = [
        request_item("Missing token", {"typeId": "1"}, 401, "UNAUTHENTICATED", token=None),
        request_item("Wrong token", {"typeId": "1"}, 401, "UNAUTHENTICATED", token="wrong-token"),
        request_item("Missing key", {"typeId": "1"}, 400, "IDEMPOTENCY_KEY_REQUIRED", key=False),
    ]
    for name, body in [
        ("Missing type", {}),
        ("Zero type", {"typeId": "0"}),
        ("Noncanonical type", {"typeId": "01"}),
        ("Numeric type", {"typeId": 1}),
        ("Overflow", {"typeId": str(2**256)}),
        ("Extra amount", {"typeId": "1", "amount": "2"}),
        ("Extra wallet", {"typeId": "1", "walletAddress": "0x" + "b" * 40}),
    ]:
        local.append(request_item(name, body, 400, "VALIDATION_FAILED"))
    for type_id in ("1", MAX_TYPE_ID):
        local.append(
            request_item(
                "Valid request awaits adapters " + type_id,
                {"typeId": type_id},
                503,
                "REDEEM_UNAVAILABLE",
            )
        )
    contract = [
        request_item("Admission acknowledgement", {"typeId": "1"}, 202),
        request_item("Maximum uint256 acknowledgement", {"typeId": MAX_TYPE_ID}, 202),
        request_item(
            "Expired token fixture", {"typeId": "1"}, 401, "UNAUTHENTICATED", token="expired-token"
        ),
        request_item(
            "Wallet forbidden fixture", {"typeId": "1"}, 403, "WALLET_FORBIDDEN", token="no-wallet"
        ),
    ]
    for type_id, (code, status) in FAILURES.items():
        contract.append(
            request_item(f"Fixture {type_id}: {code}", {"typeId": str(type_id)}, status, code)
        )
    return {
        "info": {
            "name": "BE-0608 redeem admission",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            "description": (
                "Local mock runs against the mock-token API without admission. Adapter contract "
                "requires scripts/verify_redeem_api.py --newman PATH; "
                "its IDs are fixture scenarios, "
                "not deployed coupons. No real auth, DB commit, idempotency, or chain is verified."
            ),
        },
        "item": [
            {"name": "Local mock", "item": local},
            {"name": "Adapter contract", "item": contract},
        ],
    }


class FixtureDeployment:
    def resolve(self, type_id: int) -> CouponDeployment:
        return CouponDeployment(chain_id=1337, contract_address="0x" + "b" * 40)


class FixtureAdmission:
    def admit(
        self, *, user_id: str, idempotency_key: str, payload: RedeemCouponPayload
    ) -> RedeemCouponResult:
        if payload.type_id == 13:
            raise OperationalError(
                "private-fixture SQL", {}, RuntimeError("private-fixture database")
            )
        if payload.type_id == 14:
            raise RuntimeError("private-fixture RPC revert 0xdeadbeef")
        if payload.type_id == 15:
            raise HTTPException(403, detail="private-fixture unexpected HTTP adapter failure")
        if payload.type_id == 16:
            return RedeemCouponResult(operation_id="", type_id=payload.type_id)
        if payload.type_id in FAILURES:
            code = RedeemErrorCode(FAILURES[payload.type_id][0])
            if payload.type_id in (2, 3, 4, 5, 6):
                names = {
                    2: "CouponTypeNotFound",
                    3: "InsufficientBalance",
                    4: "CouponNotStarted",
                    5: "CouponExpired",
                    6: "NoRedeemCodeAvailable",
                }
                code = classify_redeem_revert(names[payload.type_id], origin="coupon")
            elif payload.type_id == 9:
                code = classify_redeem_revert(
                    "Error", origin="coupon", reason="Not authorized to redeem"
                )
            elif payload.type_id == 12:
                code = classify_redeem_revert("InsufficientBalance", origin="unknown")
            raise RedeemFailure(code, retry_after=7 if payload.type_id == 10 else None)
        return RedeemCouponResult(operation_id="op_fixture", type_id=payload.type_id)


def fixture_auth(token: str) -> AuthenticatedWallet:
    if token == "no-wallet":
        raise RedeemFailure(RedeemErrorCode.WALLET_FORBIDDEN)
    if token != "contract-token":
        raise RedeemFailure(RedeemErrorCode.UNAUTHENTICATED)
    return AuthenticatedWallet(user_id="fixture-user", wallet_address="0x" + "a" * 40)


@contextmanager
def serve(app: FastAPI) -> Iterator[str]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        try:
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("Local fixture server failed to start")
                time.sleep(0.01)
            yield f"http://127.0.0.1:{sock.getsockname()[1]}"
        finally:
            server.should_exit = True
            thread.join(timeout=10)


def newman_check(executable: str) -> None:
    settings = ApiSettings(
        database_url="sqlite:///:memory:", APP_ENV="test", mock_auth_enabled=True
    )
    fixtures = [
        ("Local mock", create_app(settings), "mock-redeem-token"),
        (
            "Adapter contract",
            create_app(
                settings,
                redeem=RedeemDependencies(
                    authenticate=fixture_auth,
                    redeemer=RedeemCoupons(FixtureDeployment(), FixtureAdmission()),
                ),
            ),
            "contract-token",
        ),
    ]
    with tempfile.TemporaryDirectory(prefix="redeem-newman-") as directory:
        for folder, app, token in fixtures:
            report = Path(directory) / (folder + ".json")
            with serve(app) as base_url:
                result = subprocess.run(
                    [
                        executable,
                        "run",
                        str(COLLECTION),
                        "--environment",
                        str(ENVIRONMENT),
                        "--folder",
                        folder,
                        "--env-var",
                        f"baseUrl={base_url}",
                        "--env-var",
                        f"accessToken={token}",
                        "--timeout-request",
                        "5000",
                        "--reporters",
                        "json",
                        "--reporter-json-export",
                        str(report),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            if not report.exists():
                raise RuntimeError(result.stderr or "Newman produced no report")
            run = json.loads(report.read_text())["run"]
            stats = run["stats"]
            print(
                f"{folder}: {stats['requests']['total']} requests, "
                f"{stats['assertions']['total']} assertions, {len(run['failures'])} failures"
            )
            if result.returncode or run["failures"] or not stats["assertions"]["total"]:
                raise RuntimeError(json.dumps(run["failures"], ensure_ascii=False))


def encoded(value: dict[str, Any]) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--export", action="store_true", help="Regenerate OpenAPI and Postman artifacts"
    )
    parser.add_argument(
        "--newman", help="Run both folders against isolated loopback fixture servers"
    )
    args = parser.parse_args()
    document = openapi_document()
    artifacts = {
        SPEC: document,
        COLLECTION: postman_collection(),
        ENVIRONMENT: {
            "name": "Local redeem mock",
            "_postman_variable_scope": "environment",
            "values": [
                {"key": "baseUrl", "value": "http://127.0.0.1:3000", "enabled": True},
                {
                    "key": "accessToken",
                    "value": "mock-redeem-token",
                    "enabled": True,
                    "type": "secret",
                },
            ],
        },
    }
    validate(document)
    for path, artifact in artifacts.items():
        content = encoded(artifact)
        if args.export:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        elif not path.exists() or path.read_text() != content:
            raise SystemExit(f"Stale artifact: {path.relative_to(ROOT)}; run with --export")
    for folder in artifacts[COLLECTION]["item"]:
        for item in folder["item"]:
            for example in item["response"]:
                schema = document["paths"][URL]["post"]["responses"][str(example["code"])][
                    "content"
                ]["application/json"]["schema"]
                Draft202012Validator({**schema, "components": document["components"]}).validate(
                    json.loads(example["body"])
                )
    print(
        "OpenAPI validated; exported artifacts match runtime; "
        "Postman examples match response schemas."
    )
    if args.newman:
        newman_check(args.newman)


if __name__ == "__main__":
    main()
