import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from time import monotonic, sleep

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, func, insert, select, text, update

from marketplace.infrastructure.database import Database
from marketplace.infrastructure.operation_history import record_operation_created
from marketplace.infrastructure.operation_models import Operation, Outbox, WorkerAttempt
from marketplace.operations.domain.entities.kinds import OperationKind
from marketplace.worker.application.use_cases.process_outbox import OutboxWorker
from marketplace.worker.domain.entities.job import ClaimedJob
from marketplace.worker.infrastructure.repositories.outbox import WorkerOutbox

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_POSTGRES_URL"), reason="Worker lease tests require PostgreSQL"
)

KIND = OperationKind.COUPON_REDEEM
WORKER_PROGRAM = """
from sqlalchemy import text
from marketplace.operations.domain.entities.kinds import OperationKind
from marketplace.worker.main import main

def handle(connection, job):
    connection.execute(text('INSERT INTO worker_probe (operation_id) VALUES (:id)'),
                       {'id': job.operation_id})

main({OperationKind.COUPON_REDEEM: handle})
"""


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    database = Database(database_url)
    try:
        with database.engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE worker_probe (operation_id text NOT NULL)")
        yield database.engine
    finally:
        database.close()


def seed_job(engine: Engine, operation_id: str, *, kind: str = KIND) -> None:
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(
            insert(Operation).values(
                id=operation_id,
                kind=str(kind),
                actor_user_id="test-actor",
                status="PENDING",
                payload={"probe": operation_id},
                created_at=now,
            )
        )
        record_operation_created(connection, operation_id, now)
        connection.execute(
            insert(Outbox).values(
                id=f"outbox-{operation_id}",
                operation_id=operation_id,
                event_type="worker.probe.v1",
                created_at=now,
            )
        )


def add_effect(connection: Connection, job: ClaimedJob) -> None:
    assert job.kind is KIND
    assert job.payload == {"probe": job.operation_id}
    connection.execute(
        text("INSERT INTO worker_probe (operation_id) VALUES (:id)"), {"id": job.operation_id}
    )


def wait_for(predicate: Callable[[], bool], seconds: float = 5) -> None:
    deadline = monotonic() + seconds
    while not predicate():
        assert monotonic() < deadline, "worker did not reach the expected state before the deadline"
        sleep(0.02)


def test_worker_process_polls_existing_outbox_after_upgrade(engine: Engine) -> None:
    command.downgrade(Config("alembic.ini"), "0011_point_type_admission")
    seed_job(engine, "legacy-job")
    seed_job(engine, "unregistered", kind="UNREGISTERED")
    command.upgrade(Config("alembic.ini"), "head")

    env = {
        **os.environ,
        "RPC_URL": "http://localhost:8545",
        "CHAIN_ID": "1337",
        "KMS_KEY_ARN": "worker-test-reference",
        "KMS_REGION": "ap-southeast-1",
    }
    workers = [
        subprocess.Popen([sys.executable, "-c", WORKER_PROGRAM], env=env, stderr=subprocess.PIPE)
        for _ in range(2)
    ]

    def published() -> bool:
        with engine.connect() as connection:
            return (
                connection.scalar(
                    select(Outbox.published_at).where(Outbox.operation_id == "legacy-job")
                )
                is not None
            )

    try:
        wait_for(published)
    finally:
        for process in workers:
            if process.poll() is None:
                process.terminate()
        for process in workers:
            try:
                _, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                raise
            assert process.returncode == 0, stderr.decode()

    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT operation_id FROM worker_probe")
        ).scalars().all() == ["legacy-job"]
        assert connection.execute(select(WorkerAttempt.outcome)).scalars().all() == ["SUCCEEDED"]
        assert (
            connection.scalar(
                select(Outbox.published_at).where(Outbox.operation_id == "unregistered")
            )
            is None
        )


def test_two_workers_claim_once_and_skip_a_locked_row(engine: Engine) -> None:
    seed_job(engine, "locked")
    seed_job(engine, "available")
    barrier = Barrier(2)

    def claim(identity: str) -> ClaimedJob | None:
        barrier.wait(timeout=3)
        return WorkerOutbox(engine).claim((KIND,), identity, timedelta(seconds=30))

    with ThreadPoolExecutor(max_workers=2) as pool, engine.begin() as connection:
        connection.execute(
            select(Outbox.id).where(Outbox.operation_id == "locked").with_for_update()
        )
        claims = [pool.submit(claim, identity) for identity in ("first", "second")]
        jobs = [result.result(timeout=5) for result in claims]
    assert [job.operation_id for job in jobs if job is not None] == ["available"]
    with engine.connect() as connection:
        assert connection.execute(select(WorkerAttempt.attempt_no)).scalars().all() == [1]


def test_retry_history_and_atomic_effects(engine: Engine) -> None:
    seed_job(engine, "retried")

    def fail_three_times(connection: Connection, job: ClaimedJob) -> None:
        add_effect(connection, job)
        if job.attempt_no <= 3:
            raise RuntimeError("fixture failure")

    worker = OutboxWorker(WorkerOutbox(engine), {KIND: fail_three_times}, "worker")
    for attempt_number in (1, 2, 3):
        assert worker.run_once()
        assert not worker.run_once()
        with engine.begin() as connection:
            attempt = connection.execute(
                select(
                    WorkerAttempt.outcome, WorkerAttempt.finished_at, WorkerAttempt.retry_at
                ).where(WorkerAttempt.attempt_no == attempt_number)
            ).one()
            assert attempt.outcome == "RETRY"
            assert attempt.retry_at - attempt.finished_at == timedelta(
                seconds=2 ** (attempt_number - 1)
            )
            assert connection.scalar(text("SELECT count(*) FROM worker_probe")) == 0
            connection.execute(
                update(WorkerAttempt)
                .where(WorkerAttempt.attempt_no == attempt_number)
                .values(retry_at=func.clock_timestamp())
            )

    assert worker.run_once()
    assert not worker.run_once()
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM worker_probe")) == 1
        assert connection.execute(
            select(WorkerAttempt.outcome).order_by(WorkerAttempt.attempt_no)
        ).scalars().all() == ["RETRY", "RETRY", "RETRY", "SUCCEEDED"]
        assert connection.scalar(select(Operation.status)) == "PENDING"


def test_worker_renews_lease_while_handler_is_active(engine: Engine) -> None:
    seed_job(engine, "slow")

    def slow_handler(connection: Connection, job: ClaimedJob) -> None:
        initial = connection.scalar(select(WorkerAttempt.lease_until))

        def renewed() -> bool:
            with engine.connect() as observer:
                return observer.scalar(select(WorkerAttempt.lease_until)) > initial

        wait_for(renewed)
        add_effect(connection, job)

    assert OutboxWorker(
        WorkerOutbox(engine), {KIND: slow_handler}, "worker", lease_seconds=1.5
    ).run_once()
    with engine.connect() as connection:
        assert connection.scalar(select(WorkerAttempt.outcome)) == "SUCCEEDED"
        assert connection.scalar(select(Outbox.published_at)) is not None


def test_expired_lease_cannot_commit_handler_effects(engine: Engine) -> None:
    seed_job(engine, "expired")

    def expire_during_processing(connection: Connection, job: ClaimedJob) -> None:
        add_effect(connection, job)
        with engine.begin() as independent:
            independent.execute(
                update(WorkerAttempt)
                .where(WorkerAttempt.id == job.lease_id)
                .values(lease_until=func.clock_timestamp())
            )

    worker = OutboxWorker(WorkerOutbox(engine), {KIND: expire_during_processing}, "worker")
    assert worker.run_once()
    assert not worker.run_once()
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM worker_probe")) == 0
        assert connection.scalar(select(Outbox.published_at)) is None
        assert connection.scalar(select(WorkerAttempt.outcome)) is None
