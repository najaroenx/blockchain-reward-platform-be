from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CouponType:
    id: str
    name: str
    start_date: int
    expire_date: int
    face_value: int
    total_supply: int
    chain_type_id: int | None
    created_at: datetime
