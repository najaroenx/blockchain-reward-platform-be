from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Numeric, String
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator, TypeEngine

from marketplace.infrastructure.database import Base
from marketplace.listings.domain.entities.listing import Listing


class CouponExpiryTimestamp(TypeDecorator[int]):
    """Lossless uint256 Unix seconds with fixed-width text for SQLite ordering."""

    impl = String(78)
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Decimal] | TypeEngine[str]:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Numeric(78, 0))
        return dialect.type_descriptor(String(78))

    def process_bind_param(self, value: int | None, dialect: Dialect) -> Decimal | str | None:
        if value is None:
            return None
        if not 0 <= value < 2**256:
            raise ValueError("Coupon expiry must fit uint256")
        if dialect.name == "postgresql":
            return Decimal(value)
        return f"{value:078d}"

    def process_result_value(self, value: Decimal | str | None, dialect: Dialect) -> int | None:
        return None if value is None else int(value)


class ProjectionCheckpoint(Base):
    __tablename__ = "marketplace_projection_checkpoint"

    chain_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    marketplace_address: Mapped[str] = mapped_column(String(42), primary_key=True)
    indexed_block: Mapped[int] = mapped_column(BigInteger)


class ListingProjection(Base):
    __tablename__ = "marketplace_listing_projection"

    chain_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    marketplace_address: Mapped[str] = mapped_column(String(42), primary_key=True)
    # Decimal strings retain all 256 bits on PostgreSQL and local SQLite.
    listing_id: Mapped[str] = mapped_column(String(78), primary_key=True)
    seller: Mapped[str] = mapped_column(String(42))
    type_id: Mapped[str] = mapped_column(String(78))
    amount: Mapped[str] = mapped_column(String(78))
    price_per_unit: Mapped[str] = mapped_column(String(78))
    payment_token: Mapped[str] = mapped_column(String(42))
    active: Mapped[bool] = mapped_column(Boolean)
    listed_at: Mapped[int] = mapped_column(BigInteger)
    # Legacy rows have no expiry until the projection is hydrated from the Coupon contract.
    coupon_expires_at: Mapped[int | None] = mapped_column(CouponExpiryTimestamp(), deferred=True)

    def to_domain(self, *, active: bool) -> Listing:
        return Listing(
            listing_id=int(self.listing_id),
            seller=self.seller,
            type_id=int(self.type_id),
            amount=int(self.amount),
            price_per_unit=int(self.price_per_unit),
            payment_token=self.payment_token,
            active=active,
            listed_at=self.listed_at,
        )
