from sqlalchemy import BigInteger, Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from marketplace.infrastructure.database import Base
from marketplace.listings.domain.entities import Listing


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

    def to_domain(self) -> Listing:
        return Listing(
            listing_id=int(self.listing_id),
            seller=self.seller,
            type_id=int(self.type_id),
            amount=int(self.amount),
            price_per_unit=int(self.price_per_unit),
            payment_token=self.payment_token,
            active=self.active,
            listed_at=self.listed_at,
        )
