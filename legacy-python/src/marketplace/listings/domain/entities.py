from dataclasses import dataclass


@dataclass(frozen=True)
class Marketplace:
    chain_id: int
    address: str


@dataclass(frozen=True)
class Listing:
    listing_id: int
    seller: str
    type_id: int
    amount: int
    price_per_unit: int
    payment_token: str
    active: bool
    listed_at: int
