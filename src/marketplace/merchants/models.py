from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from marketplace.infrastructure.database import Base


class Merchant(Base):
    __tablename__ = "merchant"
    __table_args__ = (
        CheckConstraint("length(trim(name)) BETWEEN 1 AND 200", name="ck_merchant_name"),
        CheckConstraint(
            "status IN ('PENDING','ACTIVE','SUSPENDED','CLOSED')", name="ck_merchant_status"
        ),
        CheckConstraint(
            "status <> 'ACTIVE' OR activated_at IS NOT NULL", name="ck_merchant_active"
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, server_default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MerchantAdmin(Base):
    __tablename__ = "merchant_admin"
    __table_args__ = (
        CheckConstraint(
            "email_normalized = lower(trim(email_normalized))", name="ck_admin_email_normalized"
        ),
        CheckConstraint("password_hash LIKE '$argon2id$%'", name="ck_admin_password_argon2id"),
        Index("uq_admin_email", "email_normalized", unique=True),
        Index("ix_admin_merchant", "merchant_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchant.id"))
    email: Mapped[str] = mapped_column(String(254))
    email_normalized: Mapped[str] = mapped_column(String(254))
    password_hash: Mapped[str] = mapped_column(String)
    display_name: Mapped[str] = mapped_column(String)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Wallet(Base):
    __tablename__ = "wallet"
    __table_args__ = (
        CheckConstraint("length(address) = 20", name="ck_wallet_address_length"),
        CheckConstraint(
            "key_ref LIKE 'kms://_%' OR key_ref LIKE 'mock-kms://_%'",
            name="ck_wallet_key_reference",
        ),
        CheckConstraint("owner_kind = 'MERCHANT'", name="ck_wallet_owner_kind"),
        Index("uq_wallet_owner", "owner_kind", "owner_id", unique=True),
        Index("uq_wallet_address", "address", unique=True),
        Index("uq_wallet_key_ref", "key_ref", unique=True),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    address: Mapped[bytes] = mapped_column(LargeBinary(20))
    key_ref: Mapped[str] = mapped_column(Text)
    owner_kind: Mapped[str] = mapped_column(String(16))
    owner_id: Mapped[str] = mapped_column(ForeignKey("merchant.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
