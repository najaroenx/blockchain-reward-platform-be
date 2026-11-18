import json
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from urllib.parse import urlsplit

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Connection,
    DateTime,
    Engine,
    String,
    Text,
    UniqueConstraint,
    insert,
    select,
    update,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Mapped, Session, mapped_column

from marketplace.deposits.payments import (
    PaymentInstructions,
    PaymentProvider,
    PaymentProviderError,
    PaymentReferenceConflict,
)
from marketplace.infrastructure.database import Base
from marketplace.infrastructure.idempotency import fingerprint
from marketplace.infrastructure.operation_models import IdempotencyKey
from marketplace.infrastructure.sql_idempotency import claim_key

MIN_SATANG = 2000
MAX_SATANG = 5000000
BASE_UNITS_PER_SATANG = 10**16


class DepositError(StrEnum):
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
    IDEMPOTENCY_KEY_IN_PROGRESS = "IDEMPOTENCY_KEY_IN_PROGRESS"
    DEPOSIT_UNAVAILABLE = "DEPOSIT_UNAVAILABLE"
    PAYMENT_UNAVAILABLE = "PAYMENT_UNAVAILABLE"


class DepositFailure(Exception):
    def __init__(self, code: DepositError) -> None:
        self.code = code
        super().__init__(code.value)


class Deposit(Base):
    __tablename__ = "thb_deposit"
    __table_args__ = (
        CheckConstraint("holder_kind = 'MERCHANT'", name="ck_dep_holder_kind"),
        CheckConstraint(
            f"amount_satang BETWEEN {MIN_SATANG} AND {MAX_SATANG}", name="ck_dep_amount_range"
        ),
        CheckConstraint(
            "amount_base = CAST(amount_satang AS TEXT) || '0000000000000000'",
            name="ck_dep_amount_exact",
        ),
        CheckConstraint("status = 'PENDING_PAYMENT'", name="ck_dep_pending_payment"),
        CheckConstraint("expires_at > created_at", name="ck_dep_expiry"),
        UniqueConstraint("provider", "provider_ref", name="uq_deposit_provider_ref"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    holder_kind: Mapped[str] = mapped_column(String(16))
    holder_id: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text)
    provider_ref: Mapped[str] = mapped_column(Text)
    amount_satang: Mapped[int] = mapped_column(BigInteger)
    amount_base: Mapped[str] = mapped_column(String(78))
    status: Mapped[str] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def utc(value: datetime) -> datetime:
    # SQLite stores naive timestamps even when the column has timezone=True.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class SqlDeposits:
    def __init__(self, engine: Engine) -> None:
        self.engine = (
            engine.execution_options(isolation_level="READ COMMITTED")
            if engine.dialect.name == "postgresql"
            else engine
        )

    def create(
        self, *, merchant_id: str, key: str, amount_satang: int, provider: PaymentProvider | None
    ) -> tuple[str, bool]:
        scope = f"merchant:{merchant_id}:POST:/v1/deposits"
        digest = fingerprint({"amountSatang": str(amount_satang)})
        try:
            with self.engine.connect() as connection:
                saved = self._saved(connection, scope, key, digest)
            if saved is not None:
                return saved, True
            if provider is None:
                raise DepositFailure(DepositError.PAYMENT_UNAVAILABLE)

            reference = sha256(json.dumps([scope, key], ensure_ascii=True).encode()).hexdigest()
            now = datetime.now(UTC)
            instructions = self._prepare(provider, reference, amount_satang, now)
            with self.engine.begin() as connection:
                claim = claim_key(connection, scope=scope, key=key, digest=digest, now=now)
                if claim is None:
                    saved = self._saved(connection, scope, key, digest)
                    if saved is None:
                        raise DepositFailure(DepositError.DEPOSIT_UNAVAILABLE)
                    return saved, True

                deposit_id = f"dep_{reference}"
                amount_base = str(amount_satang * BASE_UNITS_PER_SATANG)
                expires_at = utc(instructions.expires_at)
                response = json.dumps(
                    {
                        "depositId": deposit_id,
                        "amountSatang": str(amount_satang),
                        "amountBase": amount_base,
                        "status": "PENDING_PAYMENT",
                        "paymentUrl": instructions.url,
                        "expiresAt": expires_at.isoformat().replace("+00:00", "Z"),
                    },
                    separators=(",", ":"),
                )
                connection.execute(
                    insert(Deposit).values(
                        id=deposit_id,
                        holder_kind="MERCHANT",
                        holder_id=merchant_id,
                        provider=provider.name,
                        provider_ref=instructions.reference,
                        amount_satang=amount_satang,
                        amount_base=amount_base,
                        status="PENDING_PAYMENT",
                        created_at=now,
                        expires_at=expires_at,
                    )
                )
                connection.execute(
                    update(IdempotencyKey)
                    .where(IdempotencyKey.id == claim)
                    .values(deposit_id=deposit_id, response_status=201, response_body=response)
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

    @staticmethod
    def _prepare(
        provider: PaymentProvider, reference: str, amount_satang: int, now: datetime
    ) -> PaymentInstructions:
        try:
            instructions = provider.prepare(reference, amount_satang)
            url = urlsplit(instructions.url)
            if (
                not instructions.reference
                or url.scheme != "https"
                or not url.hostname
                or url.username is not None
                or instructions.expires_at.tzinfo is None
                or instructions.expires_at <= now
            ):
                raise PaymentProviderError
            return instructions
        except PaymentReferenceConflict:
            raise DepositFailure(DepositError.IDEMPOTENCY_KEY_REUSED) from None
        except (PaymentProviderError, OSError, ValueError):
            raise DepositFailure(DepositError.PAYMENT_UNAVAILABLE) from None

    def find(self, *, merchant_id: str, deposit_id: str) -> Deposit | None:
        try:
            with Session(self.engine) as session:
                return session.scalar(
                    select(Deposit).where(
                        Deposit.id == deposit_id,
                        Deposit.holder_kind == "MERCHANT",
                        Deposit.holder_id == merchant_id,
                    )
                )
        except SQLAlchemyError:
            raise DepositFailure(DepositError.DEPOSIT_UNAVAILABLE) from None
