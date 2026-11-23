from marketplace.coupons.application.execution import RedeemReceipt
from marketplace.coupons.infrastructure.execution_store import SqlRedeemExecutionStore


class RedeemProjector:
    """Project a receipt already accepted by an explicit confirmation-policy adapter."""

    def __init__(self, store: SqlRedeemExecutionStore) -> None:
        self.store = store

    def project(self, operation_id: str, receipt: RedeemReceipt) -> dict[str, object]:
        return self.store.project(operation_id, receipt)
