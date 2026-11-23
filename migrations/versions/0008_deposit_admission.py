"""Add pending deposits and link their acknowledgements to the shared key table."""

import sqlalchemy as sa
from alembic import op

revision = "0008_deposit_admission"
down_revision = "0007_disabled_sessions"
branch_labels = None
depends_on = None

ACKNOWLEDGEMENT = "ck_idem_acknowledgement"


def upgrade() -> None:
    op.create_table(
        "thb_deposit",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("holder_kind", sa.String(16), nullable=False),
        sa.Column("holder_id", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_ref", sa.Text(), nullable=False),
        sa.Column("amount_satang", sa.BigInteger(), nullable=False),
        sa.Column("amount_base", sa.String(78), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("holder_kind = 'MERCHANT'", name="ck_dep_holder_kind"),
        sa.CheckConstraint("amount_satang BETWEEN 2000 AND 5000000", name="ck_dep_amount_range"),
        sa.CheckConstraint(
            "amount_base = CAST(amount_satang AS TEXT) || '0000000000000000'",
            name="ck_dep_amount_exact",
        ),
        sa.CheckConstraint("status = 'PENDING_PAYMENT'", name="ck_dep_pending_payment"),
        sa.CheckConstraint("expires_at > created_at", name="ck_dep_expiry"),
        sa.UniqueConstraint("provider", "provider_ref", name="uq_deposit_provider_ref"),
    )
    with op.batch_alter_table("idempotency_key") as batch:
        batch.drop_constraint(ACKNOWLEDGEMENT, type_="check")
        batch.add_column(sa.Column("deposit_id", sa.Text(), nullable=True))
        batch.create_foreign_key("fk_idem_deposit", "thb_deposit", ["deposit_id"], ["id"])
        batch.create_check_constraint(
            ACKNOWLEDGEMENT,
            "(operation_id IS NULL AND deposit_id IS NULL "
            "AND response_status IS NULL AND response_body IS NULL) OR "
            "(((operation_id IS NOT NULL AND deposit_id IS NULL) OR "
            "(operation_id IS NULL AND deposit_id IS NOT NULL)) AND response_status IS NOT NULL "
            "AND response_status BETWEEN 200 AND 599 AND response_body IS NOT NULL)",
        )


def downgrade() -> None:
    op.execute("DELETE FROM idempotency_key WHERE deposit_id IS NOT NULL")
    with op.batch_alter_table("idempotency_key") as batch:
        batch.drop_constraint(ACKNOWLEDGEMENT, type_="check")
        batch.drop_constraint("fk_idem_deposit", type_="foreignkey")
        batch.drop_column("deposit_id")
        batch.create_check_constraint(
            ACKNOWLEDGEMENT,
            "(operation_id IS NULL AND response_status IS NULL AND response_body IS NULL) OR "
            "(operation_id IS NOT NULL AND response_status IS NOT NULL "
            "AND response_status BETWEEN 200 AND 599 AND response_body IS NOT NULL)",
        )
    op.drop_table("thb_deposit")
