"""Adapter contracts for coupon-type drafts and redeem (#224).

Redeem: SQL admission is implemented, wallet/deployment are pending. Expected failures
use RedeemFailure with a public code. Unknown exceptions are sanitized at the HTTP
boundary. Decode RPC failures against the verified ABI and establish their origin before
classifying them; never pass raw RPC/SQL details.
"""

from abc import ABC, abstractmethod

from marketplace.coupons.application.dto import (
    AuthenticatedWallet,
    CouponDeployment,
    CreateCouponType,
    RedeemCouponPayload,
    RedeemCouponResult,
)
from marketplace.coupons.domain.entities.coupon import CouponTypeDraft


# Defines the repository port for storing drafts, replaying requests, and reading saved drafts.
class CouponTypeRepository(ABC):
    @abstractmethod
    def create_or_replay(
        self, data: CreateCouponType, *, idempotency_key: str, request_fingerprint: str
    ) -> CouponTypeDraft:
        """Inserts a coupon type, or replays the result of an earlier identical request.

        Raises DuplicateCouponTypeName when `data.name` belongs to a different coupon type,
        and IdempotencyKeyConflict when `idempotency_key` was already used with a different
        request body.
        """
        ...

    @abstractmethod
    def find(self, coupon_type_id: str) -> CouponTypeDraft | None: ...

    @abstractmethod
    def list(self, *, limit: int, offset: int) -> tuple[tuple[CouponTypeDraft, ...], int]: ...


# Defines how authentication obtains a trusted wallet for the verified user.
class WalletResolver(ABC):
    @abstractmethod
    def resolve(self, user_id: str) -> AuthenticatedWallet | None:
        """Resolve an authorized wallet for an already authenticated application user."""
        ...


# Defines how a use case finds the verified chain and contract for a coupon type.
class CouponDeploymentResolver(ABC):
    @abstractmethod
    def resolve(self, type_id: int) -> CouponDeployment:
        """Select an unambiguous, verified deployment from trusted configuration."""
        ...


# Defines the atomic write of the redeem operation, outbox item, and saved acknowledgement.
class RedeemAdmissionStore(ABC):
    @abstractmethod
    def admit(
        self, *, user_id: str, idempotency_key: str, payload: RedeemCouponPayload
    ) -> RedeemCouponResult:
        """Commit scoped idempotency, operation, and outbox in one DB transaction.

        Replay equivalent requests using the original operation. Distinguish
        conflicting and in-progress keys according to #147. Fingerprinting must
        include the resolved wallet, deployment, type, and amount. Persist no token.
        """
        ...
