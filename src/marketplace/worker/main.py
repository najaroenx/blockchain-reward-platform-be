from collections.abc import Mapping
from signal import SIGINT, SIGTERM, signal
from threading import Event
from types import FrameType
from uuid import uuid4

from sqlalchemy import Connection

from marketplace.infrastructure.database import Database
from marketplace.logging_config import configure_logging
from marketplace.operations.domain.entities.kinds import OperationKind
from marketplace.settings import ConfigurationError, load_settings
from marketplace.worker.application.use_cases.process_outbox import JobHandler, OutboxWorker
from marketplace.worker.infrastructure.repositories.outbox import WorkerOutbox
from marketplace.worker.presentation.cli import parse_args, print_configuration


def main(handlers: Mapping[OperationKind, JobHandler[Connection]] | None = None) -> None:
    parser, args = parse_args()
    try:
        config = load_settings("worker")
    except ConfigurationError as error:
        parser.exit(2, f"{error}\n")
    if args.check:
        print_configuration(config.chain_id)
        return
    if not handlers:
        parser.exit(2, "Worker execution is not enabled until operation handlers are registered.\n")

    stop = Event()

    def request_shutdown(_signal: int, _frame: FrameType | None) -> None:
        # Let the current handler finish before the poll loop exits.
        stop.set()

    signal(SIGINT, request_shutdown)
    signal(SIGTERM, request_shutdown)
    configure_logging("worker", config.log_level)
    database = Database(config.database_url.get_secret_value())
    try:
        try:
            worker = OutboxWorker(
                WorkerOutbox(database.engine),
                handlers,
                uuid4().hex,
                lease_seconds=config.worker_lease_seconds,
            )
        except ValueError as error:
            parser.exit(2, f"{error}\n")
        worker.run(stop)
    finally:
        database.close()
