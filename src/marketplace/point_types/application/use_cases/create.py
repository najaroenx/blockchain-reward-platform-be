from marketplace.identity import MerchantIdentity
from marketplace.point_types.application.interfaces.points import PointTypeRepository
from marketplace.point_types.domain.entities.point_type import PointTypeDraft
from marketplace.point_types.domain.exceptions import PointTypeFailure


class CreatePointTypeRequest:
    def __init__(self, repository: PointTypeRepository) -> None:
        self.repository = repository

    def execute(
        self, *, identity: MerchantIdentity, key: str, draft: PointTypeDraft
    ) -> tuple[str, str]:
        if not key.strip():
            raise PointTypeFailure("IDEMPOTENCY_KEY_INVALID")
        return self.repository.create(identity=identity, key=key, draft=draft)
