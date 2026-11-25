from time import time

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from marketplace.listings.application.interfaces.listings import ListingReader
from marketplace.listings.domain.entities.listing import Listing, Marketplace
from marketplace.listings.infrastructure.models import (
    ListingProjection,
    ProjectionCheckpoint,
)


def _active_at(timestamp: int) -> ColumnElement[bool]:
    return ListingProjection.active.is_(True) & (ListingProjection.coupon_expires_at >= timestamp)


# Implements catalog reads in SQL and evaluates listing activity and coupon expiry at query time.
class SqlListingReader(ListingReader):
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
            _active_at(int(time())),
        )
        rows = (
            self.session.execute(
                select(ListingProjection, func.count().over())
                .where(*conditions)
                .order_by(ListingProjection.listed_at.desc(), ListingProjection.listing_id)
                .limit(limit)
                .offset(offset)
            )
            .tuples()
            .all()
        )
        if rows:
            return tuple(row.to_domain(active=True) for row, _ in rows), rows[0][1]
        if offset == 0:
            return (), 0
        total = self.session.scalar(
            select(func.count()).select_from(ListingProjection).where(*conditions)
        )
        return (), total or 0

    def find(self, marketplace: Marketplace, listing_id: int) -> Listing | None:
        result = (
            self.session.execute(
                select(ListingProjection, _active_at(int(time()))).where(
                    ListingProjection.chain_id == marketplace.chain_id,
                    ListingProjection.marketplace_address == marketplace.address,
                    ListingProjection.listing_id == str(listing_id),
                )
            )
            .tuples()
            .one_or_none()
        )
        if result is None:
            return None
        row, active = result
        return row.to_domain(active=bool(active))
