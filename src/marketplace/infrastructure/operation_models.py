from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from marketplace.infrastructure.database import Base


# Stores the current operation state, payload, initiator, and event sequence.
class Operation(Base):
    __tablename__ = "operation"
    __table_args__ = (
        Index("ix_operation_actor_recent", "actor_user_id", "created_at", "id"),
        Index(
            "uq_provision_inflight",
            "subject_id",
            unique=True,
            postgresql_where=text(
                "kind = 'MERCHANT_PROVISION' AND status NOT IN ('FINALIZED', 'CHAIN_REVERTED')"
            ),
            sqlite_where=text(
                "kind = 'MERCHANT_PROVISION' AND status NOT IN ('FINALIZED', 'CHAIN_REVERTED')"
            ),
        ),
        Index(
            "uq_point_type_inflight",
            "subject_id",
            unique=True,
            postgresql_where=text(
                "kind = 'POINT_TYPE_CREATE' AND status NOT IN ('FINALIZED', 'CHAIN_REVERTED')"
            ),
            sqlite_where=text(
                "kind = 'POINT_TYPE_CREATE' AND status NOT IN ('FINALIZED', 'CHAIN_REVERTED')"
            ),
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    actor_user_id: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, str]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    event_seq: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    subject_id: Mapped[str | None] = mapped_column(Text)


# Stores the ordered transaction steps and signer roles planned for an operation.
class OperationPlanStep(Base):
    __tablename__ = "operation_plan_step"
    __table_args__ = (
        CheckConstraint("step_index >= 1", name="ck_plan_step_index"),
        CheckConstraint(
            "signer_role IN ('MARKETPLACE_OWNER','MERCHANT_WALLET')", name="ck_plan_step_signer"
        ),
    )

    operation_id: Mapped[str] = mapped_column(ForeignKey("operation.id"), primary_key=True)
    step_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    signer_role: Mapped[str] = mapped_column(String(24))


# Records the events that deferred database constraints require in operation history.
class OperationEventExpected(Base):
    __tablename__ = "operation_event_expected"
    __table_args__ = (
        UniqueConstraint("operation_id", "seq", "to_status", name="uq_opexpected_status"),
        ForeignKeyConstraint(
            ["operation_id", "seq", "to_status"],
            ["operation_event.operation_id", "operation_event.seq", "operation_event.to_status"],
            name="fk_opexpected_event",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
    )

    operation_id: Mapped[str] = mapped_column(ForeignKey("operation.id"), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    to_status: Mapped[str] = mapped_column(String(32))


# Persists a sequenced state change in an operation's history.
class OperationEvent(Base):
    __tablename__ = "operation_event"
    __table_args__ = (
        UniqueConstraint("operation_id", "seq", "to_status", name="uq_opevent_status"),
        ForeignKeyConstraint(
            ["operation_id", "seq", "to_status"],
            [
                "operation_event_expected.operation_id",
                "operation_event_expected.seq",
                "operation_event_expected.to_status",
            ],
            name="fk_opevent_expected",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["operation_id", "previous_seq", "from_status"],
            ["operation_event.operation_id", "operation_event.seq", "operation_event.to_status"],
            name="fk_opevent_previous",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("seq >= 1 AND ((seq = 1) = (from_status IS NULL))", name="ck_opevent_seq"),
        CheckConstraint(
            "actor IN ('SYSTEM','MERCHANT_ADMIN','CUSTOMER','PLATFORM_ADMIN')",
            name="ck_opevent_actor",
        ),
    )

    operation_id: Mapped[str] = mapped_column(ForeignKey("operation.id"), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    previous_seq: Mapped[int | None] = mapped_column(
        Integer, Computed("CASE WHEN seq > 1 THEN seq - 1 END", persisted=True)
    )
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(24), server_default="SYSTEM")
    tx_hash: Mapped[str | None] = mapped_column(String(66))
    error_code: Mapped[str | None] = mapped_column(String(64))
    is_snapshot: Mapped[bool] = mapped_column(server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# Stores the request fingerprint and original response for replay under the same scope and key.
class IdempotencyKey(Base):
    __tablename__ = "idempotency_key"
    __table_args__ = (
        UniqueConstraint("scope", "key", name="uq_idem"),
        CheckConstraint("length(key) BETWEEN 8 AND 255", name="ck_idem_key_length"),
        CheckConstraint("length(fingerprint) = 32", name="ck_idem_fingerprint_length"),
        CheckConstraint(
            "(operation_id IS NULL AND deposit_id IS NULL "
            "AND response_status IS NULL AND response_body IS NULL) OR "
            "(((operation_id IS NOT NULL AND deposit_id IS NULL) OR "
            "(operation_id IS NULL AND deposit_id IS NOT NULL)) AND response_status IS NOT NULL "
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
    deposit_id: Mapped[str | None] = mapped_column(
        ForeignKey("thb_deposit.id", name="fk_idem_deposit")
    )
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# Persists queued operation work and records when the Worker acknowledges it.
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


# Persists the lease, outcome, and retry schedule for one attempt at an outbox job.
class WorkerAttempt(Base):
    __tablename__ = "worker_attempt"
    __table_args__ = (
        UniqueConstraint("outbox_id", "attempt_no", name="uq_worker_attempt_number"),
        Index(
            "uq_worker_attempt_active",
            "outbox_id",
            unique=True,
            postgresql_where=text("finished_at IS NULL"),
            sqlite_where=text("finished_at IS NULL"),
        ),
        CheckConstraint("attempt_no > 0", name="ck_worker_attempt_number"),
        CheckConstraint("outcome IN ('SUCCEEDED', 'RETRY')", name="ck_worker_attempt_outcome"),
        CheckConstraint(
            "(finished_at IS NULL) = (outcome IS NULL)", name="ck_worker_attempt_completion"
        ),
        CheckConstraint(
            "COALESCE(outcome = 'RETRY', false) = (retry_at IS NOT NULL)",
            name="ck_worker_attempt_retry",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    outbox_id: Mapped[str] = mapped_column(ForeignKey("outbox.id"))
    attempt_no: Mapped[int] = mapped_column(Integer)
    worker_id: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(16))
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_type: Mapped[str | None] = mapped_column(String(64))


# Persists a transaction step and its signing, submission, and confirmation state.
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


# Stores an indexed chain log under its chain, transaction hash, and log index.
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


# Stores the indexed outcome of a confirmed or reverted redemption transaction.
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


# Stores the reason the Worker refused a redemption before transaction submission.
class RedeemRefusal(Base):
    __tablename__ = "redeem_refusal"

    operation_id: Mapped[str] = mapped_column(ForeignKey("operation.id"), primary_key=True)
    code: Mapped[str] = mapped_column(String(64))
    refused_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
