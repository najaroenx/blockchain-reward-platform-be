"""Add Coupon expiry metadata to the marketplace listing projection."""

import sqlalchemy as sa
from alembic import op

revision = "0004_listing_coupon_expiry"
down_revision = "0003_redeem_execution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "marketplace_listing_projection",
        sa.Column(
            "coupon_expires_at",
            sa.String(78).with_variant(sa.Numeric(78, 0), "postgresql"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("marketplace_listing_projection", "coupon_expires_at")
