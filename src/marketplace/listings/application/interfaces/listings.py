from abc import ABC, abstractmethod

from marketplace.listings.domain.entities.listing import Listing, Marketplace


class ListingReader(ABC):
    @abstractmethod
    def checkpoint(self, marketplace: Marketplace) -> int | None: ...

    @abstractmethod
    def active_listings(
        self, marketplace: Marketplace, *, limit: int, offset: int
    ) -> tuple[tuple[Listing, ...], int]: ...

    @abstractmethod
    def find(self, marketplace: Marketplace, listing_id: int) -> Listing | None: ...
