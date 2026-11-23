"""Verify the point-type admission OpenAPI snapshot against the enabled route."""

import argparse
import json
from pathlib import Path

from openapi_spec_validator import validate
from verify_deposit_api import openapi_document

from marketplace.settings import ApiSettings

SPEC = Path(__file__).resolve().parents[1] / "docs/api/point-types.openapi.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", action="store_true")
    args = parser.parse_args()
    settings = ApiSettings(
        APP_ENV="test", database_url="sqlite:///:memory:", point_type_admission_enabled=True
    )
    document = openapi_document("Point type admission", ("/v1/point-types",), settings=settings)
    validate(document)
    encoded = json.dumps(document, indent=2, sort_keys=True) + "\n"
    if args.export:
        SPEC.write_text(encoded)
    elif SPEC.read_text() != encoded:
        raise SystemExit("Point type OpenAPI snapshot differs from the runtime schema")
    print("Point type OpenAPI validated and matches the runtime schema.")


if __name__ == "__main__":
    main()
