import json

from sqlalchemy import Connection, Engine, insert, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from marketplace.deposits.application.interfaces.deposits import DepositAdmission, DepositRepository
from marketplace.deposits.domain.entities.deposit import DepositRecord, utc
from marketplace.deposits.domain.exceptions import DepositError, DepositFailure
from marketplace.deposits.infrastructure.models import Deposit
from marketplace.infrastructure.operation_models import IdempotencyKey
from marketplace.infrastructure.sql_idempotency import claim_key


# Implements the deposit port with SQL and commits the deposit and replay response together.
class SqlDeposits(DepositRepository):
    def __init__(self, engine: Engine) -> None:
        self.engine = (
            engine.execution_options(isolation_level="READ COMMITTED")
            if engine.dialect.name == "postgresql"
            else engine
        )

    def replay(self, scope: str, key: str, digest: bytes) -> str | None:
        try:
            with self.engine.connect() as connection:
                return self._saved(connection, scope, key, digest)
        except SQLAlchemyError:
            raise DepositFailure(DepositError.DEPOSIT_UNAVAILABLE) from None

    def save(
        self, deposit: DepositAdmission, *, scope: str, key: str, digest: bytes
    ) -> tuple[str, bool]:
        try:
            with self.engine.begin() as connection:
                claim = claim_key(
                    connection, scope=scope, key=key, digest=digest, now=deposit.created_at
                )
                if claim is None:
                    saved = self._saved(connection, scope, key, digest)
                    if saved is None:
                        raise DepositFailure(DepositError.DEPOSIT_UNAVAILABLE)
                    return saved, True

                expires_at = utc(deposit.instructions.expires_at)
                response = json.dumps(
                    {
                        "depositId": deposit.id,
                        "amountSatang": str(deposit.amount.satang),
                        "amountBase": deposit.amount.base_units,
                        "status": "PENDING_PAYMENT",
                        "paymentUrl": deposit.instructions.url,
                        "expiresAt": expires_at.isoformat().replace("+00:00", "Z"),
                    },
                    separators=(",", ":"),
                )
                connection.execute(
                    insert(Deposit).values(
                        id=deposit.id,
                        holder_kind="MERCHANT",
                        holder_id=deposit.merchant_id,
                        provider=deposit.provider,
                        provider_ref=deposit.instructions.reference,
                        amount_satang=deposit.amount.satang,
                        amount_base=deposit.amount.base_units,
                        status="PENDING_PAYMENT",
                        created_at=deposit.created_at,
                        expires_at=expires_at,
                    )
                )
                connection.execute(
                    update(IdempotencyKey)
                    .where(IdempotencyKey.id == claim)
                    .values(deposit_id=deposit.id, response_status=201, response_body=response)
                )
                return response, False
        except SQLAlchemyError:
            raise DepositFailure(DepositError.DEPOSIT_UNAVAILABLE) from None

    @staticmethod
    def _saved(connection: Connection, scope: str, key: str, digest: bytes) -> str | None:
        row = (
            connection.execute(
                select(
                    IdempotencyKey.fingerprint,
                    IdempotencyKey.deposit_id,
                    IdempotencyKey.response_status,
                    IdempotencyKey.response_body,
                ).where(IdempotencyKey.scope == scope, IdempotencyKey.key == key)
            )
            .tuples()
            .one_or_none()
        )
        if row is None:
            return None
        saved_digest, deposit_id, status, body = row
        if saved_digest != digest:
            raise DepositFailure(DepositError.IDEMPOTENCY_KEY_REUSED)
        if deposit_id is None or body is None:
            raise DepositFailure(DepositError.IDEMPOTENCY_KEY_IN_PROGRESS)
        if status != 201:
            raise DepositFailure(DepositError.DEPOSIT_UNAVAILABLE)
        return body

    def find(self, *, merchant_id: str, deposit_id: str) -> DepositRecord | None:
        try:
            with Session(self.engine) as session:
                row = session.scalar(
                    select(Deposit).where(
                        Deposit.id == deposit_id,
                        Deposit.holder_kind == "MERCHANT",
                        Deposit.holder_id == merchant_id,
                    )
                )
                if row is None:
                    return None
                return DepositRecord(
                    row.id,
                    row.amount_satang,
                    row.amount_base,
                    utc(row.created_at),
                    utc(row.expires_at),
                )
        except SQLAlchemyError:
            raise DepositFailure(DepositError.DEPOSIT_UNAVAILABLE) from None
