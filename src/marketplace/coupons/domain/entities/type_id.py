from dataclasses import dataclass


# Prevents values outside the contract's uint256 input range from reaching the adapter.
@dataclass(frozen=True)
class CouponTypeId:
    value: int

    def __post_init__(self) -> None:
        if type(self.value) is not int or not 0 <= self.value < 2**256:
            raise ValueError("Coupon type ID must be a uint256")
