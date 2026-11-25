import logging
from collections.abc import Callable, Mapping
from datetime import timedelta
from threading import Event, Thread

from marketplace.operations.domain.entities.kinds import OperationKind
from marketplace.worker.application.exceptions import OutboxUnavailable
from marketplace.worker.application.interfaces.outbox import OutboxRepository
from marketplace.worker.domain.entities.job import ClaimedJob
from marketplace.worker.domain.exceptions import LeaseLost

type JobHandler[Transaction] = Callable[[Transaction, ClaimedJob], None]

logger = logging.getLogger(__name__)


class OutboxWorker[Transaction]:
    def __init__(
        self,
        outbox: OutboxRepository[Transaction],
        handlers: Mapping[OperationKind, JobHandler[Transaction]],
        worker_id: str,
        *,
        lease_seconds: float = 30,
        poll_seconds: float = 0.25,
        retry_base_seconds: float = 1,
    ) -> None:
        self.outbox = outbox
        self.handlers = {OperationKind(kind): handler for kind, handler in handlers.items()}
        self.worker_id = worker_id
        self.lease = timedelta(seconds=lease_seconds)
        self.poll_seconds = poll_seconds
        self.retry_base_seconds = retry_base_seconds

    def run(self, stop: Event) -> None:
        while not stop.is_set():
            try:
                if self.run_once():
                    continue
            except OutboxUnavailable:
                logger.warning("worker_poll_failed")
            stop.wait(self.poll_seconds)

    def run_once(self) -> bool:
        job = self.outbox.claim(tuple(self.handlers), self.worker_id, self.lease)
        if job is None:
            return False
        done = Event()
        heartbeat = Thread(target=self.renew_until_done, args=(job, done), daemon=True)
        heartbeat.start()
        try:
            self.execute(job)
        except LeaseLost:
            logger.warning("worker_lease_lost", extra={"fields": {"outbox_id": job.outbox_id}})
        except Exception as error:
            # The handler transaction has rolled back. Persist the retry in a new transaction.
            error_type = (
                error.error_type if isinstance(error, OutboxUnavailable) else type(error).__name__
            )
            with self.outbox.transaction() as connection:
                retried = self.outbox.finish(
                    connection,
                    job,
                    error_type=error_type[:64],
                    retry_delay=job.retry_delay(self.retry_base_seconds),
                )
            if retried:
                logger.warning("worker_retry", extra={"fields": {"outbox_id": job.outbox_id}})
        finally:
            done.set()
            heartbeat.join()
        return True

    def execute(self, job: ClaimedJob) -> None:
        # Handler writes and the acknowledgement commit together. A stale lease rolls back both.
        with self.outbox.transaction() as connection:
            if not self.outbox.owns(connection, job):
                raise LeaseLost
            self.handlers[job.kind](connection, job)
            if not self.outbox.finish(connection, job):
                raise LeaseLost

    def renew_until_done(self, job: ClaimedJob, done: Event) -> None:
        # Renew independently while the handler transaction is still open.
        while not done.wait(self.lease.total_seconds() / 3):
            try:
                if not self.outbox.renew(job, self.lease):
                    return
            except OutboxUnavailable:
                logger.warning("worker_lease_renewal_failed")
                return
