"""Persist outbox leases, retry schedules, and worker attempts."""

import sqlalchemy as sa
from alembic import op

revision = "0012_worker_outbox"
down_revision = "0011_point_type_admission"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_attempt",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("outbox_id", sa.Text(), sa.ForeignKey("outbox.id"), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("outcome", sa.String(16)),
        sa.Column("retry_at", sa.DateTime(timezone=True)),
        sa.Column("error_type", sa.String(64)),
        sa.UniqueConstraint("outbox_id", "attempt_no", name="uq_worker_attempt_number"),
        sa.CheckConstraint("attempt_no > 0", name="ck_worker_attempt_number"),
        sa.CheckConstraint("outcome IN ('SUCCEEDED', 'RETRY')", name="ck_worker_attempt_outcome"),
        sa.CheckConstraint(
            "(finished_at IS NULL) = (outcome IS NULL)", name="ck_worker_attempt_completion"
        ),
        sa.CheckConstraint(
            "COALESCE(outcome = 'RETRY', false) = (retry_at IS NOT NULL)",
            name="ck_worker_attempt_retry",
        ),
    )
    op.create_index(
        "uq_worker_attempt_active",
        "worker_attempt",
        ["outbox_id"],
        unique=True,
        postgresql_where=sa.text("finished_at IS NULL"),
        sqlite_where=sa.text("finished_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("worker_attempt")
