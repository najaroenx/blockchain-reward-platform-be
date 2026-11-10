from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


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
        self.sessions: sessionmaker[Session] = sessionmaker(self.engine, expire_on_commit=False)

    engine: Engine

    def close(self) -> None:
        self.engine.dispose()
