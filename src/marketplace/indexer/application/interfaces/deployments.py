from abc import ABC, abstractmethod

from marketplace.indexer.domain.entities.deployment import DeploymentStart


# Signals that the Indexer could not load a usable deployment record.
class DeploymentUnavailable(Exception):
    pass


# Defines the port the Indexer uses to obtain deployment coordinates.
class DeploymentReader(ABC):
    @abstractmethod
    def read(self) -> DeploymentStart: ...
