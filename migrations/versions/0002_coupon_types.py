"""Create coupon-type persistence and idempotency tables (Phase 1, no chain submission)."""

import sqlalchemy as sa
from alembic import op

revision = "0002_coupon_types"
down_revision = "0001_marketplace_projection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "coupon_types",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("start_date", sa.BigInteger(), nullable=False),
        sa.Column("expire_date", sa.BigInteger(), nullable=False),
        sa.Column("face_value", sa.BigInteger(), nullable=False),
        sa.Column("total_supply", sa.BigInteger(), nullable=False),
        sa.Column("chain_type_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "coupon_type_idempotency_keys",
        sa.Column("idempotency_key", sa.String(200), primary_key=True),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "coupon_type_id",
            sa.String(36),
            sa.ForeignKey("coupon_types.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("coupon_type_idempotency_keys")
    op.drop_table("coupon_types")
