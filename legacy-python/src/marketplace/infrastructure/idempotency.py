"""Idempotency-Key store, reusable across features.

Backed by the `idempotency_key` table from migrations/versions/0002_legacy_schema_port.py (schema
unchanged here — only which Python object declares it). No feature consumes this yet: `coupons`
is still a package skeleton and no other feature has a write endpoint (see docs/architecture.md's
open-decisions table). When a feature gets its first write use case, that feature should define
its own narrow Protocol in `application/ports.py` shaped like `SqlIdempotencyStore` below, and
`main.py` injects a `SqlIdempotencyStore` instance into it — the same way `provide_catalog`
injects `Catalog` for `listings` today. `fingerprint` is a lowercase-hex string (`0x` + 64 hex
chars), matching this project's convention for hash-shaped columns elsewhere.

`operation_id`'s foreign key targets the `operation` table declared in `legacy_schema.py`, so
anything using `SqlIdempotencyStore` must import `marketplace.infrastructure.legacy_schema`
first (as `migrations/env.py` already does) or SQLAlchemy cannot resolve it at flush time.
"""

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from marketplace.infrastructure.database import Base

HASH = String(66)
TS = DateTime(timezone=True)


class IdempotencyKeyRecord(Base):
    __tablename__ = "idempotency_key"
    __table_args__ = (
        CheckConstraint("length(key) BETWEEN 8 AND 255", name="ck_idem_key_len"),
        CheckConstraint("length(fingerprint) = 66", name="ck_idem_fingerprint_len"),
        UniqueConstraint("scope", "key", name="uq_idem"),
    )

    id: Mapped[str] = mapped_column(Text(), primary_key=True)
    scope: Mapped[str] = mapped_column(Text())
    key: Mapped[str] = mapped_column(Text())
    fingerprint: Mapped[str] = mapped_column(HASH)
    operation_id: Mapped[str | None] = mapped_column(Text(), ForeignKey("operation.id"))
    response_status: Mapped[int | None] = mapped_column(Integer())
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSON())
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(TS)


class IdempotencyKeyConflict(Exception):
    """`(scope, key)` was reused with a different request fingerprint."""


class IdempotencyInProgress(Exception):
    """`(scope, key)` is reserved by a request that has not completed yet."""


class SqlIdempotencyStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _find(self, scope: str, key: str) -> IdempotencyKeyRecord | None:
        return self.session.scalar(
            select(IdempotencyKeyRecord).where(
                IdempotencyKeyRecord.scope == scope, IdempotencyKeyRecord.key == key
            )
        )

    def reserve(
        self, scope: str, key: str, fingerprint: str, *, expires_at: datetime
    ) -> IdempotencyKeyRecord | None:
        existing = self._find(scope, key)
        if existing is None:
            record = IdempotencyKeyRecord(
                id=str(uuid4()),
                scope=scope,
                key=key,
                fingerprint=fingerprint,
                expires_at=expires_at,
            )
            self.session.add(record)
            self.session.flush()
            return None
        if existing.fingerprint != fingerprint:
            raise IdempotencyKeyConflict(scope, key)
        if existing.response_status is None:
            raise IdempotencyInProgress(scope, key)
        return existing

    def complete(
        self, scope: str, key: str, *, response_status: int, response_body: dict[str, Any]
    ) -> None:
        record = self._find(scope, key)
        if record is None:
            raise LookupError(f"no reserved idempotency key for {scope}:{key}")
        record.response_status = response_status
        record.response_body = response_body
        self.session.flush()
