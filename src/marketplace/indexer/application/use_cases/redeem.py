from marketplace.coupons.application.interfaces.execution import (
    RedeemProjectionStore,
    RedeemReceipt,
)


class RedeemProjector:
    """Project a receipt already accepted by an explicit confirmation-policy adapter."""

    def __init__(self, store: RedeemProjectionStore) -> None:
        self.store = store

    def project(self, operation_id: str, receipt: RedeemReceipt) -> dict[str, object]:
        return self.store.project(operation_id, receipt)
