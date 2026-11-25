from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from marketplace.coupons.domain.entities.coupon import CouponTypeDraft
from marketplace.infrastructure.database import Base


class CouponTypeModel(Base):
    __tablename__ = "coupon_types"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    start_date: Mapped[int] = mapped_column(BigInteger)
    expire_date: Mapped[int] = mapped_column(BigInteger)
    face_value: Mapped[int] = mapped_column(BigInteger)
    total_supply: Mapped[int] = mapped_column(BigInteger)
    # Always NULL until a later phase decides deployment/signing and a worker submits
    # createCouponType; see docs/architecture.md "Decisions still required".
    chain_type_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def to_domain(self) -> CouponTypeDraft:
        # SQLite's DateTime(timezone=True) doesn't persist tzinfo; everything is written
        # in UTC, so a naive value read back is always UTC.
        created_at = self.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        return CouponTypeDraft(
            id=self.id,
            name=self.name,
            start_date=self.start_date,
            expire_date=self.expire_date,
            face_value=self.face_value,
            total_supply=self.total_supply,
            chain_type_id=self.chain_type_id,
            created_at=created_at,
        )


class CouponTypeIdempotencyKey(Base):
    __tablename__ = "coupon_type_idempotency_keys"

    idempotency_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    coupon_type_id: Mapped[str] = mapped_column(String(36), ForeignKey("coupon_types.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
