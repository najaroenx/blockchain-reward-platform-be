"""FEAT-05 round 2: point balances as the contract's lots and inventory (BE-0512, BE-0513 schema).

reference/feat-05 keeps balances in point_balance_bucket, one row per holder per epoch, with
expires_at derived from point_type_chain's epoch constants. NewPointToken v3 has no epochs
(see 0006 and docs/reference-findings.md); it has lots and, for business D, inventory:

  point_lot          one row per lot, from LotCreated(lotId, startDate, expireDate). Business A
                     has no lots: its program window is a single row with lot_key 'A', written
                     when BusinessConfigured(1, (start, expire)) is applied, so every model is
                     read the same way.
  point_lot_balance  what each holder has in each lot, from LotMinted, LotTransferred,
                     LotRedeemed and CustomerDelivered (A: CustomerMinted / Transfer on lot 'A').
                     T10: one row per (holder, lot).
  point_inventory    D's points waiting to be delivered, from InventoryMinted and
                     CustomerDelivered. Inventory does not age: expiry starts at delivery.

All three are projections: they can be truncated and rebuilt from chain_event. point_balance_bucket
is dropped. Its one dependant, FEAT-06's expiry_notice, now points at the holder's lot balance
instead of a bucket, with the same rule: one notice per (holder, lot) per threshold (uq_notice).
FEAT-06's own rules on that table are left for the FEAT-06 round.

Available balance at time t (f_available_points, T2), net of open reservations:
  CUSTOMER  sum of lots that are Active at t: start_at <= t, and t < expire_at, except that A, B
            and C are still Active at exactly expire_at while D is already Expired (contract v3
            table "ที่ expireDate พอดี"; T2b). Pending (not started) and Expired lots don't count.
  MERCHANT  the merchant's inventory (D). A, B and C mint new points per issue, so the merchant
            has no balance to reserve against and the application does not reserve one.

trg_point_reservation_balance (T3-T5, T4b, T7b) refuses a POINT_BALANCE reservation larger than
that, holding an advisory lock on the one subject so concurrent reservations on it queue while
others don't wait. f_points_fefo (T17) lists Active lots in the contract's FEFO order: expire_at,
then the smaller lot id, compared as numbers.

PostgreSQL only: the functions, the trigger, and the uint256 / lot-key format CHECKs. The tables,
keys and the lot-window CHECK exist on both dialects.

Upgrading drops point_balance_bucket and its rows, and expiry_notice rows (they name buckets);
nothing reads either yet.

    SELECT (SELECT count(*) FROM point_balance_bucket), (SELECT count(*) FROM expiry_notice);
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_feat05_lots_inventory"
down_revision = "0007_feat05_reservation_ledger"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
AMOUNT = sa.String(78)

PG_UP = r"""
ALTER TABLE point_lot ADD CONSTRAINT ck_point_lot_key_format
  CHECK (lot_key = 'A' OR f_is_uint256(lot_key, false));
ALTER TABLE point_lot_balance ADD CONSTRAINT ck_point_lot_balance_amount_uint256
  CHECK (f_is_uint256(amount, false));
ALTER TABLE point_inventory ADD CONSTRAINT ck_point_inventory_amount_uint256
  CHECK (f_is_uint256(amount, false));

-- Active at p_at, per NewPointToken v3: A, B, C (1-3) still Active at exactly expire; D (4) not.
CREATE FUNCTION f_point_lot_active(p_business smallint, p_start timestamptz,
                                   p_expire timestamptz, p_at timestamptz)
RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT p_start <= p_at
     AND (p_at < p_expire OR (p_at = p_expire AND p_business IS DISTINCT FROM 4))
$$;

-- Sort key of a lot: A first (it is the only lot of its token), then the lot id as a number.
CREATE FUNCTION f_point_lot_order(p_lot_key text) RETURNS numeric
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE WHEN p_lot_key = 'A' THEN -1 ELSE p_lot_key::numeric END
$$;

CREATE FUNCTION f_available_points(p_point_type_id text, p_holder_kind text,
                                   p_holder_id text, p_at timestamptz)
RETURNS numeric LANGUAGE plpgsql STABLE AS $$
DECLARE v_total numeric; v_held numeric;
BEGIN
  IF p_holder_kind = 'CUSTOMER' THEN
    SELECT coalesce(sum(b.amount::numeric), 0) INTO v_total
      FROM point_lot_balance b
      JOIN point_lot l USING (point_type_id, lot_key)
      LEFT JOIN point_type_chain c ON c.point_type_id = b.point_type_id
     WHERE b.point_type_id = p_point_type_id
       AND b.holder_kind = 'CUSTOMER' AND b.holder_id = p_holder_id
       AND f_point_lot_active(c.business_model, l.start_at, l.expire_at, p_at);
  ELSE
    SELECT coalesce(sum(amount::numeric), 0) INTO v_total
      FROM point_inventory
     WHERE point_type_id = p_point_type_id
       AND holder_kind = p_holder_kind AND holder_id = p_holder_id;
  END IF;

  SELECT coalesce(sum(amount::numeric), 0) INTO v_held
    FROM reservation
   WHERE subject_type = 'POINT_BALANCE' AND released_at IS NULL
     AND subject_id = p_point_type_id || '|' || p_holder_kind || '|' || p_holder_id;

  RETURN v_total - v_held;
END $$;

CREATE FUNCTION f_points_fefo(p_point_type_id text, p_holder_kind text,
                              p_holder_id text, p_at timestamptz)
RETURNS TABLE (ord bigint, lot_key text, amount numeric, expire_at timestamptz)
LANGUAGE sql STABLE AS $$
  SELECT row_number() OVER (ORDER BY l.expire_at, f_point_lot_order(l.lot_key)),
         l.lot_key, b.amount::numeric, l.expire_at
    FROM point_lot_balance b
    JOIN point_lot l USING (point_type_id, lot_key)
    LEFT JOIN point_type_chain c ON c.point_type_id = b.point_type_id
   WHERE b.point_type_id = p_point_type_id
     AND b.holder_kind = p_holder_kind AND b.holder_id = p_holder_id
     AND b.amount <> '0'
     AND f_point_lot_active(c.business_model, l.start_at, l.expire_at, p_at)
   ORDER BY 1
$$;

CREATE FUNCTION f_point_reservation_balance() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_pt text; v_kind text; v_holder text; v_avail numeric;
BEGIN
  IF NEW.subject_type <> 'POINT_BALANCE' THEN RETURN NEW; END IF;
  -- A malformed amount or subject is ck_reservation_amount_uint256's and
  -- ck_reservation_point_subject's to reject, with their own names.
  IF NOT f_is_uint256(NEW.amount, true)
     OR NEW.subject_id !~ '^[^|]+\|(MERCHANT|CUSTOMER)\|[^|]+$' THEN
    RETURN NEW;
  END IF;
  v_pt     := split_part(NEW.subject_id, '|', 1);
  v_kind   := split_part(NEW.subject_id, '|', 2);
  v_holder := split_part(NEW.subject_id, '|', 3);

  IF NOT EXISTS (SELECT 1 FROM point_type WHERE id = v_pt) THEN
    RAISE EXCEPTION 'reservation %: point type % does not exist', NEW.id, v_pt
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_point_reservation_balance';
  END IF;

  PERFORM pg_advisory_xact_lock(hashtextextended(NEW.subject_id, 0));
  v_avail := f_available_points(v_pt, v_kind, v_holder, now());
  IF NEW.amount::numeric > v_avail THEN
    RAISE EXCEPTION 'reservation %: % needs %, % available', NEW.id, NEW.subject_id,
      NEW.amount, v_avail
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_point_reservation_balance';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_point_reservation_balance
  BEFORE INSERT ON reservation
  FOR EACH ROW EXECUTE FUNCTION f_point_reservation_balance();
"""

PG_DOWN = """
DROP TRIGGER trg_point_reservation_balance ON reservation;
DROP FUNCTION f_point_reservation_balance();
DROP FUNCTION f_points_fefo(text, text, text, timestamptz);
DROP FUNCTION f_available_points(text, text, text, timestamptz);
DROP FUNCTION f_point_lot_order(text);
DROP FUNCTION f_point_lot_active(smallint, timestamptz, timestamptz, timestamptz);
"""


_NOTICE_LOT_COLUMNS = (
    ("point_type_id", sa.Text()),
    ("holder_kind", sa.String(40)),
    ("holder_id", sa.Text()),
    ("lot_key", sa.String(78)),
)


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "point_lot",
        sa.Column("point_type_id", sa.Text(), sa.ForeignKey("point_type.id"), primary_key=True),
        sa.Column("lot_key", sa.String(78), primary_key=True),
        sa.Column("start_at", TS, nullable=False),
        sa.Column("expire_at", TS, nullable=False),
        sa.Column("chain_event_id", sa.Text(), sa.ForeignKey("chain_event.id"), nullable=True),
        sa.Column("applied_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("expire_at > start_at", name="ck_point_lot_window"),
    )
    op.create_table(
        "point_lot_balance",
        sa.Column("point_type_id", sa.Text(), primary_key=True),
        sa.Column("holder_kind", sa.String(40), primary_key=True),
        sa.Column("holder_id", sa.Text(), primary_key=True),
        sa.Column("lot_key", sa.String(78), primary_key=True),
        sa.Column("amount", AMOUNT, nullable=False),
        sa.Column("as_of_block", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "holder_kind IN ('MERCHANT', 'CUSTOMER')", name="ck_point_lot_balance_holder_kind_enum"
        ),
        sa.ForeignKeyConstraint(
            ["point_type_id", "lot_key"],
            ["point_lot.point_type_id", "point_lot.lot_key"],
            name="fk_point_lot_balance_lot",
        ),
    )
    op.create_table(
        "point_inventory",
        sa.Column("point_type_id", sa.Text(), sa.ForeignKey("point_type.id"), primary_key=True),
        sa.Column("holder_kind", sa.String(40), primary_key=True),
        sa.Column("holder_id", sa.Text(), primary_key=True),
        sa.Column("amount", AMOUNT, nullable=False),
        sa.Column("as_of_block", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("holder_kind = 'MERCHANT'", name="ck_point_inventory_holder_kind"),
    )
    with op.batch_alter_table("point_ledger_entry") as batch:
        batch.create_foreign_key(
            "fk_point_ledger_lot",
            "point_lot",
            ["point_type_id", "lot_key"],
            ["point_type_id", "lot_key"],
        )

    # expiry_notice named a bucket; it now names the holder's balance in a lot.
    op.execute("DELETE FROM expiry_notice")
    with op.batch_alter_table("expiry_notice") as batch:
        batch.drop_constraint("uq_notice", type_="unique")
        batch.drop_column("bucket_id")
        for name, column_type in _NOTICE_LOT_COLUMNS:
            batch.add_column(sa.Column(name, column_type, nullable=False))
        batch.create_foreign_key(
            "fk_expiry_notice_lot_balance",
            "point_lot_balance",
            ["point_type_id", "holder_kind", "holder_id", "lot_key"],
            ["point_type_id", "holder_kind", "holder_id", "lot_key"],
            ondelete="CASCADE",
        )
        batch.create_unique_constraint(
            "uq_notice",
            ["point_type_id", "holder_kind", "holder_id", "lot_key", "threshold_days"],
        )
    op.drop_table("point_balance_bucket")

    if _is_postgresql():
        op.execute(PG_UP)


def downgrade() -> None:
    if _is_postgresql():
        op.execute(PG_DOWN)

    op.create_table(
        "point_balance_bucket",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("point_type_id", sa.Text(), sa.ForeignKey("point_type.id"), nullable=False),
        sa.Column("holder_kind", sa.String(40), nullable=False),
        sa.Column("holder_id", sa.Text(), nullable=False),
        sa.Column("epoch_index", sa.Integer(), nullable=False),
        sa.Column("amount", AMOUNT, nullable=False),
        sa.Column("received_at", TS, nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "holder_kind IN ('MERCHANT', 'CUSTOMER')", name="ck_point_bucket_holder_kind_enum"
        ),
        sa.CheckConstraint("epoch_index >= 0", name="ck_point_bucket_epoch_nonneg"),
        sa.CheckConstraint("expires_at > received_at", name="ck_bucket_expiry"),
        sa.UniqueConstraint(
            "point_type_id", "holder_kind", "holder_id", "epoch_index", name="uq_bucket"
        ),
    )
    op.execute("DELETE FROM expiry_notice")
    with op.batch_alter_table("expiry_notice") as batch:
        batch.drop_constraint("uq_notice", type_="unique")
        batch.drop_constraint("fk_expiry_notice_lot_balance", type_="foreignkey")
        for name, _ in reversed(_NOTICE_LOT_COLUMNS):
            batch.drop_column(name)
        batch.add_column(sa.Column("bucket_id", sa.Text(), nullable=False))
        # Named as PostgreSQL names the 0002 one, so both dialects end up identical.
        batch.create_foreign_key(
            "expiry_notice_bucket_id_fkey",
            "point_balance_bucket",
            ["bucket_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_unique_constraint("uq_notice", ["bucket_id", "threshold_days"])
    with op.batch_alter_table("point_ledger_entry") as batch:
        batch.drop_constraint("fk_point_ledger_lot", type_="foreignkey")
    op.drop_table("point_inventory")
    op.drop_table("point_lot_balance")
    op.drop_table("point_lot")
