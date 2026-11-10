import re
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field

from marketplace.listings.domain.entities import Listing


def address(value: str) -> str:
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", value):
        raise ValueError("must be a 20-byte hexadecimal address")
    return value.lower()


def uint256(value: str) -> str:
    if not re.fullmatch(r"0|[1-9][0-9]{0,77}", value) or int(value) >= 2**256:
        raise ValueError("must be a canonical uint256 decimal string")
    return value


Address = Annotated[str, AfterValidator(address)]
Uint256 = Annotated[str, AfterValidator(uint256)]


class ListingResponse(BaseModel):
    listing_id: Uint256
    seller: Address
    type_id: Uint256
    amount: Uint256
    price_per_unit: Uint256
    payment_token: Address
    active: bool
    listed_at: int

    @classmethod
    def from_domain(cls, listing: Listing) -> "ListingResponse":
        return cls(
            listing_id=str(listing.listing_id),
            seller=listing.seller,
            type_id=str(listing.type_id),
            amount=str(listing.amount),
            price_per_unit=str(listing.price_per_unit),
            payment_token=listing.payment_token,
            active=listing.active,
            listed_at=listing.listed_at,
        )


class PageResponse(BaseModel):
    items: list[ListingResponse]
    total: int
    limit: int
    offset: int
    indexed_block: int = Field(description="Projection checkpoint, not a live chain balance")


class DetailResponse(BaseModel):
    listing: ListingResponse
    indexed_block: int
