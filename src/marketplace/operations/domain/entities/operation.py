from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from marketplace.operation_status import OperationStatus

OperationActor = Literal["SYSTEM", "MERCHANT_ADMIN", "CUSTOMER", "PLATFORM_ADMIN"]
StepStatus = Literal["PENDING", "SUBMITTED", "CONFIRMED", "FINALIZED", "CHAIN_REVERTED", "ORPHANED"]


def utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


@dataclass(frozen=True)
class OperationSummaryRecord:
    id: str
    kind: str
    status: OperationStatus
    event_seq: int
    created_at: datetime


@dataclass(frozen=True)
class OperationEventRecord:
    seq: int
    from_status: OperationStatus | None
    to_status: OperationStatus
    actor: OperationActor
    tx_hash: str | None
    created_at: datetime
    error_code: str | None
    is_snapshot: bool


@dataclass(frozen=True)
class OperationSnapshot:
    summary: OperationSummaryRecord
    history: tuple[OperationEventRecord, ...]
    planned: tuple[tuple[int, str], ...]
    step_status: StepStatus | Literal["PREPARED"] | None
    step_hash: str | None

    def has_current_event(self) -> bool:
        return bool(self.history) and self.history[-1].seq == self.summary.event_seq

    @property
    def terminal(self) -> bool:
        return self.summary.status in {"FINALIZED", "CHAIN_REVERTED"}

    @property
    def finalized_at(self) -> datetime | None:
        latest = self.history[-1]
        return (
            latest.created_at
            if latest.to_status == "FINALIZED" and not latest.is_snapshot
            else None
        )
