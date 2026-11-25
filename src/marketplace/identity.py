from dataclasses import dataclass


# Carries the authenticated user and merchant IDs used to scope merchant requests.
@dataclass(frozen=True)
class MerchantIdentity:
    user_id: str
    merchant_id: str
