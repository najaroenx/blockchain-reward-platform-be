import json
from base64 import b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from collections.abc import Callable, Coroutine
from datetime import datetime
from re import fullmatch
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response, Security
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from marketplace.operation_status import OperationStatus
from marketplace.operations.application.exceptions import OperationNotFound, OperationsUnavailable
from marketplace.operations.application.use_cases.read import ReadOperations
from marketplace.operations.domain.entities.kinds import OperationKind
from marketplace.operations.domain.entities.operation import OperationSummaryRecord, utc
from marketplace.operations.presentation.schemas import (
    OperationEventView,
    OperationPage,
    OperationStepView,
    OperationSummary,
    OperationView,
)

OPERATION_ID = r"op_[A-Za-z0-9]{6,}"
RETRY_AFTER = {
    "PENDING": "2",
    "SUBMITTED": "5",
    "CONFIRMED": "15",
    "ORPHANED": "10",
    "MANUAL_REVIEW": "300",
}


# Carries the HTTP status and public error fields for a rejected operation read.
class OperationReadFailure(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


# Maps authenticated request data into operation reads and translates expected errors to HTTP.
class OperationRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError:
                return error_response(400, "VALIDATION_FAILED", "Invalid operation filters.")
            except OperationNotFound:
                return error_response(404, "OPERATION_NOT_FOUND", "Operation was not found.")
            except OperationsUnavailable:
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


def as_summary(record: OperationSummaryRecord) -> OperationSummary:
    return OperationSummary(
        operationId=record.id,
        kind=record.kind,
        status=record.status,
        eventSeq=record.event_seq,
        createdAt=record.created_at,
    )


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


def create_router(
    authenticate: Callable[[str], str | None] | None,
    provide_operations: Callable[[Request], ReadOperations],
) -> APIRouter:
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
        response: Response,
        user_id: Annotated[str, Depends(owner)],
        operations: Annotated[ReadOperations, Depends(provide_operations)],
        status: OperationStatus | None = None,
        kind: OperationKind | None = None,
        cursor: Annotated[str | None, Query(max_length=256)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> OperationPage:
        items, has_more = operations.page(
            user_id,
            status=status,
            kind=kind,
            before=decode_cursor(cursor) if cursor is not None else None,
            limit=limit,
        )
        views = [as_summary(item) for item in items]
        response.headers["Cache-Control"] = "private, no-cache"
        return OperationPage(
            items=views,
            nextCursor=encode_cursor(views[-1]) if has_more else None,
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
        response: Response,
        operationId: str,
        user_id: Annotated[str, Depends(owner)],
        operations: Annotated[ReadOperations, Depends(provide_operations)],
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> OperationView | Response:
        if not fullmatch(OPERATION_ID, operationId):
            raise OperationNotFound
        snapshot = operations.detail(user_id, operationId)
        latest = snapshot.history[-1]
        seq = snapshot.summary.event_seq
        kind = snapshot.summary.kind
        etag = f'"{operationId}-{seq}"'
        headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
        retry = RETRY_AFTER.get(latest.to_status)
        if retry is not None:
            headers["Retry-After"] = retry
        if matches_etag(if_none_match, etag):
            return Response(status_code=304, headers=headers)

        steps = [
            OperationStepView(index=index, name=name, status="PENDING", txHash=None)
            for index, name in snapshot.planned
        ]
        step_status = snapshot.step_status
        if step_status is not None and step_status != "PREPARED":
            steps.append(
                OperationStepView(
                    index=1,
                    name="redeemFrom" if kind == "COUPON_REDEEM" else kind,
                    status=step_status,
                    txHash=snapshot.step_hash,
                )
            )
        events = [
            OperationEventView(
                seq=event.seq,
                fromStatus=event.from_status,
                toStatus=event.to_status,
                actor=event.actor,
                txHash=event.tx_hash,
                createdAt=event.created_at,
            )
            for event in snapshot.history
        ]
        response.headers.update(headers)
        return OperationView(
            operationId=operationId,
            kind=kind,
            status=latest.to_status,
            eventSeq=seq,
            createdAt=snapshot.summary.created_at,
            finalizedAt=snapshot.finalized_at,
            terminal=snapshot.terminal,
            errorNote=latest.error_code,
            steps=steps,
            events=events,
        )

    return router
