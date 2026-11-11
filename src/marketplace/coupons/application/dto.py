"""Draft redeem messages; authentication and admission are not implemented yet."""

from dataclasses import dataclass


@dataclass(frozen=True)
class AuthenticatedWallet:
    # Represents an authenticated wallet, typically verified by an authentication adapter.
    # Contains identifiers such as user ID and wallet address.
    pass


@dataclass(frozen=True)
class RedeemCouponCommand:
    # Command to redeem a coupon, containing the actor's authenticated wallet,
    # the type ID of the coupon to redeem, and an idempotency key to ensure safe retries.
    pass


@dataclass(frozen=True)
class CouponDeployment:
    # Represents a deployed coupon contract selected from verified trusted configuration.
    # Contains identifiers such as the blockchain chain ID and the coupon contract address.
    pass


@dataclass(frozen=True)
class RedeemCouponPayload:
    # Payload containing the deployment details, wallet address,
    # coupon type ID, and amount to redeem (fixed at 1).
    pass


@dataclass(frozen=True)
class RedeemCouponResult:
    # Admission result containing the operation ID, coupon type ID,
    # PENDING status, and amount (fixed at 1).
    pass
