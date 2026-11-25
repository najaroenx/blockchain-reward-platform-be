from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta

from marketplace.operations.domain.entities.kinds import OperationKind


@dataclass(frozen=True)
class ClaimedJob:
    outbox_id: str
    operation_id: str
    kind: OperationKind
    payload: Mapping[str, object]
    lease_id: str
    attempt_no: int

    def __post_init__(self) -> None:
        if self.attempt_no < 1:
            raise ValueError("An attempt number must be positive")

    def retry_delay(self, base_seconds: float) -> timedelta:
        seconds = min(base_seconds * (2 ** min(self.attempt_no - 1, 6)), 60)
        return timedelta(seconds=seconds)
