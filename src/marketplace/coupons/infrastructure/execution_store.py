from datetime import UTC, datetime

from eth_utils.crypto import keccak
from sqlalchemy import Engine, insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from marketplace.coupons.application.dto import RedeemCouponPayload
from marketplace.coupons.application.exceptions import RedeemErrorCode
from marketplace.coupons.application.execution import (
    ReceiptCorrelationError,
    RedeemReceipt,
    SignedRedeem,
)
from marketplace.coupons.infrastructure.receipts import correlate_receipt
from marketplace.infrastructure.operation_models import (
    ChainEvent,
    Operation,
    OperationStep,
    RedeemRefusal,
    RedeemResult,
)


class SqlRedeemExecutionStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = (
            engine.execution_options(isolation_level="READ COMMITTED")
            if engine.dialect.name == "postgresql"
            else engine
        )

    def payload(self, operation_id: str) -> RedeemCouponPayload:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(Operation.kind, Operation.payload).where(Operation.id == operation_id)
            ).one()
        if row.kind != "COUPON_REDEEM":
            raise ValueError("Operation is not a coupon redemption")
        data = row.payload
        if data["amount"] != "1":
            raise ValueError("Redeem amount must be one")
        return RedeemCouponPayload(
            chain_id=int(data["chainId"]),
            coupon_contract_address=data["couponContractAddress"],
            wallet_address=data["walletAddress"],
            type_id=int(data["typeId"]),
        )

    def transaction(self, operation_id: str) -> SignedRedeem | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(OperationStep.__table__).where(
                        OperationStep.operation_id == operation_id
                    )
                )
                .mappings()
                .one_or_none()
            )
        return (
            None
            if row is None
            else SignedRedeem(
                raw_tx=row["raw_tx"],
                tx_hash=row["tx_hash"],
                signer_address=row["signer_address"],
                nonce=int(row["nonce"]),
            )
        )

    def remember(
        self, operation_id: str, payload: RedeemCouponPayload, signed: SignedRedeem
    ) -> SignedRedeem:
        if (
            not signed.raw_tx
            or signed.tx_hash.lower() != "0x" + keccak(signed.raw_tx).hex()
            or signed.nonce < 0
        ):
            raise ValueError("Invalid signed transaction")
        with self.engine.begin() as connection:
            connection.execute(
                select(Operation.id).where(Operation.id == operation_id).with_for_update()
            ).scalar_one()
            refused = connection.execute(
                select(RedeemRefusal.operation_id).where(RedeemRefusal.operation_id == operation_id)
            ).scalar_one_or_none()
            if refused is not None:
                raise ReceiptCorrelationError(
                    "Operation requires manual review before transaction preparation"
                )
            claim = (
                (postgres_insert if connection.dialect.name == "postgresql" else sqlite_insert)(
                    OperationStep
                )
                .values(
                    operation_id=operation_id,
                    chain_id=str(payload.chain_id),
                    signer_address=signed.signer_address.lower(),
                    nonce=str(signed.nonce),
                    raw_tx=signed.raw_tx,
                    tx_hash=signed.tx_hash.lower(),
                    status="PREPARED",
                    created_at=datetime.now(UTC),
                )
                .on_conflict_do_nothing(index_elements=["operation_id"])
            )
            connection.execute(claim)
        saved = self.transaction(operation_id)
        assert saved is not None
        return saved

    def submitted(self, operation_id: str) -> None:
        now = datetime.now(UTC)
        with self.engine.begin() as connection:
            changed = connection.execute(
                update(OperationStep)
                .where(
                    OperationStep.operation_id == operation_id, OperationStep.status == "PREPARED"
                )
                .values(status="SUBMITTED", submitted_at=now)
            ).rowcount
            if changed:
                connection.execute(
                    update(Operation).where(Operation.id == operation_id).values(status="SUBMITTED")
                )

    def project(self, operation_id: str, receipt: RedeemReceipt) -> dict[str, object]:
        payload = self.payload(operation_id)
        transaction = self.transaction(operation_id)
        if transaction is None:
            raise ReceiptCorrelationError("Receipt has no persisted signed transaction")
        result, logs = correlate_receipt(payload, transaction.tx_hash, receipt)
        status = "CONFIRMED" if receipt.status == 1 else "CHAIN_REVERTED"
        with self.engine.begin() as connection:
            claim = (
                (postgres_insert if connection.dialect.name == "postgresql" else sqlite_insert)(
                    RedeemResult
                )
                .values(
                    operation_id=operation_id,
                    tx_hash=receipt.tx_hash.lower(),
                    block_hash=receipt.block_hash.lower(),
                    block_number=str(receipt.block_number),
                    mined_at=receipt.mined_at,
                    status=status,
                    result=result,
                )
                .on_conflict_do_nothing(index_elements=["operation_id"])
                .returning(RedeemResult.operation_id)
            )
            inserted = connection.execute(claim).scalar_one_or_none()
            if inserted is None:
                saved = (
                    connection.execute(
                        select(RedeemResult.__table__).where(
                            RedeemResult.operation_id == operation_id
                        )
                    )
                    .mappings()
                    .one()
                )
                if (
                    saved["tx_hash"] != receipt.tx_hash.lower()
                    or saved["block_hash"] != receipt.block_hash.lower()
                    or saved["status"] != status
                    or saved["result"] != result
                ):
                    raise ReceiptCorrelationError(
                        "Receipt conflicts with an existing projection; reorg policy required"
                    )
                return dict(saved["result"])
            for log in logs:
                connection.execute(
                    insert(ChainEvent).values(
                        chain_id=str(payload.chain_id),
                        tx_hash=receipt.tx_hash.lower(),
                        log_index=log.log_index,
                        operation_id=operation_id,
                        contract_address=log.address.lower(),
                        block_hash=receipt.block_hash.lower(),
                        block_number=str(receipt.block_number),
                        topics=list(log.topics),
                        data=log.data,
                        mined_at=receipt.mined_at,
                    )
                )
            connection.execute(
                update(OperationStep)
                .where(OperationStep.operation_id == operation_id)
                .values(status=status)
            )
            connection.execute(
                update(Operation).where(Operation.id == operation_id).values(status=status)
            )
        return result

    def result(self, operation_id: str) -> dict[str, object] | None:
        with self.engine.connect() as connection:
            saved = connection.execute(
                select(RedeemResult.result).where(RedeemResult.operation_id == operation_id)
            ).scalar_one_or_none()
            if saved is None:
                refusal = connection.execute(
                    select(RedeemRefusal.code).where(RedeemRefusal.operation_id == operation_id)
                ).scalar_one_or_none()
                if refusal is not None:
                    return {"code": refusal}
        return None if saved is None else dict(saved)

    def refuse(self, operation_id: str, code: RedeemErrorCode) -> dict[str, object]:
        with self.engine.begin() as connection:
            # Lock the operation so refusal cannot race a signed-transaction claim.
            connection.execute(
                select(Operation.id).where(Operation.id == operation_id).with_for_update()
            ).scalar_one()
            existing = connection.execute(
                select(OperationStep.operation_id).where(OperationStep.operation_id == operation_id)
            ).scalar_one_or_none()
            if existing is not None:
                raise ReceiptCorrelationError(
                    "Signed transaction exists; its chain outcome must be resolved"
                )
            claim = (
                (postgres_insert if connection.dialect.name == "postgresql" else sqlite_insert)(
                    RedeemRefusal
                )
                .values(operation_id=operation_id, code=code.value, refused_at=datetime.now(UTC))
                .on_conflict_do_nothing(index_elements=["operation_id"])
            )
            connection.execute(claim)
            saved_code = connection.execute(
                select(RedeemRefusal.code).where(RedeemRefusal.operation_id == operation_id)
            ).scalar_one()
            connection.execute(
                update(Operation).where(Operation.id == operation_id).values(status="MANUAL_REVIEW")
            )
        return {"code": saved_code}
