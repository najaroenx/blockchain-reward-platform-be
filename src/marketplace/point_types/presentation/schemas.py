from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from marketplace.point_types.domain.entities.point_type import (
    POINT_DECIMALS,
    SYMBOL_PATTERN,
    normalize_name,
)


class CreatePointType(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=60)]
    symbol: Annotated[str, StringConstraints(strict=True, pattern=SYMBOL_PATTERN)]
    decimals: Annotated[int, Field(strict=True, ge=POINT_DECIMALS, le=POINT_DECIMALS)]

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: object) -> object:
        if isinstance(value, str):
            return normalize_name(value)
        return value


class PointTypeRequested(CreatePointType):
    pointTypeId: str
    status: Literal["REQUESTED"]
    operationId: str


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail
