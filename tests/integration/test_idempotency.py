from datetime import UTC, datetime, timedelta

import pytest

from marketplace.infrastructure.database import Database
from marketplace.infrastructure.idempotency import (
    IdempotencyInProgress,
    IdempotencyKeyConflict,
    SqlIdempotencyStore,
)

SCOPE = "coupons.issue"
FINGERPRINT_A = "0x" + "a" * 64
FINGERPRINT_B = "0x" + "b" * 64


def _future() -> datetime:
    return datetime.now(UTC) + timedelta(hours=72)


def test_reserve_fresh_key_returns_none(database_url: str) -> None:
    database = Database(database_url)
    with database.sessions.begin() as session:
        store = SqlIdempotencyStore(session)
        result = store.reserve(SCOPE, "order-0001", FINGERPRINT_A, expires_at=_future())
    assert result is None


def test_reserve_replays_completed_response(database_url: str) -> None:
    database = Database(database_url)
    with database.sessions.begin() as session:
        store = SqlIdempotencyStore(session)
        store.reserve(SCOPE, "order-0002", FINGERPRINT_A, expires_at=_future())
        store.complete(SCOPE, "order-0002", response_status=201, response_body={"id": "abc"})

        replay = store.reserve(SCOPE, "order-0002", FINGERPRINT_A, expires_at=_future())

    assert replay is not None
    assert replay.response_status == 201
    assert replay.response_body == {"id": "abc"}


def test_reserve_pending_key_raises_in_progress(database_url: str) -> None:
    database = Database(database_url)
    with database.sessions.begin() as session:
        store = SqlIdempotencyStore(session)
        store.reserve(SCOPE, "order-0003", FINGERPRINT_A, expires_at=_future())

        with pytest.raises(IdempotencyInProgress):
            store.reserve(SCOPE, "order-0003", FINGERPRINT_A, expires_at=_future())


def test_reserve_mismatched_fingerprint_raises_conflict(database_url: str) -> None:
    database = Database(database_url)
    with database.sessions.begin() as session:
        store = SqlIdempotencyStore(session)
        store.reserve(SCOPE, "order-0004", FINGERPRINT_A, expires_at=_future())

        with pytest.raises(IdempotencyKeyConflict):
            store.reserve(SCOPE, "order-0004", FINGERPRINT_B, expires_at=_future())


def test_complete_without_reservation_raises_lookup_error(database_url: str) -> None:
    database = Database(database_url)
    with database.sessions.begin() as session:
        store = SqlIdempotencyStore(session)
        with pytest.raises(LookupError):
            store.complete(SCOPE, "never-reserved", response_status=200, response_body={})
