"""Actual pinned-contract EVM + backend handler/projector; run verify_redeem_chain.py."""

import json
import os
import select as io_select
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError

from marketplace.coupons.application.dto import (
    AuthenticatedWallet,
    CouponDeployment,
    RedeemCouponPayload,
)
from marketplace.coupons.application.exceptions import RedeemErrorCode, RedeemFailure
from marketplace.coupons.application.execution import (
    ChainLog,
    ReceiptCorrelationError,
    RedeemReceipt,
    SignedRedeem,
)
from marketplace.coupons.application.use_cases import RedeemCoupons
from marketplace.coupons.infrastructure.admission import SqlRedeemAdmissionStore, normalized_payload
from marketplace.coupons.infrastructure.execution_store import SqlRedeemExecutionStore
from marketplace.coupons.infrastructure.receipts import CODE_TOPIC, REDEEMED_TOPIC
from marketplace.coupons.presentation.router import RedeemDependencies
from marketplace.indexer.redeem import RedeemProjector
from marketplace.infrastructure.database import Database
from marketplace.infrastructure.operation_models import (
    ChainEvent,
    Operation,
    OperationStep,
    RedeemRefusal,
    RedeemResult,
)
from marketplace.main import create_app
from marketplace.settings import ApiSettings
from marketplace.worker.redeem import RedeemHandler

pytestmark = pytest.mark.skipif(
    not os.environ.get("REDEEM_CHAIN_WORKSPACE"),
    reason="Run scripts/verify_redeem_chain.py for pinned local-chain integration",
)
URL = "/v1/coupons/redeem"
HEADERS = {"Authorization": "Bearer local-chain-fixture", "Idempotency-Key": "chain-request-key"}


class LocalChain:
    def __init__(self, workspace: Path) -> None:
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"DLP_RPC_URL", "DLP_PRIVATE_KEY"}
        }
        env["HARDHAT_NETWORK"] = "hardhat"
        self.process = subprocess.Popen(
            ["node", "test/redeem_bridge.cjs"],
            cwd=workspace,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self.buffer = b""
        self.sequence = 0
        self.fail_after_broadcast = False
        self.before_broadcast = None
        assert self.read()["ready"] is True

    def read(self):
        while True:
            while b"\n" not in self.buffer:
                assert io_select.select([self.process.stdout], [], [], 30)[0], (
                    "Local EVM bridge timed out"
                )
                chunk = os.read(self.process.stdout.fileno(), 65536)
                assert chunk, "Local EVM bridge exited"
                self.buffer += chunk
            line, self.buffer = self.buffer.split(b"\n", 1)
            try:
                value = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(value, dict) and value.get("bridge"):
                return value

    def rpc(self, method, **args):
        self.sequence += 1
        self.process.stdin.write(
            (json.dumps({"id": self.sequence, "method": method, "args": args}) + "\n").encode()
        )
        response = self.read()
        assert response["id"] == self.sequence
        assert "error" not in response, response.get("error")
        return response["result"]

    def setup(self, **args):
        self.info = self.rpc("setup", **args)
        self.fail_after_broadcast = False
        self.before_broadcast = None

    def verify(self, payload: RedeemCouponPayload) -> None:
        code = self.rpc("verify", **normalized_payload(payload))["code"]
        if code is not None:
            raise RedeemFailure(RedeemErrorCode(code))

    def prepare(self, payload: RedeemCouponPayload) -> SignedRedeem:
        value = self.rpc("prepare", **normalized_payload(payload))
        return SignedRedeem(
            raw_tx=bytes.fromhex(value["raw"][2:]),
            tx_hash=value["hash"],
            signer_address=value["signer"],
            nonce=int(value["nonce"]),
        )

    def broadcast(self, transaction: SignedRedeem) -> None:
        if self.before_broadcast is not None:
            self.before_broadcast(transaction)
        self.rpc("broadcast", raw="0x" + transaction.raw_tx.hex())
        if self.fail_after_broadcast:
            self.fail_after_broadcast = False
            raise TimeoutError("Simulated lost response after real local broadcast")

    def receipt(self, tx_hash: str) -> RedeemReceipt | None:
        value = self.rpc("receipt", hash=tx_hash)
        if value is None:
            return None
        return RedeemReceipt(
            chain_id=int(value["chainId"]),
            contract_address=value["to"],
            tx_hash=value["hash"],
            block_hash=value["blockHash"],
            block_number=int(value["blockNumber"]),
            mined_at=datetime.fromisoformat(value["minedAt"].replace("Z", "+00:00")),
            status=value["status"],
            logs=tuple(
                ChainLog(
                    address=log["address"],
                    topics=tuple(log["topics"]),
                    data=log["data"],
                    log_index=log["index"],
                    tx_hash=log["hash"],
                    block_hash=log["blockHash"],
                )
                for log in value["logs"]
            ),
            error_name=value["errorName"],
            error_origin=value["errorOrigin"],
            error_reason=value["errorReason"],
        )

    def close(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=10)


@pytest.fixture(scope="module")
def chain() -> Iterator[LocalChain]:
    bridge = LocalChain(Path(os.environ["REDEEM_CHAIN_WORKSPACE"]))
    try:
        yield bridge
    finally:
        bridge.close()


@dataclass
class Pipeline:
    database: Database
    http: TestClient
    chain: LocalChain
    store: SqlRedeemExecutionStore

    def admit(self, *, type_id="1", key="chain-request-key"):
        return self.http.post(
            URL, json={"typeId": type_id}, headers={**HEADERS, "Idempotency-Key": key}
        )

    def count(self, table):
        with self.database.engine.connect() as connection:
            return connection.execute(select(func.count()).select_from(table)).scalar_one()

    def status(self, operation_id):
        with self.database.engine.connect() as connection:
            return connection.execute(
                select(Operation.status).where(Operation.id == operation_id)
            ).scalar_one()

    def run(self, operation_id):
        return RedeemHandler(self.store, self.chain).execute(operation_id)


@pytest.fixture
def pipeline_factory(admission_database_url: str, chain: LocalChain):
    from contextlib import contextmanager

    @contextmanager
    def factory(**setup):
        chain.setup(**setup)
        database = Database(admission_database_url)

        class Deployment:
            def resolve(self, type_id):
                chain.verify(
                    RedeemCouponPayload(
                        chain_id=1337,
                        coupon_contract_address=chain.info["coupon"],
                        wallet_address=chain.info["holder"],
                        type_id=type_id,
                    )
                )
                return CouponDeployment(chain_id=1337, contract_address=chain.info["coupon"])

        def authenticate(token):
            assert token == "local-chain-fixture"
            return AuthenticatedWallet(user_id="local-user", wallet_address=chain.info["holder"])

        app = create_app(
            ApiSettings(database_url=admission_database_url, APP_ENV="test"),
            redeem=RedeemDependencies(
                authenticate=authenticate,
                redeemer=RedeemCoupons(Deployment(), SqlRedeemAdmissionStore(database.engine)),
            ),
        )
        store = SqlRedeemExecutionStore(database.engine)

        def check_persisted(signed):
            # Independent connection must observe the exact raw transaction before RPC submission.
            with database.engine.connect() as connection:
                row = (
                    connection.execute(
                        select(OperationStep.__table__).where(
                            OperationStep.tx_hash == signed.tx_hash.lower()
                        )
                    )
                    .mappings()
                    .one()
                )
            assert row["raw_tx"] == signed.raw_tx
            assert row["status"] == "SUBMITTED"

        chain.before_broadcast = check_persisted
        try:
            with TestClient(app) as http:
                yield Pipeline(database, http, chain, store)
        finally:
            database.close()

    return factory


@pytest.mark.parametrize("variant", ["Marketplace", "MarketplaceUpgradeable"])
@pytest.mark.parametrize("signer", ["holder", "operator", "owner"])
def test_real_handler_consumes_one_fifo_code_for_the_holder_and_settles_actual_escrow(
    pipeline_factory, variant, signer
):
    with pipeline_factory(variant=variant, signer=signer) as p:
        before = p.chain.rpc("inspect")
        assert [e["active"] for e in before["escrows"]] == [True, True]
        response = p.admit()
        assert response.status_code == 202
        operation_id = response.json()["operationId"]
        result = p.run(operation_id)
        assert result == {
            "walletAddress": p.chain.info["holder"].lower(),
            "typeId": "1",
            "amount": "1",
            "redeemCodes": ["FIRST"],
        }
        after = p.chain.rpc("inspect")
        assert int(after["balance"]) == int(before["balance"]) - 1
        assert int(after["available"]) == int(before["available"]) - 1
        assert after["firstUsed"] is True
        assert int(after["sellerBalance"]) - int(before["sellerBalance"]) == 100
        assert after["escrows"][0]["remaining"] == "100"
        assert after["escrows"][1] == before["escrows"][1]
        assert p.status(operation_id) == "CONFIRMED"
        assert p.count(ChainEvent) == 2
        assert p.run(operation_id) == result
        assert p.chain.rpc("inspect")["broadcasts"] == 1
        second = p.admit(key="second-chain-key").json()["operationId"]
        assert p.run(second)["redeemCodes"] == ["SECOND"]
        assert p.admit().content == response.content


@pytest.mark.parametrize("variant", ["Marketplace", "MarketplaceUpgradeable"])
def test_callback_revert_rolls_back_balance_codes_backing_and_settlement(pipeline_factory, variant):
    with pipeline_factory(variant=variant, scenario="callback-revert", signer="operator") as p:
        before = p.chain.rpc("inspect")
        operation_id = p.admit().json()["operationId"]
        assert p.run(operation_id) == {"code": "CONTRACT_CALL_FAILED"}
        after = p.chain.rpc("inspect")
        for field in ("balance", "available", "firstUsed", "sellerBalance", "escrows", "backing"):
            assert after[field] == before[field], field
        assert p.status(operation_id) == "CHAIN_REVERTED"
        assert p.count(ChainEvent) == 0


@pytest.mark.parametrize("variant", ["Marketplace", "MarketplaceUpgradeable"])
def test_redeem_without_purchase_escrow_does_not_invent_a_payout(pipeline_factory, variant):
    with pipeline_factory(variant=variant, scenario="direct") as p:
        operation_id = p.admit().json()["operationId"]
        assert p.run(operation_id)["redeemCodes"] == ["FIRST"]
        state = p.chain.rpc("inspect")
        assert state["balance"] == "2"
        assert state["sellerBalance"] == "0"
        assert [e["active"] for e in state["escrows"]] == [False, False]


@pytest.mark.parametrize(
    "scenario, type_id, code",
    [
        ("empty", "1", "INSUFFICIENT_BALANCE"),
        ("not-started", "1", "COUPON_NOT_STARTED"),
        ("expired", "1", "COUPON_EXPIRED"),
        ("normal", "99", "COUPON_TYPE_NOT_FOUND"),
    ],
)
def test_real_coupon_reverts_after_202_are_operation_failures(
    pipeline_factory, scenario, type_id, code
):
    with pipeline_factory(scenario=scenario, signer="owner") as p:
        response = p.admit(type_id=type_id)
        assert response.status_code == 202
        operation_id = response.json()["operationId"]
        assert p.run(operation_id) == {"code": code}
        assert p.status(operation_id) == "CHAIN_REVERTED"
        assert p.admit(type_id=type_id).content == response.content
        assert p.count(ChainEvent) == 0


def test_unauthorized_signer_is_configuration_failure_before_admission(pipeline_factory):
    with pipeline_factory(signer="outsider") as p:
        response = p.admit()
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "SIGNER_UNAUTHORIZED"
        assert p.count(Operation) == 0
        assert p.count(OperationStep) == 0


def test_permission_revoked_after_202_is_recorded_without_broadcast(pipeline_factory):
    with pipeline_factory() as p:
        response = p.admit()
        operation_id = response.json()["operationId"]
        p.chain.rpc("revoke")
        assert p.run(operation_id) == {"code": "SIGNER_UNAUTHORIZED"}
        assert p.status(operation_id) == "MANUAL_REVIEW"
        assert p.count(RedeemRefusal) == 1
        assert p.count(OperationStep) == 0
        assert p.chain.rpc("inspect")["broadcasts"] == 0


def test_lost_broadcast_response_recovers_original_transaction_after_worker_restart(
    pipeline_factory,
):
    with pipeline_factory() as p:
        operation_id = p.admit().json()["operationId"]
        p.chain.fail_after_broadcast = True
        with pytest.raises(TimeoutError):
            p.run(operation_id)
        original = p.store.transaction(operation_id)
        assert p.status(operation_id) == "SUBMITTED"
        restarted = SqlRedeemExecutionStore(p.database.engine)
        assert RedeemHandler(restarted, p.chain).execute(operation_id)["redeemCodes"] == ["FIRST"]
        assert restarted.transaction(operation_id) == original
        state = p.chain.rpc("inspect")
        assert state["prepares"] == state["broadcasts"] == 1
        assert state["balance"] == "1"


def test_pending_delivery_reuses_signed_bytes_and_mines_only_one_redemption(pipeline_factory):
    with pipeline_factory() as p:
        operation_id = p.admit().json()["operationId"]
        p.chain.rpc("automine", enabled=False)
        try:
            assert p.run(operation_id) is None
            signed = p.store.transaction(operation_id)
            assert p.run(operation_id) is None
            assert p.store.transaction(operation_id) == signed
            p.chain.rpc("mine")
            assert p.run(operation_id)["redeemCodes"] == ["FIRST"]
        finally:
            p.chain.rpc("automine", enabled=True)
        state = p.chain.rpc("inspect")
        assert state["prepares"] == 1
        assert state["balance"] == "1"
        assert p.count(OperationStep) == 1


def test_projector_replay_is_deduplicated_and_marketplace_event_cannot_replace_coupon_event(
    pipeline_factory,
):
    with pipeline_factory() as p:
        operation_id = p.admit().json()["operationId"]
        result = p.run(operation_id)
        signed = p.store.transaction(operation_id)
        receipt = p.chain.receipt(signed.tx_hash)
        projector = RedeemProjector(p.store)
        assert projector.project(operation_id, receipt) == result
        assert projector.project(operation_id, receipt) == result
        assert p.count(ChainEvent) == 2
        assert p.count(RedeemResult) == 1
        marketplace_logs = tuple(
            log
            for log in receipt.logs
            if log.address.lower() == p.chain.info["marketplace"].lower()
        )
        assert marketplace_logs
        with pytest.raises(ReceiptCorrelationError):
            projector.project(operation_id, replace(receipt, logs=marketplace_logs))
        with pytest.raises(ReceiptCorrelationError):
            projector.project(operation_id, replace(receipt, tx_hash="0x" + "a" * 64))
        coupon_logs = tuple(
            log
            for log in receipt.logs
            if log.address.lower() == p.chain.info["coupon"].lower()
            and log.topics[0].lower() in {CODE_TOPIC, REDEEMED_TOPIC}
        )
        wrong = replace(coupon_logs[0], tx_hash="0x" + "a" * 64)
        with pytest.raises(ReceiptCorrelationError):
            projector.project(operation_id, replace(receipt, logs=(wrong, *coupon_logs[1:])))
        moved = "0x" + "b" * 64
        with pytest.raises(ReceiptCorrelationError):
            projector.project(
                operation_id,
                replace(
                    receipt,
                    block_hash=moved,
                    logs=tuple(replace(log, block_hash=moved) for log in receipt.logs),
                ),
            )
        assert p.count(ChainEvent) == 2
        assert p.store.result(operation_id) == result


def test_execution_constraints_require_raw_tx_unique_hash_and_mined_timestamp(pipeline_factory):
    with pipeline_factory() as p:
        operation_id = p.admit().json()["operationId"]
        with pytest.raises(IntegrityError), p.database.engine.begin() as connection:
            connection.execute(
                insert(OperationStep).values(
                    operation_id=operation_id,
                    chain_id="1337",
                    signer_address=p.chain.info["signer"],
                    nonce="0",
                    raw_tx=b"",
                    tx_hash="0x" + "1" * 64,
                    status="SUBMITTED",
                    created_at=datetime.now().astimezone(),
                )
            )
        result = p.run(operation_id)
        with p.database.engine.connect() as connection:
            saved = dict(connection.execute(select(OperationStep.__table__)).mappings().one())
        another = p.admit(key="another-constraint-key").json()["operationId"]
        with pytest.raises(IntegrityError), p.database.engine.begin() as connection:
            connection.execute(
                insert(OperationStep).values(**{**saved, "operation_id": another, "nonce": "999"})
            )
        payload = p.store.payload(another)
        prepared = p.store.remember(another, payload, p.chain.prepare(payload))
        with pytest.raises(IntegrityError), p.database.engine.begin() as connection:
            connection.execute(
                insert(RedeemResult).values(
                    operation_id=another,
                    tx_hash=prepared.tx_hash,
                    block_hash="0x" + "b" * 64,
                    block_number="1",
                    mined_at=None,
                    status="CONFIRMED",
                    result=result,
                )
            )


def test_signer_revoked_during_broadcast_is_a_real_chain_failure_after_202(pipeline_factory):
    with pipeline_factory(scenario="signer-revoked", signer="operator") as p:
        operation_id = p.admit().json()["operationId"]
        assert p.run(operation_id) == {"code": "SIGNER_UNAUTHORIZED"}
        assert p.status(operation_id) == "CHAIN_REVERTED"
        assert p.count(ChainEvent) == 0
