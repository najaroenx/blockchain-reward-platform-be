from sqlalchemy import func, select
from sqlalchemy.orm import Session

from marketplace.listings.domain.entities import Listing, Marketplace
from marketplace.listings.infrastructure.models import (
    ListingProjection,
    ProjectionCheckpoint,
)


class SqlListingReader:
    def __init__(self, session: Session) -> None:
        self.session = session

    def checkpoint(self, marketplace: Marketplace) -> int | None:
        state = self.session.get(ProjectionCheckpoint, (marketplace.chain_id, marketplace.address))
        return state.indexed_block if state is not None else None

    def active_listings(
        self, marketplace: Marketplace, *, limit: int, offset: int
    ) -> tuple[tuple[Listing, ...], int]:
        conditions = (
            ListingProjection.chain_id == marketplace.chain_id,
            ListingProjection.marketplace_address == marketplace.address,
            ListingProjection.active.is_(True),
        )
        total = self.session.scalar(
            select(func.count()).select_from(ListingProjection).where(*conditions)
        )
        rows = self.session.scalars(
            select(ListingProjection)
            .where(*conditions)
            .order_by(ListingProjection.listed_at.desc(), ListingProjection.listing_id)
            .limit(limit)
            .offset(offset)
        )
        return tuple(row.to_domain() for row in rows), total or 0

    def find(self, marketplace: Marketplace, listing_id: int) -> Listing | None:
        row = self.session.get(
            ListingProjection, (marketplace.chain_id, marketplace.address, str(listing_id))
        )
        return row.to_domain() if row is not None else None
