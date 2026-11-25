from datetime import datetime
from typing import Literal, cast

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from marketplace.infrastructure.operation_models import (
    Operation,
    OperationEvent,
    OperationPlanStep,
    OperationStep,
)
from marketplace.operation_status import OperationStatus
from marketplace.operations.application.exceptions import OperationsUnavailable
from marketplace.operations.application.interfaces.reader import OperationReader
from marketplace.operations.domain.entities.kinds import OperationKind
from marketplace.operations.domain.entities.operation import (
    OperationActor,
    OperationEventRecord,
    OperationSnapshot,
    OperationSummaryRecord,
    StepStatus,
    utc,
)


class SqlOperationReader(OperationReader):
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def page(
        self,
        user_id: str,
        *,
        status: OperationStatus | None,
        kind: OperationKind | None,
        before: tuple[datetime, str] | None,
        limit: int,
    ) -> tuple[OperationSummaryRecord, ...]:
        query = (
            select(Operation.id, Operation.kind, Operation.created_at, OperationEvent)
            .join(
                OperationEvent,
                and_(
                    OperationEvent.operation_id == Operation.id,
                    OperationEvent.seq == Operation.event_seq,
                ),
            )
            .where(Operation.actor_user_id == user_id)
        )
        if status is not None:
            query = query.where(OperationEvent.to_status == status)
        if kind is not None:
            query = query.where(Operation.kind == kind)
        if before is not None:
            created_at, operation_id = before
            query = query.where(
                or_(
                    Operation.created_at < created_at,
                    and_(Operation.created_at == created_at, Operation.id < operation_id),
                )
            )
        try:
            with self.sessions() as session:
                rows = (
                    session.execute(
                        query.order_by(Operation.created_at.desc(), Operation.id.desc()).limit(
                            limit
                        )
                    )
                    .tuples()
                    .all()
                )
            return tuple(
                OperationSummaryRecord(
                    op_id, op_kind, cast(OperationStatus, event.to_status), event.seq, utc(created)
                )
                for op_id, op_kind, created, event in rows
            )
        except SQLAlchemyError:
            raise OperationsUnavailable from None

    def find(self, user_id: str, operation_id: str) -> OperationSnapshot | None:
        try:
            with self.sessions() as session:
                rows = (
                    session.execute(
                        select(
                            Operation.kind,
                            Operation.created_at,
                            Operation.event_seq,
                            OperationEvent,
                            OperationStep.status,
                            OperationStep.tx_hash,
                        )
                        .join(OperationEvent, OperationEvent.operation_id == Operation.id)
                        .outerjoin(OperationStep, OperationStep.operation_id == Operation.id)
                        .where(Operation.id == operation_id, Operation.actor_user_id == user_id)
                        .order_by(OperationEvent.seq)
                    )
                    .tuples()
                    .all()
                )
                planned = (
                    tuple(
                        session.execute(
                            select(OperationPlanStep.step_index, OperationPlanStep.name)
                            .where(OperationPlanStep.operation_id == operation_id)
                            .order_by(OperationPlanStep.step_index)
                        )
                        .tuples()
                        .all()
                    )
                    if rows and rows[-1][0] == OperationKind.MERCHANT_PROVISION
                    else ()
                )
        except SQLAlchemyError:
            raise OperationsUnavailable from None
        if not rows:
            return None

        kind, created_at, seq, latest, step_status, step_hash = rows[-1]
        return OperationSnapshot(
            OperationSummaryRecord(
                operation_id, kind, cast(OperationStatus, latest.to_status), seq, utc(created_at)
            ),
            tuple(
                OperationEventRecord(
                    event.seq,
                    cast(OperationStatus | None, event.from_status),
                    cast(OperationStatus, event.to_status),
                    cast(OperationActor, event.actor),
                    event.tx_hash,
                    utc(event.created_at),
                    event.error_code,
                    event.is_snapshot,
                )
                for _, _, _, event, _, _ in rows
            ),
            planned,
            cast(StepStatus | Literal["PREPARED"] | None, step_status),
            step_hash,
        )
