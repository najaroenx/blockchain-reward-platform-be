from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import (
    ColumnElement,
    Connection,
    DateTime,
    Engine,
    and_,
    func,
    insert,
    or_,
    select,
    true,
    update,
)
from sqlalchemy.exc import SQLAlchemyError

from marketplace.infrastructure.operation_models import (
    Operation,
    OperationStep,
    Outbox,
    WorkerAttempt,
)
from marketplace.operations.domain.entities.kinds import OperationKind
from marketplace.worker.application.exceptions import OutboxUnavailable
from marketplace.worker.application.interfaces.outbox import OutboxRepository
from marketplace.worker.domain.entities.job import ClaimedJob


def database_time(connection: Connection) -> datetime:
    # Use the database clock so workers on different hosts agree on lease deadlines.
    statement = select(func.clock_timestamp(type_=DateTime(timezone=True)))
    return connection.scalars(statement).one()


def active_lease(job: ClaimedJob) -> ColumnElement[bool]:
    # The attempt ID is the lease token, so an old claim cannot finish a later attempt.
    return and_(
        WorkerAttempt.id == job.lease_id,
        WorkerAttempt.outbox_id == job.outbox_id,
        WorkerAttempt.finished_at.is_(None),
        WorkerAttempt.lease_until > func.clock_timestamp(),
    )


def recoverable_operation(*, lock_step: bool = False) -> ColumnElement[bool]:
    # PREPARED already has a hash. SUBMITTED records intent before the RPC call.
    step = (
        select(OperationStep.operation_id)
        .where(OperationStep.operation_id == Operation.id)
        .correlate(Operation)
    )
    prepared = step.where(OperationStep.status == "PREPARED", OperationStep.submitted_at.is_(None))
    if lock_step:
        # A submission can lock the step before it updates the operation.
        prepared = prepared.with_for_update(of=OperationStep, skip_locked=True)
    return and_(Operation.status == "PENDING", or_(~step.exists(), prepared.exists()))


class WorkerOutbox(OutboxRepository[Connection]):
    def __init__(self, engine: Engine) -> None:
        if engine.dialect.name != "postgresql":
            raise ValueError("Worker outbox polling requires PostgreSQL")
        # Each statement can see lease renewals committed by the heartbeat transaction.
        self.engine = engine.execution_options(isolation_level="READ COMMITTED")

    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        try:
            with self.engine.begin() as connection:
                yield connection
        except SQLAlchemyError as error:
            raise OutboxUnavailable(type(error).__name__) from None

    def claim(
        self, kinds: Sequence[OperationKind], worker_id: str, lease: timedelta
    ) -> ClaimedJob | None:
        latest = (
            select(
                WorkerAttempt.attempt_no,
                WorkerAttempt.outcome,
                WorkerAttempt.retry_at,
                WorkerAttempt.lease_until,
            )
            .where(WorkerAttempt.outbox_id == Outbox.id)
            .order_by(WorkerAttempt.attempt_no.desc())
            .limit(1)
            .correlate(Outbox)
            .lateral()
        )
        with self.transaction() as connection:
            row = (
                connection.execute(
                    select(Outbox.id, Operation.id, Operation.kind, Operation.payload)
                    .select_from(Outbox)
                    .join(Operation, Operation.id == Outbox.operation_id)
                    .outerjoin(latest, true())
                    .where(
                        Outbox.published_at.is_(None),
                        or_(
                            latest.c.attempt_no.is_(None),
                            and_(
                                latest.c.outcome == "RETRY",
                                latest.c.retry_at <= func.clock_timestamp(),
                            ),
                            and_(
                                latest.c.outcome.is_(None),
                                latest.c.lease_until <= func.clock_timestamp(),
                                recoverable_operation(),
                            ),
                        ),
                        Operation.kind.in_(kinds),
                    )
                    .order_by(Outbox.created_at, Outbox.id)
                    .limit(1)
                    # Hold the outbox row through the attempt insert and let other workers skip it.
                    .with_for_update(of=Outbox, skip_locked=True)
                )
                .tuples()
                .first()
            )
            if row is None:
                return None

            outbox_id, operation_id, kind, payload = row
            # A claim could have committed after the selection snapshot but before the row lock.
            previous = (
                connection.execute(
                    select(
                        WorkerAttempt.id,
                        WorkerAttempt.attempt_no,
                        WorkerAttempt.outcome,
                        WorkerAttempt.retry_at,
                    )
                    .where(WorkerAttempt.outbox_id == outbox_id)
                    .order_by(WorkerAttempt.attempt_no.desc())
                    .limit(1)
                )
                .tuples()
                .first()
            )
            now = database_time(connection)
            attempt_no = 1
            if previous is not None:
                previous_id, number, outcome, retry_at = previous
                if outcome is None:
                    if not self._expire_attempt(connection, previous_id, operation_id, now):
                        return None
                elif outcome != "RETRY" or retry_at is None or retry_at > now:
                    return None
                attempt_no = number + 1
            job = ClaimedJob(
                outbox_id, operation_id, OperationKind(kind), payload, uuid4().hex, attempt_no
            )
            connection.execute(
                insert(WorkerAttempt).values(
                    id=job.lease_id,
                    outbox_id=job.outbox_id,
                    attempt_no=job.attempt_no,
                    worker_id=worker_id,
                    started_at=now,
                    lease_until=now + lease,
                )
            )
            return job

    def _expire_attempt(
        self, connection: Connection, attempt_id: str, operation_id: str, now: datetime
    ) -> bool:
        operation_is_recoverable = (
            select(Operation.id)
            .where(Operation.id == operation_id, recoverable_operation(lock_step=True))
            .with_for_update(of=Operation, skip_locked=True)
            .exists()
        )
        # Finish may hold the attempt lock while waiting for the outbox row. Skip it.
        expired = (
            select(WorkerAttempt.id)
            .where(
                WorkerAttempt.id == attempt_id,
                WorkerAttempt.finished_at.is_(None),
                WorkerAttempt.lease_until <= now,
                operation_is_recoverable,
            )
            .with_for_update(of=WorkerAttempt, skip_locked=True)
            .scalar_subquery()
        )
        return (
            connection.scalar(
                update(WorkerAttempt)
                .where(WorkerAttempt.id == expired)
                .values(finished_at=now, outcome="RETRY", retry_at=now, error_type="LeaseExpired")
                .returning(WorkerAttempt.id)
            )
            is not None
        )

    def owns(self, connection: Connection, job: ClaimedJob) -> bool:
        return connection.scalar(select(WorkerAttempt.id).where(active_lease(job))) is not None

    def renew(self, job: ClaimedJob, lease: timedelta) -> bool:
        with self.transaction() as connection:
            return (
                connection.scalar(
                    update(WorkerAttempt)
                    .where(active_lease(job))
                    .values(lease_until=func.clock_timestamp() + lease)
                    .returning(WorkerAttempt.id)
                )
                is not None
            )

    def finish(
        self,
        connection: Connection,
        job: ClaimedJob,
        *,
        error_type: str | None = None,
        retry_delay: timedelta = timedelta(0),
    ) -> bool:
        now = database_time(connection)
        succeeded = error_type is None
        updated = connection.scalar(
            update(WorkerAttempt)
            .where(active_lease(job))
            .values(
                finished_at=now,
                outcome="SUCCEEDED" if succeeded else "RETRY",
                retry_at=None if succeeded else now + retry_delay,
                error_type=error_type,
            )
            .returning(WorkerAttempt.id)
        )
        if updated is None:
            return False
        if not succeeded:
            return True
        # Acknowledge successful delivery in the same transaction as the handler's writes.
        return (
            connection.scalar(
                update(Outbox)
                .where(Outbox.id == job.outbox_id, Outbox.published_at.is_(None))
                .values(published_at=now)
                .returning(Outbox.id)
            )
            is not None
        )
