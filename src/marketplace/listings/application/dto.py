from dataclasses import dataclass

from marketplace.listings.domain.entities.listing import Listing


@dataclass(frozen=True)
class ListingPage:
    items: tuple[Listing, ...]
    total: int
    indexed_block: int
