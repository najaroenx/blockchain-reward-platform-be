"""ORM models for the five tables this feature claims out of `infrastructure/legacy_schema.py`.

Schema unchanged from migrations/versions/0002_legacy_schema_port.py (plus the `ck_one_subject`
and `uq_session_parent` restored there in this round) — only which Python object declares it.
`merchant_admin.merchant_id` and `auth_session.customer_id` stay plain FK columns with no
`relationship()`: `merchant` and `customer` remain unclaimed placeholders (FEAT-01/FEAT-02 aren't
built), the same way `idempotency.py` references `operation.id` without mapping `operation`.
Anything using these models must import `marketplace.infrastructure.legacy_schema` first (as
`migrations/env.py` already does) or SQLAlchemy cannot resolve the FKs at flush time.
"""

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from marketplace.infrastructure.database import Base

HASH = sa.String(66)
TS = sa.DateTime(timezone=True)


class MerchantAdmin(Base):
    __tablename__ = "merchant_admin"
    __table_args__ = (
        sa.CheckConstraint(
            "email_normalized = lower(trim(email_normalized))", name="ck_admin_email_normalized"
        ),
        sa.CheckConstraint("password_hash LIKE '$argon2id$%'", name="ck_admin_password_hash"),
    )

    id: Mapped[str] = mapped_column(sa.Text(), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("merchant.id"))
    email: Mapped[str] = mapped_column(sa.Text())
    email_normalized: Mapped[str] = mapped_column(sa.Text(), unique=True)
    password_hash: Mapped[str] = mapped_column(sa.Text())
    display_name: Mapped[str] = mapped_column(sa.Text())
    disabled_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=sa.func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(TS)


class AuthSession(Base):
    __tablename__ = "auth_session"
    __table_args__ = (
        sa.CheckConstraint(
            "subject_kind IN ('MERCHANT_ADMIN', 'CUSTOMER', 'PLATFORM_ADMIN')",
            name="ck_auth_session_subject_kind_enum",
        ),
        sa.CheckConstraint("length(refresh_token_hash) = 66", name="ck_auth_session_token_len"),
        sa.CheckConstraint(
            "(subject_kind = 'MERCHANT_ADMIN' AND merchant_admin_id IS NOT NULL)"
            " OR (subject_kind = 'CUSTOMER' AND customer_id IS NOT NULL)",
            name="ck_subject_matches",
        ),
        sa.CheckConstraint(
            "(CASE WHEN merchant_admin_id IS NOT NULL THEN 1 ELSE 0 END)"
            " + (CASE WHEN customer_id IS NOT NULL THEN 1 ELSE 0 END) = 1",
            name="ck_one_subject",
        ),
        sa.CheckConstraint("expires_at > created_at", name="ck_expiry_after_create"),
        sa.CheckConstraint(
            "(revoked_at IS NULL) = (revoked_reason IS NULL)", name="ck_revoke_has_reason"
        ),
        sa.Index(
            "uq_session_parent",
            "parent_id",
            unique=True,
            sqlite_where=sa.text("parent_id IS NOT NULL"),
            postgresql_where=sa.text("parent_id IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(sa.Text(), primary_key=True)
    subject_kind: Mapped[str] = mapped_column(sa.String(40))
    merchant_admin_id: Mapped[str | None] = mapped_column(
        sa.Text(), sa.ForeignKey("merchant_admin.id")
    )
    customer_id: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("customer.id"))
    family_id: Mapped[str] = mapped_column(sa.Text())
    refresh_token_hash: Mapped[str] = mapped_column(HASH, unique=True)
    parent_id: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("auth_session.id"))
    user_agent_hash: Mapped[bytes | None] = mapped_column(sa.LargeBinary())
    ip_hash: Mapped[bytes | None] = mapped_column(sa.LargeBinary())
    created_at: Mapped[datetime] = mapped_column(TS, server_default=sa.func.now())
    expires_at: Mapped[datetime] = mapped_column(TS)
    used_at: Mapped[datetime | None] = mapped_column(TS)
    revoked_at: Mapped[datetime | None] = mapped_column(TS)
    revoked_reason: Mapped[str | None] = mapped_column(sa.String(40))


class AuthEvent(Base):
    __tablename__ = "auth_event"
    __table_args__ = (
        sa.CheckConstraint(
            "subject_kind IS NULL"
            " OR subject_kind IN ('MERCHANT_ADMIN', 'CUSTOMER', 'PLATFORM_ADMIN')",
            name="ck_auth_event_subject_kind_enum",
        ),
    )

    id: Mapped[str] = mapped_column(sa.Text(), primary_key=True)
    subject_kind: Mapped[str | None] = mapped_column(sa.String(40))
    subject_id: Mapped[str | None] = mapped_column(sa.Text())
    kind: Mapped[str] = mapped_column(sa.Text())
    session_id: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("auth_session.id"))
    ip_hash: Mapped[bytes | None] = mapped_column(sa.LargeBinary())
    event_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", sa.JSON())
    created_at: Mapped[datetime] = mapped_column(TS, server_default=sa.func.now())


class LoginAttempt(Base):
    __tablename__ = "login_attempt"
    __table_args__ = (
        sa.CheckConstraint("length(identifier_hash) = 66", name="ck_login_attempt_id_hash_len"),
        sa.CheckConstraint("length(ip_hash) = 66", name="ck_login_attempt_ip_hash_len"),
        sa.CheckConstraint("succeeded = (failure_code IS NULL)", name="ck_failure_code"),
    )

    id: Mapped[str] = mapped_column(sa.Text(), primary_key=True)
    identifier_hash: Mapped[str] = mapped_column(HASH)
    ip_hash: Mapped[str] = mapped_column(HASH)
    succeeded: Mapped[bool] = mapped_column(sa.Boolean())
    failure_code: Mapped[str | None] = mapped_column(sa.Text())
    created_at: Mapped[datetime] = mapped_column(TS, server_default=sa.func.now())


class LoginLockout(Base):
    __tablename__ = "login_lockout"
    __table_args__ = (
        sa.CheckConstraint("length(identifier_hash) = 66", name="ck_login_lockout_hash_len"),
        sa.CheckConstraint("attempt_count > 0", name="ck_login_lockout_count_pos"),
    )

    identifier_hash: Mapped[str] = mapped_column(HASH, primary_key=True)
    locked_until: Mapped[datetime] = mapped_column(TS)
    attempt_count: Mapped[int] = mapped_column(sa.Integer())
    updated_at: Mapped[datetime] = mapped_column(TS, server_default=sa.func.now())
