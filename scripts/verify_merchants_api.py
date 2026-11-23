"""Validate merchant signup OpenAPI and run the local Postman collection."""

import argparse
import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from openapi_spec_validator import validate
from verify_deposit_api import openapi_document
from verify_redeem_api import serve

from marketplace.main import create_app
from marketplace.settings import ApiSettings

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "docs/api/merchants.openapi.json"
COLLECTION = ROOT / "postman/merchants.postman_collection.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", action="store_true")
    parser.add_argument("--newman", help="Path to the Newman executable")
    args = parser.parse_args()
    document = openapi_document("Merchant signup", ("/v1/merchants",))
    validate(document)
    encoded = json.dumps(document, indent=2, sort_keys=True) + "\n"
    if args.export:
        SPEC.write_text(encoded)
    elif SPEC.read_text() != encoded:
        raise SystemExit("Merchant OpenAPI snapshot differs from the runtime schema")
    if args.newman:
        with TemporaryDirectory(prefix="merchant-postman-") as directory:
            database_url = os.environ.get(
                "SIGNUP_POSTMAN_DATABASE_URL", f"sqlite:///{directory}/test.db"
            )
            with patch.dict(os.environ, {"APP_ENV": "test", "DATABASE_URL": database_url}):
                command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
            settings = ApiSettings(
                APP_ENV="test", database_url=database_url, mock_merchant_wallet_enabled=True
            )
            with serve(create_app(settings)) as url:
                subprocess.run(
                    [args.newman, "run", str(COLLECTION), "--env-var", f"baseUrl={url}"],
                    check=True,
                )
    print("Merchant OpenAPI validated and matches the runtime schema.")


if __name__ == "__main__":
    main()
