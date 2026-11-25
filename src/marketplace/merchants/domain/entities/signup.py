from dataclasses import dataclass, field
from re import fullmatch


# Represents a normalized signup request with merchant and administrator details.
@dataclass(frozen=True)
class Signup:
    name: str
    email: str
    email_normalized: str
    display_name: str
    password: str = field(repr=False)


# Holds the public address and key reference used to provision a merchant wallet.
@dataclass(frozen=True)
class WalletReference:
    address: bytes
    key_ref: str

    def is_valid(self) -> bool:
        return (
            len(self.address) == 20
            and any(self.address)
            and fullmatch(r"(?:kms|mock-kms)://[A-Za-z0-9][A-Za-z0-9._:/-]{0,511}", self.key_ref)
            is not None
        )
