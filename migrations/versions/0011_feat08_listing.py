"""FEAT-08 list on marketplace: the reference guarantees, aligned to the Marketplace ABI.

Corrections to reference/feat-08 (docs/reference-findings.md has the evidence):

  1. The delivered Marketplace (src/marketplace/infrastructure/blockchain/abi/Marketplace.json) has
     `listCoupon(typeId, amount, pricePerUnit, paymentToken)`, preceded by the Coupon token's
     ERC-1155 `setApprovalForAll`. There is no approveCoupon / createListing / lockInVault; the
     Vault locks the buyer's THB at purchase (FEAT-10), not coupons. Nothing in the schema names the
     steps; the acceptance test uses the real ones.

  2. On chain a listing is an amount of a coupon type. listing_item stays: it is the backend's own
     record of which of the seller's codes are set aside for the listing (FEAT-11 needs it to know
     whose code was redeemed), and its row count must equal the listed amount.

  3. The pack's quantity trigger only refused *more* items than `quantity`. Here
     trg_listing_item_max refuses the extra item at once (T3) and trg_listing_item_count, checked
     at commit, requires exactly `quantity` items (T3b).

  4. Nothing stopped one code from sitting in two open listings unless the service also wrote a
     COUPON_UNIT reservation. trg_listing_item_valid now refuses a code that is already in another
     PENDING or ACTIVE listing (T9b); it locks the code's row first, so two transactions listing
     the same code queue instead of both passing. It also refuses items once the listing has left
     PENDING (T2b).

  5. The pack's trg_listing_chain_immutable covered two columns. Here every chain-sourced column is
     fixed, and the close columns (closed_block, closed_tx_hash, close_reason, already in 0002) may
     be written once (T17).

  6. "ACTIVE has a chain row" was a report query (T23). trg_listing_active_has_chain makes it a
     rule, checked at commit.

Then the reference rules that 0002 left out:

  both dialects
    T19 uq_listing_inflight  one unfinished LISTING_CREATE / LISTING_DELETE per listing
  PostgreSQL only
    T4-T7 T9b T2b trg_listing_item_valid  the code is HELD by the seller, of the listing's type,
          not expired, not in another open listing; the listing is still PENDING
    T3 T3b trg_listing_item_max, trg_listing_item_count
    T8 T20 ck_listing_unit_price_uint256  a positive uint256 (f_is_uint256 from 0007)
    T17 T17b trg_listing_chain_immutable, ck_listing_chain_id_uint256
    T23 trg_listing_active_has_chain

Upgrading fails if existing rows break a new CHECK (price or chain listing id not a uint256) or
two unfinished listing operations share a subject. The triggers do not re-check existing items.

    SELECT subject_id, count(*) FROM operation
     WHERE kind IN ('LISTING_CREATE', 'LISTING_DELETE')
       AND status NOT IN ('FINALIZED', 'CHAIN_REVERTED') GROUP BY 1 HAVING count(*) > 1;
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_feat08_listing"
down_revision = "0010_feat07_coupon_type"
branch_labels = None
depends_on = None

LISTING_INFLIGHT = (
    "kind IN ('LISTING_CREATE', 'LISTING_DELETE') AND status NOT IN ('FINALIZED', 'CHAIN_REVERTED')"
)

PG_UP = r"""
ALTER TABLE listing ADD CONSTRAINT ck_listing_unit_price_uint256
  CHECK (f_is_uint256(unit_price, true));
ALTER TABLE listing_chain ADD CONSTRAINT ck_listing_chain_id_uint256
  CHECK (f_is_uint256(chain_listing_id, false));

-- T2b T4-T7 T9b · RFD 12 F5: the database decides, not a guard in code
CREATE FUNCTION f_listing_item_valid() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  c  record;
  l  record;
  v_other text;
BEGIN
  -- lock the code first: two transactions listing the same code queue here
  SELECT cc.state, cc.holder_kind, cc.holder_id, cc.coupon_type_id, t.expires_at
    INTO c
    FROM coupon_code cc JOIN coupon_type t ON t.id = cc.coupon_type_id
   WHERE cc.id = NEW.coupon_code_id
     FOR NO KEY UPDATE OF cc;
  SELECT seller_kind, seller_id, coupon_type_id, status INTO l
    FROM listing WHERE id = NEW.listing_id;

  IF l.status IS DISTINCT FROM 'PENDING' THEN
    RAISE EXCEPTION 'listing % is %: items are fixed once it leaves PENDING',
      NEW.listing_id, l.status
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_listing_item_valid';
  END IF;
  IF c.state IS DISTINCT FROM 'HELD' THEN
    RAISE EXCEPTION 'coupon_code % is %, only a HELD code can be listed',
      NEW.coupon_code_id, coalesce(c.state, 'missing')
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_listing_item_valid';
  END IF;
  IF c.holder_kind IS DISTINCT FROM l.seller_kind OR c.holder_id IS DISTINCT FROM l.seller_id THEN
    RAISE EXCEPTION 'coupon_code % is not held by the seller of listing %',
      NEW.coupon_code_id, NEW.listing_id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_listing_item_valid';
  END IF;
  IF c.coupon_type_id IS DISTINCT FROM l.coupon_type_id THEN
    RAISE EXCEPTION 'coupon_code % is of type %, listing % sells type %',
      NEW.coupon_code_id, c.coupon_type_id, NEW.listing_id, l.coupon_type_id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_listing_item_valid';
  END IF;
  IF c.expires_at <= now() THEN
    RAISE EXCEPTION 'coupon_code % expired at %', NEW.coupon_code_id, c.expires_at
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_listing_item_valid';
  END IF;
  SELECT i.listing_id INTO v_other
    FROM listing_item i JOIN listing o ON o.id = i.listing_id
   WHERE i.coupon_code_id = NEW.coupon_code_id AND i.listing_id <> NEW.listing_id
     AND o.status IN ('PENDING', 'ACTIVE')
   LIMIT 1;
  IF v_other IS NOT NULL THEN
    RAISE EXCEPTION 'coupon_code % is already in open listing %', NEW.coupon_code_id, v_other
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_listing_item_valid',
            HINT = 'RFD 12 D3 · one code, one open listing';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_listing_item_valid
  BEFORE INSERT OR UPDATE OF listing_id, coupon_code_id ON listing_item
  FOR EACH ROW EXECUTE FUNCTION f_listing_item_valid();

-- T3 · the extra item is refused at once
CREATE FUNCTION f_listing_item_max() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_q integer; v_n integer;
BEGIN
  SELECT quantity INTO v_q FROM listing WHERE id = NEW.listing_id FOR NO KEY UPDATE;
  SELECT count(*) INTO v_n FROM listing_item WHERE listing_id = NEW.listing_id;
  IF v_n > v_q THEN
    RAISE EXCEPTION 'listing % has % items, more than its quantity %', NEW.listing_id, v_n, v_q
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_listing_item_max';
  END IF;
  RETURN NULL;
END $$;

CREATE TRIGGER trg_listing_item_max
  AFTER INSERT OR UPDATE OF listing_id ON listing_item
  FOR EACH ROW EXECUTE FUNCTION f_listing_item_max();

-- T3b · at commit a listing holds exactly `quantity` items (the listed amount on chain)
CREATE FUNCTION f_listing_item_count() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_id text; v_q integer; v_n integer;
BEGIN
  IF TG_TABLE_NAME = 'listing' THEN
    v_id := NEW.id;
  ELSIF TG_OP = 'DELETE' THEN
    v_id := OLD.listing_id;
  ELSE
    v_id := NEW.listing_id;
  END IF;
  SELECT quantity INTO v_q FROM listing WHERE id = v_id;
  IF NOT FOUND THEN
    RETURN NULL;  -- the listing itself was deleted
  END IF;
  SELECT count(*) INTO v_n FROM listing_item WHERE listing_id = v_id;
  IF v_n <> v_q THEN
    RAISE EXCEPTION 'listing % has % items, its quantity is %', v_id, v_n, v_q
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_listing_item_count',
            HINT = 'quantity is the amount listed on chain: write every item with the listing';
  END IF;
  RETURN NULL;
END $$;

CREATE CONSTRAINT TRIGGER trg_listing_item_count
  AFTER INSERT OR UPDATE OF quantity ON listing
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION f_listing_item_count();
CREATE CONSTRAINT TRIGGER trg_listing_item_count
  AFTER INSERT OR DELETE OR UPDATE OF listing_id ON listing_item
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION f_listing_item_count();

-- T17 · chain-sourced columns are fixed; the close is written once
CREATE FUNCTION f_listing_chain_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.listing_id IS DISTINCT FROM OLD.listing_id
     OR NEW.contract_address IS DISTINCT FROM OLD.contract_address
     OR NEW.chain_listing_id IS DISTINCT FROM OLD.chain_listing_id
     OR NEW.created_block IS DISTINCT FROM OLD.created_block
     OR NEW.created_tx_hash IS DISTINCT FROM OLD.created_tx_hash
     OR (OLD.close_reason IS NOT NULL AND (
            NEW.close_reason IS DISTINCT FROM OLD.close_reason
         OR NEW.closed_block IS DISTINCT FROM OLD.closed_block
         OR NEW.closed_tx_hash IS DISTINCT FROM OLD.closed_tx_hash)) THEN
    RAISE EXCEPTION 'listing_chain %: values read from the chain cannot change', OLD.listing_id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_listing_chain_immutable',
            HINT = 'a new listing on chain is a new listing row';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_listing_chain_immutable
  BEFORE UPDATE ON listing_chain
  FOR EACH ROW EXECUTE FUNCTION f_listing_chain_immutable();

-- T23 · checked at commit: the projector may write either table first
CREATE FUNCTION f_listing_active_has_chain() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_id text;
BEGIN
  IF TG_TABLE_NAME = 'listing' THEN
    v_id := NEW.id;
  ELSE
    v_id := OLD.listing_id;
  END IF;
  IF EXISTS (SELECT 1 FROM listing l
              WHERE l.id = v_id AND l.status = 'ACTIVE'
                AND NOT EXISTS (SELECT 1 FROM listing_chain c WHERE c.listing_id = l.id)) THEN
    RAISE EXCEPTION 'listing % is ACTIVE without a listing_chain row', v_id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_listing_active_has_chain',
            HINT = 'ACTIVE is set by the projector from CouponListed, with the chain row';
  END IF;
  RETURN NULL;
END $$;

CREATE CONSTRAINT TRIGGER trg_listing_active_has_chain
  AFTER INSERT OR UPDATE OF status ON listing
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION f_listing_active_has_chain();
CREATE CONSTRAINT TRIGGER trg_listing_active_has_chain
  AFTER DELETE ON listing_chain
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION f_listing_active_has_chain();
"""

PG_DOWN = """
DROP TRIGGER trg_listing_active_has_chain ON listing_chain;
DROP TRIGGER trg_listing_active_has_chain ON listing;
DROP FUNCTION f_listing_active_has_chain();
DROP TRIGGER trg_listing_chain_immutable ON listing_chain;
DROP FUNCTION f_listing_chain_immutable();
DROP TRIGGER trg_listing_item_count ON listing_item;
DROP TRIGGER trg_listing_item_count ON listing;
DROP FUNCTION f_listing_item_count();
DROP TRIGGER trg_listing_item_max ON listing_item;
DROP FUNCTION f_listing_item_max();
DROP TRIGGER trg_listing_item_valid ON listing_item;
DROP FUNCTION f_listing_item_valid();
ALTER TABLE listing_chain DROP CONSTRAINT ck_listing_chain_id_uint256;
ALTER TABLE listing DROP CONSTRAINT ck_listing_unit_price_uint256;
"""


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_index(
        "uq_listing_inflight",
        "operation",
        ["subject_id"],
        unique=True,
        postgresql_where=sa.text(LISTING_INFLIGHT),
        sqlite_where=sa.text(LISTING_INFLIGHT),
    )
    if _is_postgresql():
        op.execute(PG_UP)


def downgrade() -> None:
    if _is_postgresql():
        op.execute(PG_DOWN)
    op.drop_index("uq_listing_inflight", table_name="operation")
