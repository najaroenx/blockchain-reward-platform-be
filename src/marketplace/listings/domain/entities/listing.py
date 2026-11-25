from dataclasses import dataclass


# Identifies a marketplace deployment by chain ID and contract address.
@dataclass(frozen=True)
class Marketplace:
    chain_id: int
    address: str


# Holds the values of an indexed listing and its active state at read time.
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
