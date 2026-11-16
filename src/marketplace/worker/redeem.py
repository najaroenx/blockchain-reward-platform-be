from marketplace.coupons.application.exceptions import RedeemFailure
from marketplace.coupons.application.execution import RedeemChain
from marketplace.coupons.infrastructure.execution_store import SqlRedeemExecutionStore


class RedeemHandler:
    """Explicit operation handler; no production polling, signing or finality defaults."""

    def __init__(self, store: SqlRedeemExecutionStore, chain: RedeemChain) -> None:
        self.store = store
        self.chain = chain

    def execute(self, operation_id: str) -> dict[str, object] | None:
        result = self.store.result(operation_id)
        if result is not None:
            return result
        payload = self.store.payload(operation_id)
        transaction = self.store.transaction(operation_id)
        if transaction is None:
            try:
                self.chain.verify(payload)
            except RedeemFailure as failure:
                return self.store.refuse(operation_id, failure.code)
            transaction = self.store.remember(operation_id, payload, self.chain.prepare(payload))
        receipt = self.chain.receipt(transaction.tx_hash)
        if receipt is None:
            self.chain.verify(payload)
            self.store.submitted(operation_id)
            self.chain.broadcast(transaction)
            receipt = self.chain.receipt(transaction.tx_hash)
        return None if receipt is None else self.store.project(operation_id, receipt)
