"""Persist signed redeem transactions and correlated receipt projections."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003_redeem_execution"
down_revision = "0002_redeem_admission"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "redeem_refusal",
        sa.Column("operation_id", sa.Text(), sa.ForeignKey("operation.id"), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("refused_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "operation_step",
        sa.Column("operation_id", sa.Text(), sa.ForeignKey("operation.id"), primary_key=True),
        sa.Column("chain_id", sa.String(78), nullable=False),
        sa.Column("signer_address", sa.String(42), nullable=False),
        sa.Column("nonce", sa.String(78), nullable=False),
        sa.Column("raw_tx", sa.LargeBinary(), nullable=False),
        sa.Column("tx_hash", sa.String(66), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("chain_id", "tx_hash", name="uq_step_chain_tx"),
        sa.UniqueConstraint("chain_id", "signer_address", "nonce", name="uq_step_signer_nonce"),
        sa.CheckConstraint("length(raw_tx) > 0", name="ck_step_raw_transaction"),
        sa.CheckConstraint(
            "status IN ('PREPARED','SUBMITTED','CONFIRMED','CHAIN_REVERTED')", name="ck_step_status"
        ),
    )
    op.create_table(
        "chain_event",
        sa.Column("chain_id", sa.String(78), primary_key=True),
        sa.Column("tx_hash", sa.String(66), primary_key=True),
        sa.Column("log_index", sa.Integer(), primary_key=True),
        sa.Column("operation_id", sa.Text(), sa.ForeignKey("operation.id"), nullable=False),
        sa.Column("contract_address", sa.String(42), nullable=False),
        sa.Column("block_hash", sa.String(66), nullable=False),
        sa.Column("block_number", sa.String(78), nullable=False),
        sa.Column("topics", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False),
        sa.Column("data", sa.Text(), nullable=False),
        sa.Column("mined_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "redeem_result",
        sa.Column(
            "operation_id",
            sa.Text(),
            sa.ForeignKey("operation_step.operation_id"),
            primary_key=True,
        ),
        sa.Column("tx_hash", sa.String(66), nullable=False),
        sa.Column("block_hash", sa.String(66), nullable=False),
        sa.Column("block_number", sa.String(78), nullable=False),
        sa.Column("mined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("result", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False),
        sa.CheckConstraint(
            "status IN ('CONFIRMED','CHAIN_REVERTED')", name="ck_redeem_result_status"
        ),
    )


def downgrade() -> None:
    op.drop_table("redeem_result")
    op.drop_table("chain_event")
    op.drop_table("operation_step")
    op.drop_table("redeem_refusal")
