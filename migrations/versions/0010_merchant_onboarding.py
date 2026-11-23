"""Persist merchant onboarding and unsigned provision plans.

merchant/merchant_admin already exist (0006_auth_login); this extends them
instead of recreating, since BE-0902 was developed before that foundation
landed on this chain.
"""

import sqlalchemy as sa
from alembic import op

revision = "0010_merchant_onboarding"
down_revision = "0009_operation_history"
branch_labels = None
depends_on = None

INFLIGHT = "kind = 'MERCHANT_PROVISION' AND status NOT IN ('FINALIZED', 'CHAIN_REVERTED')"
PROVISION_SUBJECT = """
    NEW.kind = 'MERCHANT_PROVISION' AND NOT EXISTS (
        SELECT 1 FROM merchant WHERE id = NEW.subject_id
    )
"""
PROVISION_TOPIC = """
    NEW.event_type <> 'merchant.provision.v1' AND EXISTS (
        SELECT 1 FROM operation WHERE id = NEW.operation_id AND kind = 'MERCHANT_PROVISION'
    )
"""
PROVISION_SUBJECT_IMMUTABLE = """
    OLD.kind = 'MERCHANT_PROVISION'
    AND coalesce(NEW.subject_id, '') <> coalesce(OLD.subject_id, '')
"""
MERCHANT_ACTIVATION = """
    NEW.status = 'ACTIVE' AND NOT EXISTS (
        SELECT 1 FROM operation
        WHERE kind = 'MERCHANT_PROVISION' AND subject_id = NEW.id AND status = 'FINALIZED'
    )
"""


def upgrade() -> None:
    with op.batch_alter_table("merchant") as batch:
        batch.add_column(sa.Column("activated_at", sa.DateTime(timezone=True)))
        batch.create_check_constraint("ck_merchant_name", "length(trim(name)) BETWEEN 1 AND 200")
        batch.create_check_constraint(
            "ck_merchant_active", "status <> 'ACTIVE' OR activated_at IS NOT NULL"
        )
    op.create_index("ix_admin_merchant", "merchant_admin", ["merchant_id"])
    op.create_table(
        "wallet",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("address", sa.LargeBinary(20), nullable=False),
        sa.Column("key_ref", sa.Text(), nullable=False),
        sa.Column("owner_kind", sa.String(16), nullable=False),
        sa.Column("owner_id", sa.String(), sa.ForeignKey("merchant.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(address) = 20", name="ck_wallet_address_length"),
        sa.CheckConstraint(
            "key_ref LIKE 'kms://_%' OR key_ref LIKE 'mock-kms://_%'",
            name="ck_wallet_key_reference",
        ),
        sa.CheckConstraint("owner_kind = 'MERCHANT'", name="ck_wallet_owner_kind"),
    )
    op.create_index("uq_wallet_owner", "wallet", ["owner_kind", "owner_id"], unique=True)
    op.create_index("uq_wallet_address", "wallet", ["address"], unique=True)
    op.create_index("uq_wallet_key_ref", "wallet", ["key_ref"], unique=True)
    op.add_column("operation", sa.Column("subject_id", sa.Text(), nullable=True))
    op.create_index(
        "uq_provision_inflight",
        "operation",
        ["subject_id"],
        unique=True,
        postgresql_where=sa.text(INFLIGHT),
        sqlite_where=sa.text(INFLIGHT),
    )
    op.create_table(
        "operation_plan_step",
        sa.Column("operation_id", sa.Text(), sa.ForeignKey("operation.id"), primary_key=True),
        sa.Column("step_index", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("signer_role", sa.String(24), nullable=False),
        sa.CheckConstraint("step_index >= 1", name="ck_plan_step_index"),
        sa.CheckConstraint(
            "signer_role IN ('MARKETPLACE_OWNER','MERCHANT_WALLET')", name="ck_plan_step_signer"
        ),
    )
    add_guards()


GUARDS = (
    ("provision_subject", "operation", "INSERT", PROVISION_SUBJECT, "Invalid provision subject"),
    (
        "provision_subject_immutable",
        "operation",
        "UPDATE OF subject_id",
        PROVISION_SUBJECT_IMMUTABLE,
        "Provision subject is immutable",
    ),
    ("provision_topic", "outbox", "INSERT", PROVISION_TOPIC, "Invalid merchant provision topic"),
    (
        "provision_topic_update",
        "outbox",
        "UPDATE OF event_type, operation_id",
        PROVISION_TOPIC,
        "Invalid merchant provision topic",
    ),
    (
        "merchant_activation",
        "merchant",
        "INSERT",
        MERCHANT_ACTIVATION,
        "Provision is not finalized",
    ),
    (
        "merchant_activation_update",
        "merchant",
        "UPDATE OF status",
        MERCHANT_ACTIVATION,
        "Provision is not finalized",
    ),
)


def add_guards() -> None:
    for name, table, event, predicate, error in GUARDS:
        if op.get_context().dialect.name == "postgresql":
            op.execute(f"""
                CREATE FUNCTION f_{name}() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF {predicate} THEN
                        RAISE EXCEPTION '{error}' USING ERRCODE = '23514';
                    END IF;
                    RETURN NEW;
                END $$;
            """)
            op.execute(f"""
                CREATE TRIGGER trg_{name} BEFORE {event} ON {table}
                FOR EACH ROW EXECUTE FUNCTION f_{name}()
            """)
        else:
            op.execute(f"""
                CREATE TRIGGER trg_{name} BEFORE {event} ON {table}
                WHEN {predicate}
                BEGIN SELECT RAISE(ABORT, '{error}'); END
            """)


def downgrade() -> None:
    postgres = op.get_context().dialect.name == "postgresql"
    for name, table, *_ in GUARDS:
        op.execute(
            f"DROP TRIGGER trg_{name} ON {table}" if postgres else f"DROP TRIGGER trg_{name}"
        )
        if postgres:
            op.execute(f"DROP FUNCTION f_{name}()")
    op.drop_table("operation_plan_step")
    op.drop_index("uq_provision_inflight", table_name="operation")
    op.drop_column("operation", "subject_id")
    op.drop_table("wallet")
    op.drop_index("ix_admin_merchant", table_name="merchant_admin")
    with op.batch_alter_table("merchant") as batch:
        batch.drop_constraint("ck_merchant_active", type_="check")
        batch.drop_constraint("ck_merchant_name", type_="check")
        batch.drop_column("activated_at")
