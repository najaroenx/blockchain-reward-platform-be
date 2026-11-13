from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    false,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from marketplace.auth.domain.entities import MerchantStatus
from marketplace.infrastructure.database import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class Merchant(Base):
    __tablename__ = "merchant"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','ACTIVE','SUSPENDED','CLOSED')", name="ck_merchant_status"
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    status: Mapped[MerchantStatus] = mapped_column(
        String, default="PENDING", server_default="PENDING"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.current_timestamp()
    )


class MerchantAdmin(Base):
    __tablename__ = "merchant_admin"
    __table_args__ = (
        CheckConstraint(
            "email_normalized = lower(trim(email_normalized))", name="ck_admin_email_normalized"
        ),
        CheckConstraint("password_hash LIKE '$argon2id$%'", name="ck_admin_password_argon2id"),
        Index("uq_admin_email", "email_normalized", unique=True),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchant.id"))
    email: Mapped[str] = mapped_column(String(254))
    email_normalized: Mapped[str] = mapped_column(String(254))
    password_hash: Mapped[str] = mapped_column(String)
    display_name: Mapped[str] = mapped_column(String)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.current_timestamp()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Customer(Base):
    """Session owner foundation from FEAT-03; OTP provisioning remains a separate task."""

    __tablename__ = "customer"
    __table_args__ = (Index("uq_customer_tel", "tel_hash", unique=True),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tel_hash: Mapped[bytes] = mapped_column(LargeBinary(32))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.current_timestamp()
    )


class AuthSession(Base):
    __tablename__ = "auth_session"
    __table_args__ = (
        CheckConstraint(
            "(merchant_admin_id IS NOT NULL AND customer_id IS NULL) OR "
            "(merchant_admin_id IS NULL AND customer_id IS NOT NULL)",
            name="ck_one_subject",
        ),
        CheckConstraint(
            "(subject_kind = 'MERCHANT_ADMIN' AND merchant_admin_id IS NOT NULL) OR "
            "(subject_kind = 'CUSTOMER' AND customer_id IS NOT NULL)",
            name="ck_subject_matches",
        ),
        CheckConstraint("expires_at > created_at", name="ck_expiry_after_create"),
        CheckConstraint(
            "(revoked_at IS NULL) = (revoked_reason IS NULL)", name="ck_revoke_has_reason"
        ),
        CheckConstraint("length(refresh_token_hash) = 32", name="ck_refresh_token_hash"),
        CheckConstraint(
            "revoked_reason IS NULL OR revoked_reason IN "
            "('LOGOUT','ROTATED','REUSE_DETECTED','PASSWORD_CHANGED','ADMIN_REVOKED','EXPIRED')",
            name="ck_revoke_reason",
        ),
        Index("uq_session_token", "refresh_token_hash", unique=True),
        Index(
            "uq_session_parent",
            "parent_id",
            unique=True,
            postgresql_where=text("parent_id IS NOT NULL"),
            sqlite_where=text("parent_id IS NOT NULL"),
        ),
        Index("ix_session_family", "family_id"),
        Index("ix_session_subject", "subject_kind", "merchant_admin_id", "customer_id"),
        Index(
            "ix_session_live",
            "expires_at",
            postgresql_where=text("used_at IS NULL AND revoked_at IS NULL"),
            sqlite_where=text("used_at IS NULL AND revoked_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    subject_kind: Mapped[str] = mapped_column(String)
    merchant_admin_id: Mapped[str | None] = mapped_column(ForeignKey("merchant_admin.id"))
    customer_id: Mapped[str | None] = mapped_column(ForeignKey("customer.id"))
    family_id: Mapped[str] = mapped_column(String)
    refresh_token_hash: Mapped[bytes] = mapped_column(LargeBinary(32))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("auth_session.id"))
    user_agent_hash: Mapped[bytes | None] = mapped_column(LargeBinary(32))
    ip_hash: Mapped[bytes | None] = mapped_column(LargeBinary(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.current_timestamp()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String)


class LoginAttempt(Base):
    __tablename__ = "login_attempt"
    __table_args__ = (
        CheckConstraint("length(identifier_hash) = 32", name="ck_attempt_identifier_hash"),
        CheckConstraint("length(ip_hash) = 32", name="ck_attempt_ip_hash"),
        CheckConstraint("succeeded = (failure_code IS NULL)", name="ck_failure_code"),
        CheckConstraint(
            "NOT counts_for_lockout OR (NOT succeeded AND failure_code = 'BAD_CREDENTIALS')",
            name="ck_attempt_lockout_countable",
        ),
        Index("ix_attempt_identifier", "identifier_hash", "created_at"),
        Index("ix_attempt_ip", "ip_hash", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    identifier_hash: Mapped[bytes] = mapped_column(LargeBinary(32))
    ip_hash: Mapped[bytes] = mapped_column(LargeBinary(32))
    succeeded: Mapped[bool] = mapped_column(Boolean)
    failure_code: Mapped[str | None] = mapped_column(String)
    counts_for_lockout: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.current_timestamp()
    )


class LoginLockout(Base):
    __tablename__ = "login_lockout"
    __table_args__ = (
        CheckConstraint("length(identifier_hash) = 32", name="ck_lockout_identifier_hash"),
        CheckConstraint("attempt_count > 0", name="ck_lockout_attempt_count"),
    )

    identifier_hash: Mapped[bytes] = mapped_column(LargeBinary(32), primary_key=True)
    locked_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.current_timestamp()
    )


class AuthEvent(Base):
    __tablename__ = "auth_event"
    __table_args__ = (
        Index("ix_auth_event_subject", "subject_kind", "subject_id", "created_at"),
        Index("ix_auth_event_kind", "kind", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    subject_kind: Mapped[str | None] = mapped_column(String)
    subject_id: Mapped[str | None] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)
    session_id: Mapped[str | None] = mapped_column(ForeignKey("auth_session.id"))
    ip_hash: Mapped[bytes | None] = mapped_column(LargeBinary(32))
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, server_default=text("'{}'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.current_timestamp()
    )
