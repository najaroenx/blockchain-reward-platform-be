"""FEAT-07 create coupon type and issue codes: the reference guarantees, aligned to the Coupon ABI.

Corrections to reference/feat-07 (docs/reference-findings.md has the evidence):

  1. The delivered contract is
     `createCouponType(string name, uint256 startDate, uint256 expireDate)`
     (src/marketplace/infrastructure/blockchain/abi/Coupon.json). The pack has no start date, so
     the service had nothing to send as startDate. coupon_type.starts_at is added (existing rows
     take created_at) with ck_coupon_type_window: expires_at > starts_at.

  2. The pack's f_issue_within_supply counts every live code of the type again for every inserted
     row: issuing the maximum 1,000,000 codes reads about 5 * 10^11 index entries. It also only
     runs on INSERT, so turning a VOIDED code back into ISSUED walks past the supply, and nothing
     stops total_supply from being lowered under what was already issued. Here the count is a
     column, coupon_type.issued_count, kept by statement-level triggers on coupon_code (one UPDATE
     per type per statement, through transition tables), and the rule is a plain CHECK:
     ck_coupon_type_within_supply  issued_count BETWEEN 0 AND total_supply. Updating the coupon_type
     row also serialises concurrent issues of the same type (the second waits on the row lock and
     then sees the first one's count). trg_coupon_issued_count_guard refuses issued_count written by
     anything but those triggers.

  3. The pack's immutability trigger covered chain_type_id only; here every chain-sourced column of
     coupon_type_chain is fixed (trg_coupon_chain_immutable), and chain_type_id must be a uint256
     string (ck_coupon_type_chain_type_id_uint256).

  4. "LIVE has a chain row" was a report query in the pack (T20). trg_coupon_live_has_chain makes it
     a rule, checked at commit so the projector can write coupon_type and coupon_type_chain in
     either order inside one transaction.

Then the reference rules that 0002 left out:

  both dialects
    T3  uq_coupon_type_name (merchant_id, name_normalized) WHERE status <> 'FAILED', with
        ck_coupon_type_name_normalized: name_normalized = lower(trim(name)) (the 0006 pattern;
        write it as lower(trim(:name)) in SQL)
    T4  a FAILED deploy releases its name (the WHERE above)
    T15 uq_coupon_deploy_inflight  one unfinished COUPON_TYPE_CREATE per coupon type
    T7  ck_coupon_type_within_supply (see 2)
  PostgreSQL only
    T2  trg_coupon_type_merchant_active  f_merchant_must_be_active from 0006
    T6  ck_code_ref_derived  code_ref = '0x' || hex(sha256(id)): the projector derives the reference
        from the row id, and the plaintext code never reaches the chain or this table
    T19 ck_coupon_type_face_value_uint256  f_is_uint256 from 0007 (>= 0, as in the pack)
    T13 trg_coupon_chain_immutable, ck_coupon_type_chain_type_id_uint256
    T20 trg_coupon_live_has_chain
    T7  f_coupon_issued_count + trg_coupon_issued_insert / _update / _delete,
        trg_coupon_issued_count_guard

Upgrading fails if existing rows break a new rule: two live coupon types of one merchant whose names
differ only in case or surrounding spaces, more live codes than total_supply, a code_ref that is not
sha256(id), a LIVE type without a chain row, or a face value / chain type id that is not a uint256.

    SELECT merchant_id, lower(trim(name)), count(*) FROM coupon_type WHERE status <> 'FAILED'
     GROUP BY 1, 2 HAVING count(*) > 1;
"""

import sqlalchemy as sa
from alembic import op

revision = "0010_feat07_coupon_type"
down_revision = "0009_feat06_balance_reads"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)

NOT_FAILED = "status <> 'FAILED'"
DEPLOY_INFLIGHT = "kind = 'COUPON_TYPE_CREATE' AND status NOT IN ('FINALIZED', 'CHAIN_REVERTED')"
WITHIN_SUPPLY = "issued_count >= 0 AND issued_count <= total_supply"

BACKFILL = """
UPDATE coupon_type
   SET name_normalized = lower(trim(name)),
       starts_at = created_at,
       issued_count = (SELECT count(*) FROM coupon_code c
                        WHERE c.coupon_type_id = coupon_type.id AND c.state <> 'VOIDED')
"""

PG_UP = r"""
ALTER TABLE coupon_type ADD CONSTRAINT ck_coupon_type_face_value_uint256
  CHECK (f_is_uint256(face_value, false));
ALTER TABLE coupon_type_chain ADD CONSTRAINT ck_coupon_type_chain_type_id_uint256
  CHECK (f_is_uint256(chain_type_id, false));
ALTER TABLE coupon_code ADD CONSTRAINT ck_code_ref_derived
  CHECK (code_ref = '0x' || encode(sha256(convert_to(id, 'UTF8')), 'hex'));

CREATE TRIGGER trg_coupon_type_merchant_active
  BEFORE INSERT ON coupon_type
  FOR EACH ROW EXECUTE FUNCTION f_merchant_must_be_active();

CREATE FUNCTION f_coupon_chain_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.coupon_type_id IS DISTINCT FROM OLD.coupon_type_id
     OR NEW.contract_address IS DISTINCT FROM OLD.contract_address
     OR NEW.chain_type_id IS DISTINCT FROM OLD.chain_type_id
     OR NEW.deployed_block IS DISTINCT FROM OLD.deployed_block
     OR NEW.deployed_tx_hash IS DISTINCT FROM OLD.deployed_tx_hash THEN
    RAISE EXCEPTION 'coupon_type_chain %: values read from the chain cannot change',
      OLD.coupon_type_id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_coupon_chain_immutable',
            HINT = 'a new deployment is a new coupon_type row, not an edit of this one';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_coupon_chain_immutable
  BEFORE UPDATE ON coupon_type_chain
  FOR EACH ROW EXECUTE FUNCTION f_coupon_chain_immutable();

-- T20 · checked at commit: the projector may write either table first
CREATE FUNCTION f_coupon_live_has_chain() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_id text;
BEGIN
  IF TG_TABLE_NAME = 'coupon_type' THEN
    v_id := NEW.id;
  ELSE
    v_id := OLD.coupon_type_id;
  END IF;
  IF EXISTS (SELECT 1 FROM coupon_type t
              WHERE t.id = v_id AND t.status = 'LIVE'
                AND NOT EXISTS (SELECT 1 FROM coupon_type_chain c
                                 WHERE c.coupon_type_id = t.id)) THEN
    RAISE EXCEPTION 'coupon_type % is LIVE without a coupon_type_chain row', v_id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_coupon_live_has_chain',
            HINT = 'LIVE is set by the projector from CouponTypeCreated, with the chain row';
  END IF;
  RETURN NULL;
END $$;

CREATE CONSTRAINT TRIGGER trg_coupon_live_has_chain
  AFTER INSERT OR UPDATE OF status ON coupon_type
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION f_coupon_live_has_chain();

CREATE CONSTRAINT TRIGGER trg_coupon_chain_keeps_live
  AFTER DELETE ON coupon_type_chain
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION f_coupon_live_has_chain();

-- T7 · issued_count follows coupon_code, one UPDATE per type per statement.
-- Types are updated in id order so two statements touching the same types cannot deadlock.
CREATE FUNCTION f_coupon_issued_count() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r record;
BEGIN
  IF TG_OP = 'INSERT' THEN
    FOR r IN SELECT coupon_type_id AS id, count(*)::int AS d FROM new_rows
              WHERE state <> 'VOIDED' GROUP BY 1 ORDER BY 1 LOOP
      UPDATE coupon_type SET issued_count = issued_count + r.d WHERE id = r.id;
    END LOOP;
  ELSIF TG_OP = 'DELETE' THEN
    FOR r IN SELECT coupon_type_id AS id, count(*)::int AS d FROM old_rows
              WHERE state <> 'VOIDED' GROUP BY 1 ORDER BY 1 LOOP
      UPDATE coupon_type SET issued_count = issued_count - r.d WHERE id = r.id;
    END LOOP;
  ELSE
    FOR r IN SELECT id, sum(d)::int AS d FROM (
               SELECT coupon_type_id AS id, 1 AS d FROM new_rows WHERE state <> 'VOIDED'
               UNION ALL
               SELECT coupon_type_id, -1 FROM old_rows WHERE state <> 'VOIDED') x
              GROUP BY id HAVING sum(d) <> 0 ORDER BY id LOOP
      UPDATE coupon_type SET issued_count = issued_count + r.d WHERE id = r.id;
    END LOOP;
  END IF;
  RETURN NULL;
END $$;

CREATE TRIGGER trg_coupon_issued_insert
  AFTER INSERT ON coupon_code REFERENCING NEW TABLE AS new_rows
  FOR EACH STATEMENT EXECUTE FUNCTION f_coupon_issued_count();
CREATE TRIGGER trg_coupon_issued_update
  AFTER UPDATE ON coupon_code REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows
  FOR EACH STATEMENT EXECUTE FUNCTION f_coupon_issued_count();
CREATE TRIGGER trg_coupon_issued_delete
  AFTER DELETE ON coupon_code REFERENCING OLD TABLE AS old_rows
  FOR EACH STATEMENT EXECUTE FUNCTION f_coupon_issued_count();

-- only f_coupon_issued_count (trigger depth 2) may write the counter
CREATE FUNCTION f_coupon_issued_count_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF pg_trigger_depth() < 2
     AND ((TG_OP = 'INSERT' AND NEW.issued_count <> 0)
       OR (TG_OP = 'UPDATE' AND NEW.issued_count IS DISTINCT FROM OLD.issued_count)) THEN
    RAISE EXCEPTION 'coupon_type %: issued_count is kept by the database', NEW.id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_coupon_issued_count_guard',
            HINT = 'insert or void coupon_code rows instead';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_coupon_issued_count_guard
  BEFORE INSERT OR UPDATE OF issued_count ON coupon_type
  FOR EACH ROW EXECUTE FUNCTION f_coupon_issued_count_guard();
"""

PG_DOWN = """
DROP TRIGGER trg_coupon_issued_count_guard ON coupon_type;
DROP FUNCTION f_coupon_issued_count_guard();
DROP TRIGGER trg_coupon_issued_delete ON coupon_code;
DROP TRIGGER trg_coupon_issued_update ON coupon_code;
DROP TRIGGER trg_coupon_issued_insert ON coupon_code;
DROP FUNCTION f_coupon_issued_count();
DROP TRIGGER trg_coupon_chain_keeps_live ON coupon_type_chain;
DROP TRIGGER trg_coupon_live_has_chain ON coupon_type;
DROP FUNCTION f_coupon_live_has_chain();
DROP TRIGGER trg_coupon_chain_immutable ON coupon_type_chain;
DROP FUNCTION f_coupon_chain_immutable();
DROP TRIGGER trg_coupon_type_merchant_active ON coupon_type;
ALTER TABLE coupon_code DROP CONSTRAINT ck_code_ref_derived;
ALTER TABLE coupon_type_chain DROP CONSTRAINT ck_coupon_type_chain_type_id_uint256;
ALTER TABLE coupon_type DROP CONSTRAINT ck_coupon_type_face_value_uint256;
"""


def _partial(where: str) -> dict[str, sa.TextClause]:
    return {"postgresql_where": sa.text(where), "sqlite_where": sa.text(where)}


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def upgrade() -> None:
    with op.batch_alter_table("coupon_type") as batch:
        batch.add_column(sa.Column("name_normalized", sa.Text(), nullable=True))
        batch.add_column(sa.Column("starts_at", TS, nullable=True, server_default=sa.func.now()))
        batch.add_column(
            sa.Column("issued_count", sa.Integer(), nullable=False, server_default="0")
        )

    op.execute(BACKFILL)

    with op.batch_alter_table("coupon_type") as batch:
        batch.alter_column("name_normalized", nullable=False, existing_type=sa.Text())
        batch.alter_column(
            "starts_at", nullable=False, existing_type=TS, existing_server_default=sa.func.now()
        )
        batch.create_check_constraint(
            "ck_coupon_type_name_normalized", "name_normalized = lower(trim(name))"
        )
        batch.create_check_constraint("ck_coupon_type_window", "expires_at > starts_at")
        batch.create_check_constraint("ck_coupon_type_within_supply", WITHIN_SUPPLY)

    op.create_index(
        "uq_coupon_type_name",
        "coupon_type",
        ["merchant_id", "name_normalized"],
        unique=True,
        **_partial(NOT_FAILED),
    )
    op.create_index(
        "uq_coupon_deploy_inflight",
        "operation",
        ["subject_id"],
        unique=True,
        **_partial(DEPLOY_INFLIGHT),
    )

    if _is_postgresql():
        op.execute(PG_UP)


def downgrade() -> None:
    if _is_postgresql():
        op.execute(PG_DOWN)

    op.drop_index("uq_coupon_deploy_inflight", table_name="operation")
    op.drop_index("uq_coupon_type_name", table_name="coupon_type")

    with op.batch_alter_table("coupon_type") as batch:
        for name in (
            "ck_coupon_type_within_supply",
            "ck_coupon_type_window",
            "ck_coupon_type_name_normalized",
        ):
            batch.drop_constraint(name, type_="check")
        for column in ("issued_count", "starts_at", "name_normalized"):
            batch.drop_column(column)
