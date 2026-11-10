from marketplace.listings.application.dto import ListingPage
from marketplace.listings.application.exceptions import (
    ListingNotFound,
    ProjectionUnavailable,
)
from marketplace.listings.application.ports import ListingReader
from marketplace.listings.domain.entities import Listing, Marketplace


class Catalog:
    def __init__(self, listings: ListingReader) -> None:
        self.listings = listings

    def active(self, marketplace: Marketplace, *, limit: int, offset: int) -> ListingPage:
        block = self._checkpoint(marketplace)
        listings, total = self.listings.active_listings(marketplace, limit=limit, offset=offset)
        return ListingPage(items=listings, total=total, indexed_block=block)

    def get(self, marketplace: Marketplace, listing_id: int) -> tuple[Listing, int]:
        block = self._checkpoint(marketplace)
        listing = self.listings.find(marketplace, listing_id)
        if listing is None:
            raise ListingNotFound
        return listing, block

    def _checkpoint(self, marketplace: Marketplace) -> int:
        block = self.listings.checkpoint(marketplace)
        if block is None:
            raise ProjectionUnavailable
        return block
