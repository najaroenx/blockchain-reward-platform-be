from enum import StrEnum


class OperationKind(StrEnum):
    """Operation kinds shared by the Operations API and Worker."""

    # Set up a merchant for on-chain operations.
    MERCHANT_PROVISION = "MERCHANT_PROVISION"
    # Create a loyalty point type for a merchant.
    POINT_TYPE_CREATE = "POINT_TYPE_CREATE"
    # Transfer loyalty points between wallets.
    POINT_TRANSFER = "POINT_TRANSFER"
    # Define a new coupon type.
    COUPON_TYPE_CREATE = "COUPON_TYPE_CREATE"
    # Issue coupons of an existing type.
    COUPON_ISSUE = "COUPON_ISSUE"
    # Redeem a coupon held by a wallet.
    COUPON_REDEEM = "COUPON_REDEEM"
    # List an item for sale on the marketplace.
    LISTING_CREATE = "LISTING_CREATE"
    # Remove a listing from the marketplace.
    LISTING_DELETE = "LISTING_DELETE"
    # Purchase an item from a marketplace listing.
    LISTING_BUY = "LISTING_BUY"
    # Mint THB tokens.
    THB_MINT = "THB_MINT"
