from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from marketplace.infrastructure.database import Base


# Persists a merchant's point type, its uniqueness constraints, and its lifecycle state.
class PointType(Base):
    __tablename__ = "point_type"
    __table_args__ = (
        CheckConstraint("length(trim(name)) BETWEEN 1 AND 60", name="ck_point_type_name"),
        CheckConstraint("symbol ~ '^[A-Z][A-Z0-9]{1,10}$'", name="ck_point_type_symbol").ddl_if(
            dialect="postgresql"
        ),
        CheckConstraint(
            "length(symbol) BETWEEN 2 AND 11 AND symbol GLOB '[A-Z]*' "
            "AND symbol NOT GLOB '*[^A-Z0-9]*'",
            name="ck_point_type_symbol",
        ).ddl_if(dialect="sqlite"),
        CheckConstraint("decimals = 18", name="ck_point_type_decimals"),
        CheckConstraint(
            "status IN ('REQUESTED','LIVE','FAILED','RETIRED')", name="ck_point_type_status"
        ),
        CheckConstraint(
            "(status = 'LIVE') = (live_at IS NOT NULL) OR status = 'RETIRED'",
            name="ck_point_type_live",
        ),
        CheckConstraint(
            "(status = 'RETIRED') = (retired_at IS NOT NULL)", name="ck_point_type_retired"
        ),
        Index(
            "uq_point_symbol",
            "merchant_id",
            "symbol",
            unique=True,
            postgresql_where=text("status <> 'FAILED'"),
            sqlite_where=text("status <> 'FAILED'"),
        ),
        Index("ix_point_type_merchant", "merchant_id", "status"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchant.id"))
    name: Mapped[str] = mapped_column(Text)
    symbol: Mapped[str] = mapped_column(String)
    decimals: Mapped[int] = mapped_column(SmallInteger)
    status: Mapped[str] = mapped_column(String, server_default="REQUESTED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    live_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index(
    "uq_point_name",
    PointType.merchant_id,
    func.lower(func.ltrim(func.rtrim(PointType.name))),
    unique=True,
    postgresql_where=text("status <> 'FAILED'"),
    sqlite_where=text("status <> 'FAILED'"),
)
