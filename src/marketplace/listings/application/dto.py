from dataclasses import dataclass

from marketplace.listings.domain.entities.listing import Listing


# Carries a page of listings, the total matching count, and the indexed block represented by the
# page.
@dataclass(frozen=True)
class ListingPage:
    items: tuple[Listing, ...]
    total: int
    indexed_block: int
