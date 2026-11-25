from abc import ABC, abstractmethod

from marketplace.identity import MerchantIdentity
from marketplace.point_types.domain.entities.point_type import PointTypeDraft


class PointTypeRepository(ABC):
    @abstractmethod
    def create(
        self, *, identity: MerchantIdentity, key: str, draft: PointTypeDraft
    ) -> tuple[str, str]: ...
