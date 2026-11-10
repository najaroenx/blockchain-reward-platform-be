from dataclasses import dataclass

from marketplace.listings.domain.entities import Listing


@dataclass(frozen=True)
class ListingPage:
    items: tuple[Listing, ...]
    total: int
    indexed_block: int
