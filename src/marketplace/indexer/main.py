import argparse
import json
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from marketplace.settings import ConfigurationError, load_settings


class DeploymentStart(BaseModel):
    schema_version: Literal[1] = Field(validation_alias="schemaVersion")
    status: Literal["ready"]
    chain_id: int = Field(validation_alias="chainId", gt=0)
    deploy_block: int = Field(validation_alias="deployBlock", ge=0)


def main() -> None:
    parser = argparse.ArgumentParser(description="Indexer configuration check")
    parser.add_argument(
        "--check", action="store_true", help="Validate configuration without indexing a chain"
    )
    args = parser.parse_args()
    try:
        config = load_settings("indexer")
    except ConfigurationError as error:
        parser.exit(2, f"{error}\n")
    if not args.check:
        parser.exit(
            2, "Indexer execution is not enabled: deployment and reorg policy are undecided.\n"
        )
    result: dict[str, object] = {
        "role": "indexer",
        "configuration_valid": True,
        "chain_id": config.chain_id,
        "finality_depth": config.finality_depth,
        "indexing_enabled": False,
    }
    if config.deployment_file is not None:
        try:
            deployment = DeploymentStart.model_validate_json(config.deployment_file.read_bytes())
        except (OSError, ValidationError):
            parser.exit(2, "DEPLOYMENT_FILE must contain a ready deployment record\n")
        if deployment.chain_id != config.chain_id:
            parser.exit(2, "DEPLOYMENT_FILE chainId must match CHAIN_ID\n")
        result["deployment_start_block"] = deployment.deploy_block
    print(json.dumps(result))
