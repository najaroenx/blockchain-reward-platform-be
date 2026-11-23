from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import Connection, insert, select, update

from marketplace.infrastructure.operation_models import Operation, OperationEvent

OperationStatus = Literal[
    "PENDING", "SUBMITTED", "CONFIRMED", "FINALIZED", "CHAIN_REVERTED", "ORPHANED", "MANUAL_REVIEW"
]


def record_operation_created(
    connection: Connection, operation_id: str, created_at: datetime
) -> None:
    connection.execute(
        insert(OperationEvent).values(
            operation_id=operation_id, seq=1, to_status="PENDING", created_at=created_at
        )
    )


def advance_operation(
    connection: Connection,
    operation_id: str,
    status: OperationStatus,
    *,
    tx_hash: str | None = None,
    error_code: str | None = None,
) -> None:
    current_status, current_seq = (
        connection.execute(
            select(Operation.status, Operation.event_seq)
            .where(Operation.id == operation_id)
            .with_for_update()
        )
        .tuples()
        .one()
    )
    if current_status == status:
        return
    seq = current_seq + 1
    connection.execute(
        update(Operation).where(Operation.id == operation_id).values(status=status, event_seq=seq)
    )
    connection.execute(
        insert(OperationEvent).values(
            operation_id=operation_id,
            seq=seq,
            from_status=current_status,
            to_status=status,
            tx_hash=tx_hash.lower() if tx_hash is not None else None,
            error_code=error_code,
            created_at=datetime.now(UTC),
        )
    )
