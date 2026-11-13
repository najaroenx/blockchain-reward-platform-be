from sqlite3 import Connection as SqliteConnection

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.pool.base import ConnectionPoolEntry


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, url: str) -> None:
        parsed = make_url(url)
        if parsed.drivername == "postgresql":
            parsed = parsed.set(drivername="postgresql+psycopg")
        if parsed.get_backend_name() == "sqlite":
            if parsed.database == ":memory:":
                self.engine = create_engine(
                    parsed, connect_args={"check_same_thread": False}, poolclass=StaticPool
                )
            else:
                self.engine = create_engine(parsed, connect_args={"check_same_thread": False})
        else:
            self.engine = create_engine(
                parsed,
                pool_pre_ping=True,
                connect_args={"connect_timeout": 3},
                isolation_level="REPEATABLE READ",
            )
        if parsed.get_backend_name() == "sqlite":
            event.listen(self.engine, "connect", enable_foreign_keys)
        self.sessions: sessionmaker[Session] = sessionmaker(self.engine, expire_on_commit=False)

    engine: Engine

    def close(self) -> None:
        self.engine.dispose()


def enable_foreign_keys(connection: SqliteConnection, record: ConnectionPoolEntry) -> None:
    connection.execute("PRAGMA foreign_keys=ON")
