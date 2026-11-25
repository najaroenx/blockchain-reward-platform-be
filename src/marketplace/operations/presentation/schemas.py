from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from marketplace.operation_status import OperationStatus


class OperationSummary(BaseModel):
    operationId: str
    kind: str
    status: OperationStatus
    eventSeq: int
    createdAt: datetime


class OperationEventView(BaseModel):
    seq: int
    fromStatus: OperationStatus | None
    toStatus: OperationStatus
    actor: Literal["SYSTEM", "MERCHANT_ADMIN", "CUSTOMER", "PLATFORM_ADMIN"]
    txHash: str | None
    createdAt: datetime


class OperationStepView(BaseModel):
    index: int
    name: str
    status: Literal["PENDING", "SUBMITTED", "CONFIRMED", "FINALIZED", "CHAIN_REVERTED", "ORPHANED"]
    txHash: str | None


class OperationView(OperationSummary):
    finalizedAt: datetime | None
    terminal: bool
    errorNote: str | None
    steps: list[OperationStepView]
    events: list[OperationEventView]


class OperationPage(BaseModel):
    items: list[OperationSummary]
    nextCursor: str | None
