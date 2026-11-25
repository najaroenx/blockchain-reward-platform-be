from dataclasses import dataclass


@dataclass(frozen=True)
class MerchantIdentity:
    user_id: str
    merchant_id: str
