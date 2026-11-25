from abc import ABC, abstractmethod
from datetime import datetime

from marketplace.operation_status import OperationStatus
from marketplace.operations.domain.entities.kinds import OperationKind
from marketplace.operations.domain.entities.operation import (
    OperationSnapshot,
    OperationSummaryRecord,
)


class OperationReader(ABC):
    @abstractmethod
    def page(
        self,
        user_id: str,
        *,
        status: OperationStatus | None,
        kind: OperationKind | None,
        before: tuple[datetime, str] | None,
        limit: int,
    ) -> tuple[OperationSummaryRecord, ...]: ...

    @abstractmethod
    def find(self, user_id: str, operation_id: str) -> OperationSnapshot | None: ...
