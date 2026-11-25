from dataclasses import dataclass

from marketplace.indexer.domain.exceptions import DeploymentChainMismatch


# Holds deployment coordinates and validates the chain before returning the starting block.
@dataclass(frozen=True)
class DeploymentStart:
    chain_id: int
    deploy_block: int

    def block_for(self, chain_id: int) -> int:
        if self.chain_id != chain_id:
            raise DeploymentChainMismatch
        return self.deploy_block
