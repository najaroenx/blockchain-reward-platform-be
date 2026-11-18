from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import Connection
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from marketplace.infrastructure.operation_models import IdempotencyKey


def claim_key(
    connection: Connection, *, scope: str, key: str, digest: bytes, now: datetime
) -> str | None:
    insert = postgres_insert if connection.dialect.name == "postgresql" else sqlite_insert
    claim = (
        insert(IdempotencyKey)
        .values(
            id="ik_" + uuid4().hex,
            scope=scope,
            key=key,
            fingerprint=digest,
            created_at=now,
            expires_at=now + timedelta(hours=72),
        )
        .on_conflict_do_nothing(index_elements=["scope", "key"])
        .returning(IdempotencyKey.id)
    )
    return connection.execute(claim).scalar_one_or_none()
