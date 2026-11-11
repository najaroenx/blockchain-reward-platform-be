"""Create read projections separately from the legacy reference schema."""

import sqlalchemy as sa
from alembic import op

revision = "0001_marketplace_projection"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "marketplace_projection_checkpoint",
        sa.Column("chain_id", sa.BigInteger(), primary_key=True),
        sa.Column("marketplace_address", sa.String(42), primary_key=True),
        sa.Column("indexed_block", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "marketplace_listing_projection",
        sa.Column("chain_id", sa.BigInteger(), primary_key=True),
        sa.Column("marketplace_address", sa.String(42), primary_key=True),
        sa.Column("listing_id", sa.String(78), primary_key=True),
        sa.Column("seller", sa.String(42), nullable=False),
        sa.Column("type_id", sa.String(78), nullable=False),
        sa.Column("amount", sa.String(78), nullable=False),
        sa.Column("price_per_unit", sa.String(78), nullable=False),
        sa.Column("payment_token", sa.String(42), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("listed_at", sa.BigInteger(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("marketplace_listing_projection")
    op.drop_table("marketplace_projection_checkpoint")
