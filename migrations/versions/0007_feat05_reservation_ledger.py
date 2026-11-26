"""FEAT-05 round 1: reservation rules, amounts as uint256, and a ledger of its own for points.

Three things the merged schema got wrong for FEAT-05 (docs/reference-findings.md):

  1. Amounts are decimal strings (0002 ported numeric(78,0) to String(78) and dropped
     `amount > 0`), so nothing stopped '0', '-5' or '1e3'. f_is_uint256 restores the rule on
     PostgreSQL and is shared by every later pack's money columns; this migration applies it to
     reservation and the new point ledger only (T11 T12). SQLite cannot compare numbers this
     large (one point is 10^18 base units), so these rules exist on PostgreSQL only.

  2. ledger_entry merged two ledgers that cannot share a table: THB (FEAT-10/12: account NOT NULL
     in a THB enum, asset = 'THB', entries of one operation sum to zero) and points (FEAT-05:
     DEBIT/CREDIT per point type and holder). No point row could be written. Points move to
     point_ledger_entry; the FEAT-05 columns leave ledger_entry, which stays THB only.
     T16: DEBIT = CREDIT per operation, checked at COMMIT by a deferred constraint trigger
     (the reference only queried it).

  3. 0002 dropped the reservation rules:
       T8   uq_reservation_exclusive   one open reservation per single item (COUPON_UNIT,
            LISTING, ESCROW). POINT_BALANCE is left out on purpose: a balance can back many
            reservations at once (RFD 9 Q4). Both dialects.
       T7   ck_reservation_point_subject  POINT_BALANCE subject is
            <point_type_id>|<MERCHANT|CUSTOMER>|<holder_id>. PostgreSQL only (regex).
       T18  trg_reservation_release_timing  a reservation is released only once its operation is
            FINALIZED or CHAIN_REVERTED (MANUAL_REVIEW keeps it held), and stays released.
            PostgreSQL only (trigger).

The balance check behind T3-T5 needs the lot model and comes with 0008.

Upgrading fails if a stored amount is not a valid uint256, or if ledger_entry holds point rows:

    SELECT id, amount FROM reservation
     WHERE amount !~ '^[1-9][0-9]{0,77}$';
    SELECT count(*) FROM ledger_entry WHERE point_type_id IS NOT NULL OR direction IS NOT NULL;
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_feat05_reservation_ledger"
down_revision = "0006_feat04_point_type"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
AMOUNT = sa.String(78)
SINGLE_ITEM_OPEN = "released_at IS NULL AND subject_type IN ('COUPON_UNIT', 'LISTING', 'ESCROW')"
POINT_SUBJECT = (
    "subject_type <> 'POINT_BALANCE' OR subject_id ~ '^[^|]+\\|(MERCHANT|CUSTOMER)\\|[^|]+$'"
)

PG_UP = r"""
-- A decimal string that is a valid uint256: digits only, no leading zero, at most 2^256 - 1.
CREATE FUNCTION f_is_uint256(p_value text, p_positive boolean) RETURNS boolean
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE
    WHEN p_value IS NULL OR p_value !~ '^(0|[1-9][0-9]{0,77})$' THEN false
    WHEN p_positive AND p_value = '0' THEN false
    ELSE p_value::numeric
         <= 115792089237316195423570985008687907853269984665640564039457584007913129639935
  END
$$;

ALTER TABLE reservation
  ADD CONSTRAINT ck_reservation_amount_uint256 CHECK (f_is_uint256(amount, true));
ALTER TABLE point_ledger_entry
  ADD CONSTRAINT ck_point_ledger_amount_uint256 CHECK (f_is_uint256(amount, true));

CREATE FUNCTION f_reservation_release_timing() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_status text;
BEGIN
  IF OLD.released_at IS NOT NULL THEN
    IF NEW.released_at IS DISTINCT FROM OLD.released_at THEN
      RAISE EXCEPTION 'reservation % is already released', OLD.id
        USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_reservation_release_timing';
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.released_at IS NULL THEN RETURN NEW; END IF;
  SELECT status INTO v_status FROM operation WHERE id = NEW.operation_id;
  IF v_status IS DISTINCT FROM 'FINALIZED' AND v_status IS DISTINCT FROM 'CHAIN_REVERTED' THEN
    RAISE EXCEPTION 'reservation %: operation % is %, release waits until it ends',
      NEW.id, NEW.operation_id, v_status
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_reservation_release_timing',
            HINT = 'MANUAL_REVIEW keeps the amount held until a person decides';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_reservation_release_timing
  BEFORE UPDATE ON reservation
  FOR EACH ROW EXECUTE FUNCTION f_reservation_release_timing();

CREATE FUNCTION f_point_ledger_balanced() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_op text; v_debit numeric; v_credit numeric;
BEGIN
  v_op := CASE WHEN TG_OP = 'DELETE' THEN OLD.operation_id ELSE NEW.operation_id END;
  SELECT coalesce(sum(amount::numeric) FILTER (WHERE direction = 'DEBIT'), 0),
         coalesce(sum(amount::numeric) FILTER (WHERE direction = 'CREDIT'), 0)
    INTO v_debit, v_credit
    FROM point_ledger_entry WHERE operation_id = v_op;
  IF v_debit <> v_credit THEN
    RAISE EXCEPTION 'operation %: point ledger DEBIT % <> CREDIT %', v_op, v_debit, v_credit
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_point_ledger_balanced';
  END IF;
  RETURN NULL;
END $$;

CREATE CONSTRAINT TRIGGER trg_point_ledger_balanced
  AFTER INSERT OR UPDATE OR DELETE ON point_ledger_entry
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION f_point_ledger_balanced();
"""

PG_DOWN = """
DROP TRIGGER trg_point_ledger_balanced ON point_ledger_entry;
DROP FUNCTION f_point_ledger_balanced();
DROP TRIGGER trg_reservation_release_timing ON reservation;
DROP FUNCTION f_reservation_release_timing();
ALTER TABLE point_ledger_entry DROP CONSTRAINT ck_point_ledger_amount_uint256;
ALTER TABLE reservation DROP CONSTRAINT ck_reservation_amount_uint256;
DROP FUNCTION f_is_uint256(text, boolean);
"""


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "point_ledger_entry",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("operation_id", sa.Text(), sa.ForeignKey("operation.id"), nullable=False),
        sa.Column("entry_seq", sa.Integer(), nullable=False),
        sa.Column("point_type_id", sa.Text(), sa.ForeignKey("point_type.id"), nullable=False),
        sa.Column("holder_kind", sa.String(40), nullable=False),
        sa.Column("holder_id", sa.Text(), nullable=False),
        sa.Column("lot_key", sa.String(78), nullable=True),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("amount", AMOUNT, nullable=False),
        sa.Column("chain_event_id", sa.Text(), sa.ForeignKey("chain_event.id"), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("entry_seq >= 1", name="ck_point_ledger_seq_min"),
        sa.CheckConstraint(
            "holder_kind IN ('MERCHANT', 'CUSTOMER', 'ISSUANCE', 'INVENTORY')",
            name="ck_point_ledger_holder_kind_enum",
        ),
        sa.CheckConstraint(
            "direction IN ('DEBIT', 'CREDIT')", name="ck_point_ledger_direction_enum"
        ),
        sa.UniqueConstraint("operation_id", "entry_seq", name="uq_point_ledger_seq"),
    )

    # The FEAT-05 columns that 0002 merged into the THB ledger.
    with op.batch_alter_table("ledger_entry") as batch:
        batch.drop_constraint("ck_ledger_entry_holder_kind_enum", type_="check")
        batch.drop_constraint("ck_ledger_entry_direction_enum", type_="check")
        for column in ("point_type_id", "holder_kind", "holder_id", "direction", "chain_event_id"):
            batch.drop_column(column)

    op.create_index(
        "uq_reservation_exclusive",
        "reservation",
        ["subject_type", "subject_id"],
        unique=True,
        postgresql_where=sa.text(SINGLE_ITEM_OPEN),
        sqlite_where=sa.text(SINGLE_ITEM_OPEN),
    )

    if _is_postgresql():
        op.create_check_constraint("ck_reservation_point_subject", "reservation", POINT_SUBJECT)
        op.execute(PG_UP)


def downgrade() -> None:
    if _is_postgresql():
        op.execute(PG_DOWN)
        op.drop_constraint("ck_reservation_point_subject", "reservation", type_="check")

    op.drop_index("uq_reservation_exclusive", table_name="reservation")

    with op.batch_alter_table("ledger_entry") as batch:
        batch.add_column(sa.Column("point_type_id", sa.Text(), nullable=True))
        # Named as PostgreSQL names the 0002 one, so both dialects end up identical.
        batch.create_foreign_key(
            "ledger_entry_point_type_id_fkey", "point_type", ["point_type_id"], ["id"]
        )
        batch.add_column(sa.Column("holder_kind", sa.String(40), nullable=True))
        batch.add_column(sa.Column("holder_id", sa.Text(), nullable=True))
        batch.add_column(sa.Column("direction", sa.String(40), nullable=True))
        batch.add_column(sa.Column("chain_event_id", sa.Text(), nullable=True))
        batch.create_check_constraint(
            "ck_ledger_entry_holder_kind_enum",
            "holder_kind IS NULL OR holder_kind IN ('MERCHANT', 'CUSTOMER')",
        )
        batch.create_check_constraint(
            "ck_ledger_entry_direction_enum",
            "direction IS NULL OR direction IN ('DEBIT', 'CREDIT')",
        )

    op.drop_table("point_ledger_entry")
