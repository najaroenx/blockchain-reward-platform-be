"""Validate the deposit OpenAPI and optionally run its Postman collection locally."""

import argparse
import json
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from openapi_spec_validator import validate
from verify_redeem_api import serve

from marketplace.main import create_app
from marketplace.settings import ApiSettings

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "docs/api/deposits.openapi.json"
COLLECTION = ROOT / "postman/deposits.postman_collection.json"
DEPOSITS_PATHS = ("/v1/deposits", "/v1/deposits/{depositId}")


def refs(value: object) -> set[str]:
    if isinstance(value, Mapping):
        ref = value.get("$ref")
        own = {ref.rsplit("/", 1)[-1]} if isinstance(ref, str) else set()
        return own.union(*(refs(child) for child in value.values()))
    if isinstance(value, list):
        return set().union(*(refs(child) for child in value))
    return set()


def openapi_document(
    title: str = "THB deposit admission",
    paths_to_keep: tuple[str, ...] = DEPOSITS_PATHS,
    *,
    settings: ApiSettings | None = None,
) -> dict[str, Any]:
    settings = settings or ApiSettings(APP_ENV="test", database_url="sqlite:///:memory:")
    source = create_app(settings).openapi()
    paths = {path: source["paths"][path] for path in paths_to_keep}
    schemas = {}
    pending = refs(paths)
    while pending:
        name = pending.pop()
        if name not in schemas:
            schema = source["components"]["schemas"][name]
            schemas[name] = schema
            pending.update(refs(schema))
    return {
        "openapi": source["openapi"],
        "info": {"title": title, "version": "0.1.0"},
        "paths": paths,
        "components": {
            "schemas": schemas,
            "securitySchemes": {
                "HTTPBearer": source["components"]["securitySchemes"]["HTTPBearer"]
            },
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", action="store_true")
    parser.add_argument("--newman", help="Path to the Newman executable")
    args = parser.parse_args()
    document = openapi_document()
    validate(document)
    encoded = json.dumps(document, indent=2, sort_keys=True) + "\n"
    if args.export:
        SPEC.write_text(encoded)
    elif SPEC.read_text() != encoded:
        raise SystemExit("Deposit OpenAPI snapshot differs from the runtime schema")
    if args.newman:
        with TemporaryDirectory(prefix="deposit-postman-") as directory:
            database_url = f"sqlite:///{directory}/deposits.db"
            with patch.dict(os.environ, {"APP_ENV": "test", "DATABASE_URL": database_url}):
                command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
            settings = ApiSettings(
                APP_ENV="test",
                database_url=database_url,
                mock_auth_enabled=True,
                mock_deposit_psp_enabled=True,
            )
            with serve(create_app(settings)) as url:
                subprocess.run(
                    [args.newman, "run", str(COLLECTION), "--env-var", f"baseUrl={url}"],
                    check=True,
                )
    print("Deposit OpenAPI validated and matches the runtime schema.")


if __name__ == "__main__":
    main()
