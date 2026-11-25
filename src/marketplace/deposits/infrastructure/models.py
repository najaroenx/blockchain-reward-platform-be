from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from marketplace.deposits.domain.entities.deposit import MAX_SATANG, MIN_SATANG
from marketplace.infrastructure.database import Base


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
