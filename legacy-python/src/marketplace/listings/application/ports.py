from typing import Protocol

from marketplace.listings.domain.entities import Listing, Marketplace


class ListingReader(Protocol):
    def checkpoint(self, marketplace: Marketplace) -> int | None: ...

    def active_listings(
        self, marketplace: Marketplace, *, limit: int, offset: int
    ) -> tuple[tuple[Listing, ...], int]: ...

    def find(self, marketplace: Marketplace, listing_id: int) -> Listing | None: ...
