from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


@contextmanager
def auth_transaction(sessions: sessionmaker[Session], scope: bytes) -> Iterator[Session]:
    with sessions() as session:
        try:
            if session.get_bind().dialect.name == "postgresql":
                session.connection(execution_options={"isolation_level": "READ COMMITTED"})
                key = int.from_bytes(sha256(scope).digest()[:8], "big", signed=True)
                session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
            else:
                session.execute(text("BEGIN IMMEDIATE"))
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
