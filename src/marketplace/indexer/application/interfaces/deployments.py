from abc import ABC, abstractmethod

from marketplace.indexer.domain.entities.deployment import DeploymentStart


class DeploymentUnavailable(Exception):
    pass


class DeploymentReader(ABC):
    @abstractmethod
    def read(self) -> DeploymentStart: ...
