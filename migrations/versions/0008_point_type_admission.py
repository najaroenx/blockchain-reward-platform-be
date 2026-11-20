"""Store Point type requests and guard concurrent deployments."""

import sqlalchemy as sa
from alembic import op

revision = "0008_point_type_admission"
down_revision = "0007_merchant_onboarding"
branch_labels = None
depends_on = None

CLAIMED = "status <> 'FAILED'"
INFLIGHT = "kind = 'POINT_TYPE_CREATE' AND status NOT IN ('FINALIZED', 'CHAIN_REVERTED')"


def upgrade() -> None:
    postgres = op.get_context().dialect.name == "postgresql"
    symbol_check = (
        "symbol ~ '^[A-Z][A-Z0-9]{1,10}$'"
        if postgres
        else "length(symbol) BETWEEN 2 AND 11 AND symbol GLOB '[A-Z]*' "
        "AND symbol NOT GLOB '*[^A-Z0-9]*'"
    )
    op.create_table(
        "point_type",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("merchant_id", sa.String(), sa.ForeignKey("merchant.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("decimals", sa.SmallInteger(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="REQUESTED"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("live_at", sa.DateTime(timezone=True)),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("length(trim(name)) BETWEEN 1 AND 60", name="ck_point_type_name"),
        sa.CheckConstraint(symbol_check, name="ck_point_type_symbol"),
        sa.CheckConstraint("decimals = 18", name="ck_point_type_decimals"),
        sa.CheckConstraint(
            "status IN ('REQUESTED','LIVE','FAILED','RETIRED')", name="ck_point_type_status"
        ),
        sa.CheckConstraint(
            "(status = 'LIVE') = (live_at IS NOT NULL) OR status = 'RETIRED'",
            name="ck_point_type_live",
        ),
        sa.CheckConstraint(
            "(status = 'RETIRED') = (retired_at IS NOT NULL)", name="ck_point_type_retired"
        ),
    )
    op.create_index(
        "uq_point_symbol",
        "point_type",
        ["merchant_id", "symbol"],
        unique=True,
        postgresql_where=sa.text(CLAIMED),
        sqlite_where=sa.text(CLAIMED),
    )
    op.create_index(
        "uq_point_name",
        "point_type",
        ["merchant_id", sa.text("lower(ltrim(rtrim(name)))")],
        unique=True,
        postgresql_where=sa.text(CLAIMED),
        sqlite_where=sa.text(CLAIMED),
    )
    op.create_index("ix_point_type_merchant", "point_type", ["merchant_id", "status"])
    op.create_index(
        "uq_point_type_inflight",
        "operation",
        ["subject_id"],
        unique=True,
        postgresql_where=sa.text(INFLIGHT),
        sqlite_where=sa.text(INFLIGHT),
    )
    if postgres:
        op.execute("""
            CREATE FUNCTION point_type_require_active_merchant() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                PERFORM id FROM merchant
                WHERE id = NEW.merchant_id AND status = 'ACTIVE' FOR SHARE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'Merchant is not active'
                        USING ERRCODE = '23514', CONSTRAINT = 'ck_point_type_merchant_active';
                END IF;
                RETURN NEW;
            END;
            $$
        """)
        op.execute("""
            CREATE TRIGGER trg_point_type_merchant_active
            BEFORE INSERT ON point_type FOR EACH ROW
            EXECUTE FUNCTION point_type_require_active_merchant()
        """)
    else:
        op.execute("""
            CREATE TRIGGER trg_point_type_merchant_active
            BEFORE INSERT ON point_type FOR EACH ROW
            WHEN NOT EXISTS (
                SELECT 1 FROM merchant WHERE id = NEW.merchant_id AND status = 'ACTIVE'
            )
            BEGIN
                SELECT RAISE(ABORT, 'ck_point_type_merchant_active');
            END
        """)


def downgrade() -> None:
    op.drop_index("uq_point_type_inflight", table_name="operation")
    op.drop_table("point_type")
    if op.get_context().dialect.name == "postgresql":
        op.execute("DROP FUNCTION point_type_require_active_merchant()")
