import json
from base64 import b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from re import fullmatch
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response, Security
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import SQLAlchemyError

from marketplace.infrastructure.operation_history import OperationStatus
from marketplace.infrastructure.operation_models import Operation, OperationEvent, OperationStep

OPERATION_ID = r"op_[A-Za-z0-9]{6,}"
RETRY_AFTER = {
    "PENDING": "2",
    "SUBMITTED": "5",
    "CONFIRMED": "15",
    "ORPHANED": "10",
    "MANUAL_REVIEW": "300",
}
TERMINAL = {"FINALIZED", "CHAIN_REVERTED"}
OperationKind = Literal[
    "MERCHANT_PROVISION",
    "POINT_TYPE_CREATE",
    "POINT_TRANSFER",
    "COUPON_TYPE_CREATE",
    "COUPON_ISSUE",
    "COUPON_REDEEM",
    "LISTING_CREATE",
    "LISTING_DELETE",
    "LISTING_BUY",
    "THB_MINT",
]


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
    status: Literal["SUBMITTED", "CONFIRMED", "FINALIZED", "CHAIN_REVERTED", "ORPHANED"]
    txHash: str


class OperationView(OperationSummary):
    finalizedAt: datetime | None
    terminal: bool
    errorNote: str | None
    steps: list[OperationStepView]
    events: list[OperationEventView]


class OperationPage(BaseModel):
    items: list[OperationSummary]
    nextCursor: str | None


class OperationReadFailure(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


class OperationRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError:
                return error_response(400, "VALIDATION_FAILED", "Invalid operation filters.")
            except SQLAlchemyError:
                return error_response(
                    503, "OPERATIONS_UNAVAILABLE", "Operation status is unavailable."
                )
            except OperationReadFailure as error:
                return error_response(error.status, error.code, error.message)

        return handle


def error_response(status: int, code: str, message: str) -> JSONResponse:
    headers = {"Cache-Control": "private, no-cache"}
    if status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse(
        {"error": {"code": code, "message": message}}, status_code=status, headers=headers
    )


def utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


def encode_cursor(summary: OperationSummary) -> str:
    value = json.dumps([summary.createdAt.isoformat(), summary.operationId], separators=(",", ":"))
    return urlsafe_b64encode(value.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        if not fullmatch(r"[A-Za-z0-9_-]{1,256}", cursor):
            raise ValueError
        decoded = json.loads(b64decode(cursor + "=" * (-len(cursor) % 4), b"-_", validate=True))
        if (
            not isinstance(decoded, list)
            or len(decoded) != 2
            or not all(isinstance(value, str) for value in decoded)
        ):
            raise ValueError
        timestamp, operation_id = decoded
        created_at = datetime.fromisoformat(timestamp)
        if created_at.tzinfo is None or not fullmatch(OPERATION_ID, operation_id):
            raise ValueError
        return utc(created_at), operation_id
    except (ValueError, Base64Error, UnicodeError, OverflowError):
        raise OperationReadFailure(400, "INVALID_CURSOR", "Invalid operation cursor.") from None


def matches_etag(header: str | None, etag: str) -> bool:
    if header is None:
        return False
    return any(part.strip().removeprefix("W/") in {etag, "*"} for part in header.split(","))


def create_router(authenticate: Callable[[str], str | None] | None) -> APIRouter:
    router = APIRouter(prefix="/v1/operations", tags=["operations"], route_class=OperationRoute)
    bearer = HTTPBearer(auto_error=False)

    def owner(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer)],
    ) -> str:
        user_id = (
            authenticate(credentials.credentials)
            if credentials is not None and authenticate is not None
            else None
        )
        if user_id is None:
            raise OperationReadFailure(401, "UNAUTHENTICATED", "A valid access token is required.")
        return user_id

    @router.get(
        "",
        operation_id="listOperations",
        response_model=OperationPage,
        responses={
            400: {"description": "Invalid filters or cursor."},
            401: {"description": "Access token is missing or invalid."},
            503: {"description": "Operation status is unavailable."},
            "4XX": {"description": "Client error."},
        },
    )
    def list_operations(
        request: Request,
        response: Response,
        user_id: Annotated[str, Depends(owner)],
        status: OperationStatus | None = None,
        kind: OperationKind | None = None,
        cursor: Annotated[str | None, Query(max_length=256)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> OperationPage:
        query = (
            select(Operation.id, Operation.kind, Operation.created_at, OperationEvent)
            .join(
                OperationEvent,
                and_(
                    OperationEvent.operation_id == Operation.id,
                    OperationEvent.seq == Operation.event_seq,
                ),
            )
            .where(Operation.actor_user_id == user_id)
        )
        if status is not None:
            query = query.where(OperationEvent.to_status == status)
        if kind is not None:
            query = query.where(Operation.kind == kind)
        if cursor is not None:
            created_at, operation_id = decode_cursor(cursor)
            query = query.where(
                or_(
                    Operation.created_at < created_at,
                    and_(Operation.created_at == created_at, Operation.id < operation_id),
                )
            )
        with request.app.state.database.sessions() as session:
            rows = (
                session.execute(
                    query.order_by(Operation.created_at.desc(), Operation.id.desc()).limit(
                        limit + 1
                    )
                )
                .tuples()
                .all()
            )
        items = [
            OperationSummary(
                operationId=op_id,
                kind=op_kind,
                status=event.to_status,
                eventSeq=event.seq,
                createdAt=utc(op_created),
            )
            for op_id, op_kind, op_created, event in rows[:limit]
        ]
        response.headers["Cache-Control"] = "private, no-cache"
        return OperationPage(
            items=items,
            nextCursor=encode_cursor(items[-1]) if len(rows) > limit else None,
        )

    @router.get(
        "/{operationId}",
        operation_id="getOperation",
        response_model=OperationView,
        responses={
            200: {
                "headers": {
                    "ETag": {"schema": {"type": "string"}},
                    "Retry-After": {"schema": {"type": "integer"}},
                }
            },
            304: {"description": "The event sequence has not changed."},
            401: {"description": "Access token is missing or invalid."},
            404: {"description": "Operation was not found."},
            503: {"description": "Operation status is unavailable."},
            "4XX": {"description": "Client error."},
        },
    )
    def get_operation(
        request: Request,
        response: Response,
        operationId: str,
        user_id: Annotated[str, Depends(owner)],
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> OperationView | Response:
        missing = OperationReadFailure(404, "OPERATION_NOT_FOUND", "Operation was not found.")
        if not fullmatch(OPERATION_ID, operationId):
            raise missing
        with request.app.state.database.sessions() as session:
            rows = (
                session.execute(
                    select(
                        Operation.kind,
                        Operation.created_at,
                        Operation.event_seq,
                        OperationEvent,
                        OperationStep.status,
                        OperationStep.tx_hash,
                    )
                    .join(OperationEvent, OperationEvent.operation_id == Operation.id)
                    .outerjoin(OperationStep, OperationStep.operation_id == Operation.id)
                    .where(Operation.id == operationId, Operation.actor_user_id == user_id)
                    .order_by(OperationEvent.seq)
                )
                .tuples()
                .all()
            )
        if not rows:
            raise missing

        kind, created_at, seq, latest, step_status, step_hash = rows[-1]
        if seq != latest.seq:
            raise OperationReadFailure(
                503, "OPERATIONS_UNAVAILABLE", "Operation status is unavailable."
            )
        etag = f'"{operationId}-{seq}"'
        headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
        retry = RETRY_AFTER.get(latest.to_status)
        if retry is not None:
            headers["Retry-After"] = retry
        if matches_etag(if_none_match, etag):
            return Response(status_code=304, headers=headers)

        steps = []
        if step_status not in {None, "PREPARED"}:
            steps.append(
                OperationStepView(
                    index=1,
                    name="redeemFrom" if kind == "COUPON_REDEEM" else kind,
                    status=step_status,
                    txHash=step_hash,
                )
            )
        events = [
            OperationEventView(
                seq=event.seq,
                fromStatus=event.from_status,
                toStatus=event.to_status,
                actor=event.actor,
                txHash=event.tx_hash,
                createdAt=utc(event.created_at),
            )
            for _, _, _, event, _, _ in rows
        ]
        response.headers.update(headers)
        return OperationView(
            operationId=operationId,
            kind=kind,
            status=latest.to_status,
            eventSeq=seq,
            createdAt=utc(created_at),
            finalizedAt=(
                utc(latest.created_at)
                if latest.to_status == "FINALIZED" and not latest.is_snapshot
                else None
            ),
            terminal=latest.to_status in TERMINAL,
            errorNote=latest.error_code,
            steps=steps,
            events=events,
        )

    return router
