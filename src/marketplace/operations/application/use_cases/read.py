from datetime import datetime

from marketplace.operation_status import OperationStatus
from marketplace.operations.application.exceptions import OperationNotFound, OperationsUnavailable
from marketplace.operations.application.interfaces.reader import OperationReader
from marketplace.operations.domain.entities.kinds import OperationKind
from marketplace.operations.domain.entities.operation import (
    OperationSnapshot,
    OperationSummaryRecord,
)


# Coordinates operation reads, checks snapshot consistency, and prepares the next page cursor.
class ReadOperations:
    def __init__(self, repository: OperationReader) -> None:
        self.repository = repository

    def page(
        self,
        user_id: str,
        *,
        status: OperationStatus | None,
        kind: OperationKind | None,
        before: tuple[datetime, str] | None,
        limit: int,
    ) -> tuple[tuple[OperationSummaryRecord, ...], bool]:
        rows = self.repository.page(
            user_id, status=status, kind=kind, before=before, limit=limit + 1
        )
        return rows[:limit], len(rows) > limit

    def detail(self, user_id: str, operation_id: str) -> OperationSnapshot:
        snapshot = self.repository.find(user_id, operation_id)
        if snapshot is None:
            raise OperationNotFound
        if not snapshot.has_current_event():
            raise OperationsUnavailable
        return snapshot
