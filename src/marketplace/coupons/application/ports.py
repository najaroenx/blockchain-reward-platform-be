"""Adapter contracts for #224; production adapters are not implemented yet.

Expected failures use RedeemFailure with a public code. Unknown exceptions are
sanitized at the HTTP boundary. Decode RPC failures against the verified ABI and
establish their origin before classifying them; never pass raw RPC/SQL details.
"""

from typing import Protocol

from marketplace.coupons.application.dto import (
    AuthenticatedWallet,
    CouponDeployment,
    RedeemCouponPayload,
    RedeemCouponResult,
)


class WalletResolver(Protocol):
    def resolve(self, user_id: str) -> AuthenticatedWallet | None:
        """Resolve an authorized wallet for an already authenticated application user."""
        ...


class CouponDeploymentResolver(Protocol):
    def resolve(self, type_id: int) -> CouponDeployment:
        """Select an unambiguous, verified deployment from trusted configuration."""
        ...


class RedeemAdmissionStore(Protocol):
    def admit(
        self, *, user_id: str, idempotency_key: str, payload: RedeemCouponPayload
    ) -> RedeemCouponResult:
        """Commit scoped idempotency, operation, and outbox in one DB transaction.

        Replay equivalent requests using the original operation. Distinguish
        conflicting and in-progress keys according to #147. Fingerprinting must
        include the resolved wallet, deployment, type, and amount. Persist no token.
        """
        ...
