"""Create the atomic redeem admission records; no per-code reservations."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_redeem_admission"
down_revision = "0001_marketplace_projection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operation",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("actor_user_id", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "payload", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "idempotency_key",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("fingerprint", sa.LargeBinary(32), nullable=False),
        sa.Column("operation_id", sa.Text(), sa.ForeignKey("operation.id")),
        sa.Column("response_status", sa.Integer()),
        sa.Column("response_body", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("scope", "key", name="uq_idem"),
        sa.CheckConstraint("length(key) BETWEEN 8 AND 255", name="ck_idem_key_length"),
        sa.CheckConstraint("length(fingerprint) = 32", name="ck_idem_fingerprint_length"),
        sa.CheckConstraint(
            "(operation_id IS NULL AND response_status IS NULL AND response_body IS NULL) OR "
            "(operation_id IS NOT NULL AND response_status IS NOT NULL "
            "AND response_status BETWEEN 200 AND 599 AND response_body IS NOT NULL)",
            name="ck_idem_acknowledgement",
        ),
    )
    op.create_index("ix_idem_expiry", "idempotency_key", ["expires_at"])
    op.create_table(
        "outbox",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("operation_id", sa.Text(), sa.ForeignKey("operation.id"), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("operation_id", name="uq_outbox_operation"),
    )
    op.create_index("ix_outbox_pending", "outbox", ["published_at", "created_at"])


def downgrade() -> None:
    op.drop_table("outbox")
    op.drop_table("idempotency_key")
    op.drop_table("operation")
