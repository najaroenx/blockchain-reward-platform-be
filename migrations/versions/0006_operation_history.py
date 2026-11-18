"""Record operation transitions and require a matching event at commit."""

import sqlalchemy as sa
from alembic import op

revision = "0006_operation_history"
down_revision = "0005_deposit_admission"
branch_labels = None
depends_on = None

EVENT_KEYS = ["operation_id", "seq", "to_status"]
TRANSITION = """
       (OLD.status = 'PENDING' AND NEW.status IN ('SUBMITTED','MANUAL_REVIEW'))
    OR (OLD.status = 'SUBMITTED' AND
        NEW.status IN ('CONFIRMED','CHAIN_REVERTED','ORPHANED','MANUAL_REVIEW'))
    OR (OLD.status = 'CONFIRMED' AND NEW.status IN ('FINALIZED','ORPHANED','MANUAL_REVIEW'))
    OR (OLD.status = 'ORPHANED' AND NEW.status IN ('SUBMITTED','MANUAL_REVIEW'))
    OR (OLD.status = 'MANUAL_REVIEW' AND NEW.status IN ('SUBMITTED','CHAIN_REVERTED','FINALIZED'))
"""
INVALID_UPDATE = f"""
       OLD.status IN ('FINALIZED','CHAIN_REVERTED')
    OR NEW.id <> OLD.id
    OR NEW.actor_user_id <> OLD.actor_user_id
    OR NEW.kind <> OLD.kind
    OR NEW.created_at <> OLD.created_at
    OR (NEW.status = OLD.status AND NEW.event_seq <> OLD.event_seq)
    OR (NEW.status <> OLD.status AND
        (NEW.event_seq <> OLD.event_seq + 1 OR NOT ({TRANSITION})))
"""
MATCH_EXPECTATION = """
    SELECT 1 FROM operation
    WHERE id = NEW.operation_id AND event_seq = NEW.seq AND status = NEW.to_status
"""
INSERT_REQUIREMENT = """
    INSERT INTO operation_event_expected(operation_id, seq, to_status)
    VALUES (NEW.id, NEW.event_seq, NEW.status)
"""


def upgrade() -> None:
    postgres = op.get_context().dialect.name == "postgresql"
    op.add_column(
        "operation", sa.Column("event_seq", sa.Integer(), server_default="1", nullable=False)
    )
    op.create_index("ix_operation_actor_recent", "operation", ["actor_user_id", "created_at", "id"])

    expected_constraints = [
        sa.UniqueConstraint(*EVENT_KEYS, name="uq_opexpected_status"),
    ]
    reciprocal = sa.ForeignKeyConstraint(
        EVENT_KEYS,
        [f"operation_event.{column}" for column in EVENT_KEYS],
        name="fk_opexpected_event",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_table(
        "operation_event_expected",
        sa.Column("operation_id", sa.Text(), sa.ForeignKey("operation.id"), primary_key=True),
        sa.Column("seq", sa.Integer(), primary_key=True),
        sa.Column("to_status", sa.String(32), nullable=False),
        *expected_constraints,
        *([] if postgres else [reciprocal]),
    )
    op.create_table(
        "operation_event",
        sa.Column("operation_id", sa.Text(), sa.ForeignKey("operation.id"), primary_key=True),
        sa.Column("seq", sa.Integer(), primary_key=True),
        sa.Column(
            "previous_seq",
            sa.Integer(),
            sa.Computed("CASE WHEN seq > 1 THEN seq - 1 END", persisted=True),
        ),
        sa.Column("from_status", sa.String(32)),
        sa.Column("to_status", sa.String(32), nullable=False),
        sa.Column("actor", sa.String(24), nullable=False, server_default="SYSTEM"),
        sa.Column("tx_hash", sa.String(66)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("is_snapshot", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(*EVENT_KEYS, name="uq_opevent_status"),
        sa.ForeignKeyConstraint(
            EVENT_KEYS,
            [f"operation_event_expected.{column}" for column in EVENT_KEYS],
            name="fk_opevent_expected",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["operation_id", "previous_seq", "from_status"],
            [f"operation_event.{column}" for column in EVENT_KEYS],
            name="fk_opevent_previous",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint(
            "seq >= 1 AND ((seq = 1) = (from_status IS NULL))", name="ck_opevent_seq"
        ),
        sa.CheckConstraint(
            "actor IN ('SYSTEM','MERCHANT_ADMIN','CUSTOMER','PLATFORM_ADMIN')",
            name="ck_opevent_actor",
        ),
    )
    if postgres:
        op.create_foreign_key(
            "fk_opexpected_event",
            "operation_event_expected",
            "operation_event",
            EVENT_KEYS,
            EVENT_KEYS,
            deferrable=True,
            initially="DEFERRED",
        )

    # The previous schema has no event log. Preserve only the state it recorded.
    op.execute("""
        INSERT INTO operation_event_expected (operation_id, seq, to_status)
        SELECT id, 1, status FROM operation
    """)
    op.execute("""
        INSERT INTO operation_event
            (operation_id, seq, to_status, tx_hash, error_code, is_snapshot, created_at)
        SELECT operation.id, 1, operation.status,
            CASE WHEN operation.status IN ('SUBMITTED','CONFIRMED','FINALIZED','CHAIN_REVERTED')
                 THEN operation_step.tx_hash END,
            redeem_refusal.code, TRUE,
            CASE WHEN operation.status = 'PENDING'
                 THEN operation.created_at ELSE CURRENT_TIMESTAMP END
        FROM operation LEFT JOIN operation_step ON operation.id = operation_step.operation_id
        LEFT JOIN redeem_refusal ON operation.id = redeem_refusal.operation_id
    """)
    if postgres:
        install_postgres_triggers()
    else:
        install_sqlite_triggers()


def install_postgres_triggers() -> None:
    op.execute(f"""
        CREATE FUNCTION f_operation_event_expected() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'PENDING' OR NEW.event_seq <> 1 THEN
                    RAISE EXCEPTION 'Operation must start at PENDING seq 1' USING ERRCODE = '23514';
                END IF;
            ELSE
                IF {INVALID_UPDATE} THEN
                    RAISE EXCEPTION 'Invalid operation transition' USING ERRCODE = '23514';
                END IF;
                IF NEW.status = OLD.status THEN RETURN NEW; END IF;
            END IF;
            {INSERT_REQUIREMENT};
            RETURN NEW;
        END $$;
    """)
    op.execute("""
        CREATE TRIGGER trg_operation_event_expected AFTER INSERT OR UPDATE ON operation
        FOR EACH ROW EXECUTE FUNCTION f_operation_event_expected()
    """)
    op.execute(f"""
        CREATE FUNCTION f_operation_event_expected_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'Recorded transition is immutable' USING ERRCODE = '23514';
            END IF;
            IF NOT EXISTS ({MATCH_EXPECTATION}) THEN
                RAISE EXCEPTION 'Operation state does not match required event'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$;
    """)
    op.execute("""
        CREATE TRIGGER trg_operation_event_expected_guard
        BEFORE INSERT OR UPDATE OR DELETE ON operation_event_expected
        FOR EACH ROW EXECUTE FUNCTION f_operation_event_expected_guard()
    """)
    op.execute("""
        CREATE FUNCTION f_operation_event_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NOT NEW.is_snapshot THEN RETURN NEW; END IF;
            END IF;
            RAISE EXCEPTION 'Operation history is append-only' USING ERRCODE = '23514';
        END $$;
    """)
    op.execute("""
        CREATE TRIGGER trg_operation_event_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON operation_event
        FOR EACH ROW EXECUTE FUNCTION f_operation_event_immutable()
    """)


def install_sqlite_triggers() -> None:
    op.execute(f"""
        CREATE TRIGGER trg_operation_event_created AFTER INSERT ON operation
        BEGIN
            SELECT RAISE(ABORT, 'Operation must start at PENDING seq 1')
            WHERE NEW.status <> 'PENDING' OR NEW.event_seq <> 1;
            {INSERT_REQUIREMENT};
        END
    """)
    op.execute(f"""
        CREATE TRIGGER trg_operation_event_expected AFTER UPDATE ON operation
        BEGIN
            SELECT RAISE(ABORT, 'Invalid operation transition') WHERE {INVALID_UPDATE};
            INSERT INTO operation_event_expected(operation_id, seq, to_status)
            SELECT NEW.id, NEW.event_seq, NEW.status WHERE NEW.status <> OLD.status;
        END
    """)
    op.execute(f"""
        CREATE TRIGGER trg_operation_event_expected_guard BEFORE INSERT ON operation_event_expected
        WHEN NOT EXISTS ({MATCH_EXPECTATION})
        BEGIN
            SELECT RAISE(ABORT, 'Operation state does not match required event');
        END
    """)
    op.execute("""
        CREATE TRIGGER trg_operation_event_snapshot BEFORE INSERT ON operation_event
        WHEN NEW.is_snapshot
        BEGIN SELECT RAISE(ABORT, 'Operation history is append-only'); END
    """)
    for action in ("UPDATE", "DELETE"):
        op.execute(f"""
            CREATE TRIGGER trg_operation_event_{action.lower()}
            BEFORE {action} ON operation_event
            BEGIN SELECT RAISE(ABORT, 'Operation history is append-only'); END
        """)
        op.execute(f"""
            CREATE TRIGGER trg_operation_event_expected_{action.lower()}
            BEFORE {action} ON operation_event_expected
            BEGIN SELECT RAISE(ABORT, 'Recorded transition is immutable'); END
        """)


def downgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        op.execute("DROP TRIGGER trg_operation_event_expected ON operation")
        op.execute("DROP TRIGGER trg_operation_event_expected_guard ON operation_event_expected")
        op.execute("DROP TRIGGER trg_operation_event_immutable ON operation_event")
        op.execute("DROP FUNCTION f_operation_event_expected()")
        op.execute("DROP FUNCTION f_operation_event_expected_guard()")
        op.execute("DROP FUNCTION f_operation_event_immutable()")
        op.drop_constraint("fk_opexpected_event", "operation_event_expected", type_="foreignkey")
    else:
        for name in (
            "created",
            "expected",
            "expected_guard",
            "snapshot",
            "update",
            "delete",
            "expected_update",
            "expected_delete",
        ):
            op.execute(f"DROP TRIGGER trg_operation_event_{name}")
        op.execute("DELETE FROM operation_event")
        op.execute("DELETE FROM operation_event_expected")
    op.drop_table("operation_event")
    op.drop_table("operation_event_expected")
    op.drop_index("ix_operation_actor_recent", table_name="operation")
    op.drop_column("operation", "event_seq")
