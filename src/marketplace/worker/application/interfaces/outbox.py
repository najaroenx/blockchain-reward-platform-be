from abc import ABC, abstractmethod
from collections.abc import Sequence
from contextlib import AbstractContextManager
from datetime import timedelta

from marketplace.operations.domain.entities.kinds import OperationKind
from marketplace.worker.domain.entities.job import ClaimedJob


# Defines how the Worker claims jobs, renews leases, and finishes or retries attempts.
# The transaction port lets handlers commit their writes together with acknowledgement.
class OutboxRepository[Transaction](ABC):
    @abstractmethod
    def transaction(self) -> AbstractContextManager[Transaction]: ...

    @abstractmethod
    def claim(
        self, kinds: Sequence[OperationKind], worker_id: str, lease: timedelta
    ) -> ClaimedJob | None: ...

    @abstractmethod
    def owns(self, connection: Transaction, job: ClaimedJob) -> bool: ...

    @abstractmethod
    def renew(self, job: ClaimedJob, lease: timedelta) -> bool: ...

    @abstractmethod
    def finish(
        self,
        connection: Transaction,
        job: ClaimedJob,
        *,
        error_type: str | None = None,
        retry_delay: timedelta = timedelta(0),
    ) -> bool: ...
