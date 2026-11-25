from typing import Literal

OperationStatus = Literal[
    "PENDING", "SUBMITTED", "CONFIRMED", "FINALIZED", "CHAIN_REVERTED", "ORPHANED", "MANUAL_REVIEW"
]
