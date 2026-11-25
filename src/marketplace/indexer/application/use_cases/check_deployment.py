from marketplace.indexer.application.interfaces.deployments import DeploymentReader


# Loads the deployment through its port and resolves the initial block for the configured chain.
class CheckDeployment:
    def __init__(self, reader: DeploymentReader) -> None:
        self.reader = reader

    def start_block(self, chain_id: int) -> int:
        return self.reader.read().block_for(chain_id)
