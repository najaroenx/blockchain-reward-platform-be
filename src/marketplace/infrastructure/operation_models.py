from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from marketplace.infrastructure.database import Base


class Operation(Base):
    __tablename__ = "operation"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    actor_user_id: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, str]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class IdempotencyKey(Base):
    __tablename__ = "idempotency_key"
    __table_args__ = (
        UniqueConstraint("scope", "key", name="uq_idem"),
        CheckConstraint("length(key) BETWEEN 8 AND 255", name="ck_idem_key_length"),
        CheckConstraint("length(fingerprint) = 32", name="ck_idem_fingerprint_length"),
        CheckConstraint(
            "(operation_id IS NULL AND response_status IS NULL AND response_body IS NULL) OR "
            "(operation_id IS NOT NULL AND response_status IS NOT NULL "
            "AND response_status BETWEEN 200 AND 599 AND response_body IS NOT NULL)",
            name="ck_idem_acknowledgement",
        ),
        Index("ix_idem_expiry", "expires_at"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    scope: Mapped[str] = mapped_column(Text)
    key: Mapped[str] = mapped_column(String(255))
    fingerprint: Mapped[bytes] = mapped_column(LargeBinary(32))
    operation_id: Mapped[str | None] = mapped_column(ForeignKey("operation.id"))
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Outbox(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        UniqueConstraint("operation_id", name="uq_outbox_operation"),
        Index("ix_outbox_pending", "published_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    operation_id: Mapped[str] = mapped_column(ForeignKey("operation.id"))
    event_type: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OperationStep(Base):
    __tablename__ = "operation_step"
    __table_args__ = (
        UniqueConstraint("chain_id", "tx_hash", name="uq_step_chain_tx"),
        UniqueConstraint("chain_id", "signer_address", "nonce", name="uq_step_signer_nonce"),
        CheckConstraint("length(raw_tx) > 0", name="ck_step_raw_transaction"),
        CheckConstraint(
            "status IN ('PREPARED','SUBMITTED','CONFIRMED','CHAIN_REVERTED')", name="ck_step_status"
        ),
    )

    operation_id: Mapped[str] = mapped_column(ForeignKey("operation.id"), primary_key=True)
    chain_id: Mapped[str] = mapped_column(String(78))
    signer_address: Mapped[str] = mapped_column(String(42))
    nonce: Mapped[str] = mapped_column(String(78))
    raw_tx: Mapped[bytes] = mapped_column(LargeBinary)
    tx_hash: Mapped[str] = mapped_column(String(66))
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChainEvent(Base):
    __tablename__ = "chain_event"

    chain_id: Mapped[str] = mapped_column(String(78), primary_key=True)
    tx_hash: Mapped[str] = mapped_column(String(66), primary_key=True)
    log_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    operation_id: Mapped[str] = mapped_column(ForeignKey("operation.id"))
    contract_address: Mapped[str] = mapped_column(String(42))
    block_hash: Mapped[str] = mapped_column(String(66))
    block_number: Mapped[str] = mapped_column(String(78))
    topics: Mapped[list[str]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    data: Mapped[str] = mapped_column(Text)
    mined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RedeemResult(Base):
    __tablename__ = "redeem_result"
    __table_args__ = (
        CheckConstraint("status IN ('CONFIRMED','CHAIN_REVERTED')", name="ck_redeem_result_status"),
    )

    operation_id: Mapped[str] = mapped_column(
        ForeignKey("operation_step.operation_id"), primary_key=True
    )
    tx_hash: Mapped[str] = mapped_column(String(66))
    block_hash: Mapped[str] = mapped_column(String(66))
    block_number: Mapped[str] = mapped_column(String(78))
    mined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32))
    result: Mapped[dict[str, object]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))


class RedeemRefusal(Base):
    __tablename__ = "redeem_refusal"

    operation_id: Mapped[str] = mapped_column(ForeignKey("operation.id"), primary_key=True)
    code: Mapped[str] = mapped_column(String(64))
    refused_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
