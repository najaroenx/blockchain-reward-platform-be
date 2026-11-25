from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from marketplace.indexer.application.interfaces.deployments import (
    DeploymentReader,
    DeploymentUnavailable,
)
from marketplace.indexer.domain.entities.deployment import DeploymentStart


# Validates the deployment file fields needed to determine whether indexing can start.
class DeploymentRecord(BaseModel):
    schema_version: Literal[1] = Field(validation_alias="schemaVersion")
    status: Literal["ready"]
    chain_id: int = Field(validation_alias="chainId", gt=0)
    deploy_block: int = Field(validation_alias="deployBlock", ge=0)


# Implements the deployment reader port by loading the local deployment file.
class FileDeploymentReader(DeploymentReader):
    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> DeploymentStart:
        try:
            record = DeploymentRecord.model_validate_json(self.path.read_bytes())
        except (OSError, ValidationError):
            raise DeploymentUnavailable from None
        return DeploymentStart(record.chain_id, record.deploy_block)
