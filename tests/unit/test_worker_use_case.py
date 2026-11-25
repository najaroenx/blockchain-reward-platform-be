from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import timedelta

import pytest

from marketplace.operations.domain.entities.kinds import OperationKind
from marketplace.worker.application.interfaces.outbox import OutboxRepository
from marketplace.worker.application.use_cases.process_outbox import OutboxWorker
from marketplace.worker.domain.entities.job import ClaimedJob


class FakeOutbox(OutboxRepository[object]):
    def __init__(self, job: ClaimedJob | None) -> None:
        self.job = job
        self.transactions: list[object] = []
        self.finished: list[tuple[object, str | None, timedelta]] = []

    @contextmanager
    def transaction(self) -> Iterator[object]:
        connection = object()
        self.transactions.append(connection)
        yield connection

    def claim(
        self, kinds: Sequence[OperationKind], worker_id: str, lease: timedelta
    ) -> ClaimedJob | None:
        return self.job

    def owns(self, connection: object, job: ClaimedJob) -> bool:
        return True

    def renew(self, job: ClaimedJob, lease: timedelta) -> bool:
        return True

    def finish(
        self,
        connection: object,
        job: ClaimedJob,
        *,
        error_type: str | None = None,
        retry_delay: timedelta = timedelta(0),
    ) -> bool:
        self.finished.append((connection, error_type, retry_delay))
        return True


def test_an_empty_outbox_does_not_start_a_transaction() -> None:
    outbox = FakeOutbox(None)
    worker = OutboxWorker(outbox, {}, "worker")

    assert not worker.run_once()
    assert outbox.transactions == []


@pytest.mark.parametrize("should_fail", [False, True])
def test_worker_runs_handler_and_records_result_with_injected_repository(
    should_fail: bool,
) -> None:
    kind = OperationKind.COUPON_REDEEM
    job = ClaimedJob("outbox", "operation", kind, {}, "lease", 2)
    outbox = FakeOutbox(job)
    handled: list[tuple[object, ClaimedJob]] = []

    def handle(transaction: object, claimed: ClaimedJob) -> None:
        handled.append((transaction, claimed))
        if should_fail:
            raise RuntimeError

    assert OutboxWorker(outbox, {kind: handle}, "worker").run_once()
    assert handled == [(outbox.transactions[0], job)]
    if should_fail:
        assert len(outbox.transactions) == 2
        assert outbox.finished == [(outbox.transactions[1], "RuntimeError", timedelta(seconds=2))]
    else:
        assert outbox.finished == [(outbox.transactions[0], None, timedelta(0))]
