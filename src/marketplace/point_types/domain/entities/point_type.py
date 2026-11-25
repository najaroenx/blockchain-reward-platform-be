from dataclasses import dataclass
from re import fullmatch
from unicodedata import normalize

POINT_DECIMALS = 18
SYMBOL_PATTERN = r"^[A-Z][A-Z0-9]{1,10}$"


def normalize_name(value: str) -> str:
    if "\x00" in value:
        raise ValueError("Name cannot contain a NUL character")
    return normalize("NFC", value).strip()


@dataclass(frozen=True)
class PointTypeDraft:
    name: str
    symbol: str
    decimals: int

    def __post_init__(self) -> None:
        name = normalize_name(self.name)
        if not 1 <= len(name) <= 60:
            raise ValueError("Name must contain 1 to 60 characters")
        if fullmatch(SYMBOL_PATTERN, self.symbol) is None:
            raise ValueError("Invalid point type symbol")
        if self.decimals != POINT_DECIMALS:
            raise ValueError("Point type decimals must equal 18")
        object.__setattr__(self, "name", name)
