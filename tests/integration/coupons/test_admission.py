from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event, Lock

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import event, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from marketplace.coupons.application.dto import AuthenticatedWallet, CouponDeployment
from marketplace.coupons.application.use_cases import RedeemCoupons
from marketplace.coupons.infrastructure.admission import REDEEM_ENDPOINT, SqlRedeemAdmissionStore
from marketplace.coupons.presentation.router import RedeemDependencies
from marketplace.infrastructure.database import Database
from marketplace.infrastructure.operation_models import IdempotencyKey, Operation, Outbox
from marketplace.main import create_app
from marketplace.settings import ApiSettings

URL = "/v1/coupons/redeem"
HEADERS = {"Authorization": "Bearer admission-fixture-token", "Idempotency-Key": "admission-key-1"}


class TrustedContext:
    """Fixtures only for wallet/deployment; admission uses the migrated database."""

    def __init__(self) -> None:
        self.user_id = "auth-user-1"
        self.wallet = "0x" + "A" * 40
        self.chain_id = 1337
        self.contract = "0x" + "B" * 40

    def authenticate(self, token: str) -> AuthenticatedWallet:
        assert token == "admission-fixture-token"
        return AuthenticatedWallet(user_id=self.user_id, wallet_address=self.wallet)

    def resolve(self, type_id: int) -> CouponDeployment:
        return CouponDeployment(chain_id=self.chain_id, contract_address=self.contract)


@dataclass
class AdmissionHarness:
    http: TestClient
    database: Database
    context: TrustedContext
    url: str

    def counts(self) -> list[int]:
        with self.database.engine.connect() as connection:
            return [
                connection.execute(select(func.count()).select_from(model)).scalar_one()
                for model in (IdempotencyKey, Operation, Outbox)
            ]

    def post(self, *, key: str = "admission-key-1", type_id: str = "1"):
        return self.http.post(
            URL, json={"typeId": type_id}, headers={**HEADERS, "Idempotency-Key": key}
        )

    def orphan_count(self) -> int:
        with self.database.engine.connect() as connection:
            return connection.execute(
                select(func.count())
                .select_from(Operation)
                .outerjoin(Outbox, Outbox.operation_id == Operation.id)
                .where(Outbox.id.is_(None))
            ).scalar_one()


@pytest.fixture
def admitted(admission_database_url: str) -> Iterator[AdmissionHarness]:
    database = Database(admission_database_url)
    trusted = TrustedContext()
    app = create_app(
        ApiSettings(database_url=admission_database_url, APP_ENV="test"),
        redeem=RedeemDependencies(
            authenticate=trusted.authenticate,
            redeemer=RedeemCoupons(trusted, SqlRedeemAdmissionStore(database.engine)),
        ),
    )
    try:
        with TestClient(app) as http:
            yield AdmissionHarness(http, database, trusted, admission_database_url)
    finally:
        database.close()


@pytest.mark.parametrize("type_id", ["1", str(2**256 - 1)])
def test_committed_admission_contains_only_resolved_payload_and_acknowledgement(
    admitted: AdmissionHarness, type_id: str
) -> None:
    response = admitted.post(type_id=type_id)
    assert response.status_code == 202
    result = response.json()
    assert result == {
        "operationId": result["operationId"],
        "status": "PENDING",
        "typeId": type_id,
        "amount": "1",
    }
    assert admitted.counts() == [1, 1, 1]
    with admitted.database.engine.connect() as connection:
        operation = connection.execute(select(Operation.__table__)).mappings().one()
        idem = connection.execute(select(IdempotencyKey.__table__)).mappings().one()
        outbox = connection.execute(select(Outbox.__table__)).mappings().one()
    assert (
        operation["id"] == idem["operation_id"] == outbox["operation_id"] == result["operationId"]
    )
    assert operation["kind"] == "COUPON_REDEEM"
    assert operation["actor_user_id"] == admitted.context.user_id
    assert operation["status"] == "PENDING"
    assert operation["payload"] == {
        "chainId": "1337",
        "couponContractAddress": "0x" + "b" * 40,
        "walletAddress": "0x" + "a" * 40,
        "typeId": type_id,
        "amount": "1",
    }
    assert "admission-fixture-token" not in repr((operation, idem, outbox))
    assert idem["scope"] == f"user:{admitted.context.user_id}:{REDEEM_ENDPOINT}"
    assert len(idem["fingerprint"]) == 32
    assert idem["response_status"] == 202
    assert idem["response_body"].encode() == response.content
    assert outbox["event_type"] == "COUPON_REDEEM_REQUESTED"
    assert outbox["published_at"] is None


def test_idempotency_claim_is_the_first_statement_in_admission(admitted: AdmissionHarness) -> None:
    statements = []

    def record(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(admitted.database.engine, "before_cursor_execute", record)
    response = admitted.post()
    event.remove(admitted.database.engine, "before_cursor_execute", record)
    assert response.status_code == 202
    assert statements[0].startswith("INSERT INTO idempotency_key")
    assert "ON CONFLICT (scope, key) DO NOTHING" in statements[0].replace('"', "")


def test_admission_migration_matches_models_and_can_downgrade_populated_test_data(
    admitted: AdmissionHarness,
) -> None:
    assert admitted.post().status_code == 202
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.check(config)
    command.downgrade(config, "0001_marketplace_projection")
    command.upgrade(config, "head")
    assert admitted.counts() == [0, 0, 0]


def test_outbox_cannot_reference_a_nonexistent_operation(admitted: AdmissionHarness) -> None:
    with pytest.raises(IntegrityError), admitted.database.engine.begin() as connection:
        connection.execute(
            insert(Outbox).values(
                id="ob_orphan",
                operation_id="op_missing",
                event_type="COUPON_REDEEM_REQUESTED",
                created_at=datetime.now(UTC),
            )
        )
    assert admitted.counts() == [0, 0, 0]


def test_replay_uses_exact_saved_acknowledgement_after_restart(admitted: AdmissionHarness) -> None:
    original = admitted.post()
    # A new engine/store has no process-local cache and sees only committed SQL state.
    restarted = Database(admitted.url)
    app = create_app(
        ApiSettings(database_url=admitted.url, APP_ENV="test"),
        redeem=RedeemDependencies(
            authenticate=admitted.context.authenticate,
            redeemer=RedeemCoupons(admitted.context, SqlRedeemAdmissionStore(restarted.engine)),
        ),
    )
    try:
        with TestClient(app) as http:
            replay = http.post(URL, json={"typeId": "1"}, headers=HEADERS)
        assert replay.status_code == original.status_code == 202
        assert replay.content == original.content
        assert admitted.counts() == [1, 1, 1]
    finally:
        restarted.close()


@pytest.mark.parametrize("changed", ["type", "wallet", "chain", "contract"])
def test_same_key_with_changed_resolved_context_is_conflict(
    admitted: AdmissionHarness, changed: str
) -> None:
    original = admitted.post()
    type_id = "1"
    if changed == "type":
        type_id = "2"
    if changed == "wallet":
        admitted.context.wallet = "0x" + "c" * 40
    if changed == "chain":
        admitted.context.chain_id = 1338
    if changed == "contract":
        admitted.context.contract = "0x" + "d" * 40
    response = admitted.post(type_id=type_id)
    assert original.status_code == 202
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert admitted.counts() == [1, 1, 1]


def test_address_case_normalizes_to_the_same_request(admitted: AdmissionHarness) -> None:
    original = admitted.post()
    admitted.context.wallet = admitted.context.wallet.lower()
    admitted.context.contract = admitted.context.contract.lower()
    replay = admitted.post()
    assert replay.status_code == 202
    assert replay.content == original.content
    assert admitted.counts() == [1, 1, 1]


def test_replay_preserves_saved_response_bytes_including_whitespace(
    admitted: AdmissionHarness,
) -> None:
    original = admitted.post()
    saved = "\n" + original.text + "\n"
    with admitted.database.engine.begin() as connection:
        connection.execute(update(IdempotencyKey).values(response_body=saved))
    response = admitted.post()
    assert response.status_code == 202
    assert response.content == saved.encode()
    assert admitted.counts() == [1, 1, 1]


def test_corrupted_saved_response_is_sanitized_instead_of_leaking_fields(
    admitted: AdmissionHarness,
) -> None:
    assert admitted.post().status_code == 202
    with admitted.database.engine.begin() as connection:
        connection.execute(
            update(IdempotencyKey).values(response_body='{"privateToken":"private-data"}')
        )
    response = admitted.post()
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "private" not in response.text
    assert admitted.counts() == [1, 1, 1]


@pytest.mark.parametrize("changed", ["key", "user"])
def test_different_keys_or_users_have_independent_requests(
    admitted: AdmissionHarness, changed: str
) -> None:
    first = admitted.post()
    if changed == "user":
        admitted.context.user_id = "auth-user-2"
    second = admitted.post(key="another-admission-key" if changed == "key" else "admission-key-1")
    assert first.status_code == second.status_code == 202
    assert first.json()["operationId"] != second.json()["operationId"]
    assert admitted.counts() == [2, 2, 2]


def test_committed_claim_without_acknowledgement_is_in_progress(admitted: AdmissionHarness) -> None:
    assert admitted.post().status_code == 202
    with admitted.database.engine.begin() as connection:
        digest = connection.execute(select(IdempotencyKey.fingerprint)).scalar_one()
        now = datetime.now(UTC)
        connection.execute(
            insert(IdempotencyKey).values(
                id="ik_in_progress",
                scope=f"user:{admitted.context.user_id}:{REDEEM_ENDPOINT}",
                key="in-progress-key",
                fingerprint=digest,
                created_at=now,
                expires_at=now + timedelta(hours=72),
            )
        )
    response = admitted.post(key="in-progress-key")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_IN_PROGRESS"
    assert admitted.counts() == [2, 1, 1]


def test_terminal_operation_replays_original_202_without_new_work(
    admitted: AdmissionHarness,
) -> None:
    original = admitted.post()
    with admitted.database.engine.begin() as connection:
        connection.execute(update(Operation).values(status="FINALIZED"))
    response = admitted.post()
    assert response.status_code == 202
    assert response.content == original.content
    assert admitted.counts() == [1, 1, 1]


def test_twenty_simultaneous_http_requests_create_one_operation_and_outbox(
    admitted: AdmissionHarness,
) -> None:
    start = Barrier(20)
    writer_inserted, competing_insert, release = Event(), Event(), Event()
    lock = Lock()
    claims = 0
    winner = None

    def before(connection, cursor, statement, parameters, context, executemany):
        nonlocal claims, winner
        if statement.startswith("INSERT INTO idempotency_key"):
            with lock:
                claims += 1
                if claims == 1:
                    winner = connection
                else:
                    competing_insert.set()

    def after(connection, cursor, statement, parameters, context, executemany):
        if connection is winner and statement.startswith("INSERT INTO idempotency_key"):
            writer_inserted.set()
            assert release.wait(15)

    def request():
        start.wait(timeout=15)
        return admitted.post()

    event.listen(admitted.database.engine, "before_cursor_execute", before)
    event.listen(admitted.database.engine, "after_cursor_execute", after)
    try:
        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = [pool.submit(request) for _ in range(20)]
            try:
                assert writer_inserted.wait(15)
                assert competing_insert.wait(15)
            finally:
                release.set()
            results = [future.result(timeout=30) for future in futures]
    finally:
        event.remove(admitted.database.engine, "before_cursor_execute", before)
        event.remove(admitted.database.engine, "after_cursor_execute", after)
    assert claims == 20
    assert [response.status_code for response in results] == [202] * 20
    assert len({response.content for response in results}) == 1
    assert admitted.counts() == [1, 1, 1]


def test_http_acknowledgement_waits_for_database_commit(admitted: AdmissionHarness) -> None:
    committing, release = Event(), Event()

    def hold_commit(connection):
        committing.set()
        assert release.wait(15)

    event.listen(admitted.database.engine, "commit", hold_commit)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(admitted.post)
            try:
                assert committing.wait(15)
                assert not future.done()
                assert admitted.counts() == [0, 0, 0]
            finally:
                release.set()
            response = future.result(timeout=30)
    finally:
        event.remove(admitted.database.engine, "commit", hold_commit)
    assert response.status_code == 202
    assert admitted.counts() == [1, 1, 1]


@pytest.mark.parametrize("stage", ["outbox", "acknowledgement", "commit"])
def test_sql_failure_rolls_back_claim_operation_and_outbox_then_allows_retry(
    admitted: AdmissionHarness, stage: str
) -> None:
    target = "INSERT INTO outbox" if stage == "outbox" else "UPDATE idempotency_key"

    def fail(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith(target):
            connection.exec_driver_sql("SELECT private_admission_failure_function()")

    def fail_commit(connection):
        connection.exec_driver_sql("SELECT private_admission_failure_function()")

    event_name, callback = (
        ("commit", fail_commit) if stage == "commit" else ("before_cursor_execute", fail)
    )
    event.listen(admitted.database.engine, event_name, callback)
    response = admitted.post()
    event.remove(admitted.database.engine, event_name, callback)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ADMISSION_UNAVAILABLE"
    assert "private_admission" not in response.text
    assert admitted.counts() == [0, 0, 0]
    assert admitted.orphan_count() == 0
    assert admitted.post().status_code == 202
    assert admitted.counts() == [1, 1, 1]
    assert admitted.orphan_count() == 0
