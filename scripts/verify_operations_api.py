"""Validate the operation status OpenAPI and run its Postman collection locally."""

import argparse
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from openapi_spec_validator import validate
from sqlalchemy import insert
from verify_deposit_api import openapi_document
from verify_redeem_api import serve

from marketplace.infrastructure.database import Database
from marketplace.infrastructure.operation_history import advance_operation, record_operation_created
from marketplace.infrastructure.operation_models import Operation
from marketplace.main import create_app
from marketplace.settings import ApiSettings

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "docs/api/operations.openapi.json"
COLLECTION = ROOT / "postman/operations.postman_collection.json"
URLS = ("/v1/operations", "/v1/operations/{operationId}")


def seed_operations(database_url: str) -> None:
    database = Database(database_url)
    try:
        with database.engine.begin() as connection:
            at = datetime.now(UTC)
            for operation_id, actor in (
                ("op_localdemo", "mock-user"),
                ("op_foreigndemo", "other-user"),
            ):
                connection.execute(
                    insert(Operation).values(
                        id=operation_id,
                        actor_user_id=actor,
                        kind="COUPON_REDEEM",
                        status="PENDING",
                        payload={},
                        created_at=at,
                    )
                )
                record_operation_created(connection, operation_id, at)
            for status in ("SUBMITTED", "CONFIRMED", "ORPHANED"):
                advance_operation(connection, "op_localdemo", status)
    finally:
        database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", action="store_true")
    parser.add_argument("--newman", help="Path to the Newman executable")
    args = parser.parse_args()
    document = openapi_document("Operation status", URLS)
    validate(document)
    encoded = json.dumps(document, indent=2, sort_keys=True) + "\n"
    if args.export:
        SPEC.write_text(encoded)
    elif SPEC.read_text() != encoded:
        raise SystemExit("Operation OpenAPI snapshot differs from the runtime schema")
    if args.newman:
        with TemporaryDirectory(prefix="operations-postman-") as directory:
            database_url = os.environ.get(
                "OPERATIONS_POSTMAN_DATABASE_URL", f"sqlite:///{directory}/test.db"
            )
            with patch.dict(os.environ, {"APP_ENV": "test", "DATABASE_URL": database_url}):
                command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
            seed_operations(database_url)
            settings = ApiSettings(
                APP_ENV="test", database_url=database_url, mock_auth_enabled=True
            )
            with serve(create_app(settings)) as url:
                subprocess.run(
                    [args.newman, "run", str(COLLECTION), "--env-var", f"baseUrl={url}"],
                    check=True,
                )
    print("Operations OpenAPI validated and matches the runtime schema.")


if __name__ == "__main__":
    main()
