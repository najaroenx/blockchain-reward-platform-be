from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from marketplace.operation_status import OperationStatus


# Exposes an operation's current status and latest event in an HTTP response.
class OperationSummary(BaseModel):
    operationId: str
    kind: str
    status: OperationStatus
    eventSeq: int
    createdAt: datetime


# Exposes one operation history event with its sequence and timestamp.
class OperationEventView(BaseModel):
    seq: int
    fromStatus: OperationStatus | None
    toStatus: OperationStatus
    actor: Literal["SYSTEM", "MERCHANT_ADMIN", "CUSTOMER", "PLATFORM_ADMIN"]
    txHash: str | None
    createdAt: datetime


# Exposes the caller-visible state of a planned transaction step.
class OperationStepView(BaseModel):
    index: int
    name: str
    status: Literal["PENDING", "SUBMITTED", "CONFIRMED", "FINALIZED", "CHAIN_REVERTED", "ORPHANED"]
    txHash: str | None


# Returns the operation detail with its history, transaction steps, and finalization status.
class OperationView(OperationSummary):
    finalizedAt: datetime | None
    terminal: bool
    errorNote: str | None
    steps: list[OperationStepView]
    events: list[OperationEventView]


# Returns a page of operation summaries and its continuation cursor.
class OperationPage(BaseModel):
    items: list[OperationSummary]
    nextCursor: str | None
