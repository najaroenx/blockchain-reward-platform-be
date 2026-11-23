from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

# merchant/merchant_admin are owned by auth (FEAT-03 foundation); re-exported here so
# this feature's own imports don't need to know that.
from marketplace.auth.infrastructure.models import Merchant as Merchant
from marketplace.auth.infrastructure.models import MerchantAdmin as MerchantAdmin
from marketplace.infrastructure.database import Base


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
