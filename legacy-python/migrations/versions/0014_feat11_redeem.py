"""FEAT-11 redeem coupon: the reference guarantees, aligned to the Coupon, Marketplace, Vault ABIs.

Corrections to reference/feat-11 (docs/reference-findings.md has the evidence):

  1. The pack's `burnCoupon(code_ref)` then `releaseFundsPartial` is an ABI the pack itself says
     does not exist. The delivered Coupon has `redeem(typeId, amount)` / `redeemFrom`, which call
     `Marketplace.onCouponRedeemed`, which releases the redeemer's escrowed THB to the seller in the
     same transaction. There is no "burned but not paid" window to recover from, so "a redeemed
     coupon's seller has been paid" (pack T11, a report query) becomes a rule checked at commit.

  2. The Vault keeps one escrow per purchase (`FundsLocked(escrowId, …)`), and many purchases of one
     coupon type are active together. The pack's uq_escrow_active (one active escrow per
     contract + typeId, T9) would refuse the second buyer. Here an escrow row is one Vault escrow
     (`chain_escrow_id`, unique per Vault) and one purchase (`purchase_id`, unique).

  3. FEAT-10 put the buyer's THB in the ledger account ESCROW:<purchase id>. An escrow's
     remaining_amount must equal that balance at commit (trg_escrow_matches_ledger), so the Vault
     mirror and the ledger cannot drift.

Rules:

  both dialects
    escrow.chain_escrow_id, escrow.purchase_id (NOT NULL: an escrow exists only for a purchase)
    T9   uq_escrow_chain (contract_address, chain_escrow_id) · uq_escrow_purchase (purchase_id)
    T18  ck_escrow_closed  an escrow is inactive exactly when nothing remains
  PostgreSQL only
    T7 T8 ck_escrow_*_uint256, ck_escrow_remaining_le_total
    T15  trg_escrow_terms     total, coupon count, seller and buyer wallets and coupon type copy
                              the purchase; they never change
    T16  trg_escrow_progress  remaining only falls, redeemed only grows, closed stays closed
    T17  trg_escrow_matches_ledger (at commit)  remaining_amount = balance of ESCROW:<purchase>;
                              ESCROW money without a Vault escrow is refused
    T13  trg_redeem_admissible  a COUPON_REDEEM names a code the actor holds, unexpired, started,
                              and not in an open listing
    T14  trg_coupon_redeemed_final  REDEEMED comes from HELD and is final
    T11  trg_redeemed_coupon_paid (at commit)  a buyer's redeemed purchased codes of a type never
                              outnumber the coupons their escrows of that type have released

Upgrading fails if escrow has rows (the two new columns have nothing to backfill from):

    SELECT count(*) FROM escrow;
"""

import sqlalchemy as sa
from alembic import op

revision = "0014_feat11_redeem"
down_revision = "0013_feat10_purchase"
branch_labels = None
depends_on = None

ESCROW_CLOSED = "(remaining_amount = '0') = (NOT active)"

PG_UP = r"""
ALTER TABLE escrow
  ADD CONSTRAINT ck_escrow_total_uint256 CHECK (f_is_uint256(total_amount, true)),
  ADD CONSTRAINT ck_escrow_remaining_uint256 CHECK (f_is_uint256(remaining_amount, false)),
  ADD CONSTRAINT ck_escrow_chain_id_uint256 CHECK (f_is_uint256(chain_escrow_id, false)),
  ADD CONSTRAINT ck_escrow_token_uint256 CHECK (f_is_uint256(chain_token_id, false)),
  ADD CONSTRAINT ck_escrow_remaining_le_total CHECK (
    NOT (f_is_uint256(total_amount, true) AND f_is_uint256(remaining_amount, false))
    OR remaining_amount::numeric <= total_amount::numeric);

-- T15 · an escrow is the Vault's copy of one purchase
CREATE FUNCTION f_escrow_terms() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p record; v_seller record; v_buyer record; v_type text;
BEGIN
  IF TG_OP = 'UPDATE' THEN
    IF (NEW.purchase_id, NEW.chain_escrow_id, NEW.contract_address, NEW.chain_token_id,
        NEW.seller_wallet_id, NEW.buyer_wallet_id, NEW.total_amount, NEW.coupons_locked)
       IS DISTINCT FROM
       (OLD.purchase_id, OLD.chain_escrow_id, OLD.contract_address, OLD.chain_token_id,
        OLD.seller_wallet_id, OLD.buyer_wallet_id, OLD.total_amount, OLD.coupons_locked) THEN
      RAISE EXCEPTION 'escrow %: what was locked cannot change', OLD.id
        USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_escrow_terms';
    END IF;
    RETURN NEW;
  END IF;
  SELECT pu.total_price, pu.quantity, pu.seller_kind, pu.seller_id, pu.buyer_kind, pu.buyer_id,
         ch.chain_type_id
    INTO p
    FROM purchase pu
    JOIN listing l ON l.id = pu.listing_id
    LEFT JOIN coupon_type_chain ch ON ch.coupon_type_id = l.coupon_type_id
   WHERE pu.id = NEW.purchase_id;
  SELECT owner_kind, owner_id INTO v_seller FROM wallet WHERE id = NEW.seller_wallet_id;
  SELECT owner_kind, owner_id INTO v_buyer FROM wallet WHERE id = NEW.buyer_wallet_id;
  IF NEW.total_amount IS DISTINCT FROM p.total_price
     OR NEW.coupons_locked IS DISTINCT FROM p.quantity
     OR (v_seller.owner_kind, v_seller.owner_id) IS DISTINCT FROM (p.seller_kind, p.seller_id)
     OR (v_buyer.owner_kind, v_buyer.owner_id) IS DISTINCT FROM (p.buyer_kind, p.buyer_id)
     OR NEW.chain_token_id IS DISTINCT FROM p.chain_type_id THEN
    RAISE EXCEPTION 'escrow % does not match purchase % (amount, coupons, wallets or type)',
      NEW.id, NEW.purchase_id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_escrow_terms';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_escrow_terms
  BEFORE INSERT OR UPDATE ON escrow
  FOR EACH ROW EXECUTE FUNCTION f_escrow_terms();

-- T16 · money leaves an escrow; it never comes back into it
CREATE FUNCTION f_escrow_progress() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT (f_is_uint256(NEW.remaining_amount, false)
          AND f_is_uint256(OLD.remaining_amount, false)) THEN
    RETURN NEW;  -- the CHECKs refuse malformed amounts
  END IF;
  IF NEW.remaining_amount::numeric > OLD.remaining_amount::numeric
     OR NEW.coupons_redeemed < OLD.coupons_redeemed
     OR (NOT OLD.active AND NEW.active) THEN
    RAISE EXCEPTION 'escrow %: remaining only falls, redeemed only grows, closed stays closed',
      OLD.id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_escrow_progress';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_escrow_progress
  BEFORE UPDATE OF remaining_amount, coupons_redeemed, active ON escrow
  FOR EACH ROW EXECUTE FUNCTION f_escrow_progress();

-- T17 · the Vault mirror and the ledger agree, at commit
CREATE FUNCTION f_escrow_matches_ledger() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_purchase text; v_balance numeric; v_remaining text;
BEGIN
  IF TG_TABLE_NAME = 'escrow' THEN
    v_purchase := NEW.purchase_id;
  ELSE
    IF coalesce(NEW.account, OLD.account) <> 'ESCROW' THEN
      RETURN NULL;
    END IF;
    v_purchase := substr(coalesce(NEW.account_id, OLD.account_id), length('ESCROW:') + 1);
  END IF;
  SELECT coalesce((SELECT amount::numeric FROM thb_balance
                    WHERE account = 'ESCROW' AND account_id = 'ESCROW:' || v_purchase), 0)
    INTO v_balance;
  SELECT remaining_amount INTO v_remaining FROM escrow WHERE purchase_id = v_purchase;
  IF NOT FOUND THEN
    IF v_balance <> 0 THEN
      RAISE EXCEPTION 'ESCROW:% holds % THB but no Vault escrow is recorded', v_purchase, v_balance
        USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_escrow_matches_ledger',
              HINT = 'write the escrow from FundsLocked in the same transaction';
    END IF;
  ELSIF v_remaining::numeric <> v_balance THEN
    RAISE EXCEPTION 'escrow of purchase % has % remaining, its ledger account holds %',
      v_purchase, v_remaining, v_balance
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_escrow_matches_ledger';
  END IF;
  RETURN NULL;
END $$;

CREATE CONSTRAINT TRIGGER trg_escrow_matches_ledger
  AFTER INSERT OR UPDATE ON escrow
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION f_escrow_matches_ledger();
CREATE CONSTRAINT TRIGGER trg_escrow_matches_ledger
  AFTER INSERT OR UPDATE OR DELETE ON thb_balance
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION f_escrow_matches_ledger();

-- T13 · only the holder redeems, only a live code, never one that is for sale
CREATE FUNCTION f_redeem_admissible() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  c record;
  v_kind text := split_part(NEW.actor_scope, ':', 1);
  v_id   text := substr(NEW.actor_scope, length(split_part(NEW.actor_scope, ':', 1)) + 2);
  v_listing text;
BEGIN
  IF NEW.kind <> 'COUPON_REDEEM'
     OR NEW.actor_scope !~ '^(merchant|customer|admin|anon|system):.+$' THEN
    RETURN NEW;
  END IF;
  IF NEW.subject_id IS NULL THEN
    RAISE EXCEPTION 'COUPON_REDEEM needs the coupon code id in subject_id'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_redeem_admissible';
  END IF;
  SELECT cc.state, cc.holder_kind, cc.holder_id, t.starts_at, t.expires_at INTO c
    FROM coupon_code cc JOIN coupon_type t ON t.id = cc.coupon_type_id
   WHERE cc.id = NEW.subject_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'coupon code % not found', NEW.subject_id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_redeem_admissible';
  END IF;
  IF c.state <> 'HELD' OR upper(v_kind) IS DISTINCT FROM c.holder_kind
     OR v_id IS DISTINCT FROM c.holder_id THEN
    RAISE EXCEPTION 'coupon code % is not held by %', NEW.subject_id, NEW.actor_scope
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_redeem_admissible';
  END IF;
  IF now() < c.starts_at OR now() >= c.expires_at THEN
    RAISE EXCEPTION 'coupon code % is outside its validity (% to %)',
      NEW.subject_id, c.starts_at, c.expires_at
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_redeem_admissible';
  END IF;
  SELECT i.listing_id INTO v_listing
    FROM listing_item i JOIN listing l ON l.id = i.listing_id
   WHERE i.coupon_code_id = NEW.subject_id AND l.status IN ('PENDING', 'ACTIVE')
   LIMIT 1;
  IF v_listing IS NOT NULL THEN
    RAISE EXCEPTION 'coupon code % is for sale in listing %: delist it first',
      NEW.subject_id, v_listing
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_redeem_admissible';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_redeem_admissible
  BEFORE INSERT OR UPDATE OF kind, subject_id, actor_scope ON operation
  FOR EACH ROW EXECUTE FUNCTION f_redeem_admissible();

-- T14 · a code is redeemed once, from HELD
CREATE FUNCTION f_coupon_redeemed_final() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.state = 'REDEEMED' THEN
    IF (NEW.state, NEW.holder_kind, NEW.holder_id, NEW.redeemed_at, NEW.coupon_type_id)
       IS DISTINCT FROM
       (OLD.state, OLD.holder_kind, OLD.holder_id, OLD.redeemed_at, OLD.coupon_type_id) THEN
      RAISE EXCEPTION 'coupon code % is redeemed: nothing about it changes', OLD.id
        USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_coupon_redeemed_final';
    END IF;
  ELSIF NEW.state = 'REDEEMED' AND NEW.redeemed_at IS NOT NULL AND OLD.state <> 'HELD' THEN
    RAISE EXCEPTION 'coupon code % is %, only a HELD code can be redeemed', OLD.id, OLD.state
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_coupon_redeemed_final';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_coupon_redeemed_final
  BEFORE UPDATE ON coupon_code
  FOR EACH ROW EXECUTE FUNCTION f_coupon_redeemed_final();

-- T11 · no coupon is redeemed while its seller goes unpaid, at commit.
-- The Vault releases a redeemer's escrows of a type in its own order, not per code, so the rule
-- counts per (buyer, coupon type): redeemed codes the buyer bought ≤ coupons released to sellers.
CREATE FUNCTION f_redeemed_coupon_paid() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_redeemed bigint; v_paid bigint;
BEGIN
  IF NEW.state <> 'REDEEMED' OR NEW.holder_kind IS NULL THEN
    RETURN NULL;
  END IF;
  SELECT count(DISTINCT cc.id) INTO v_redeemed
    FROM coupon_code cc
    JOIN listing_item i ON i.coupon_code_id = cc.id
    JOIN purchase p ON p.listing_id = i.listing_id AND p.status = 'SETTLED'
   WHERE cc.state = 'REDEEMED' AND cc.coupon_type_id = NEW.coupon_type_id
     AND cc.holder_kind = NEW.holder_kind AND cc.holder_id = NEW.holder_id
     AND p.buyer_kind = NEW.holder_kind AND p.buyer_id = NEW.holder_id;
  SELECT coalesce(sum(e.coupons_redeemed), 0) INTO v_paid
    FROM escrow e
    JOIN purchase p ON p.id = e.purchase_id
    JOIN listing l ON l.id = p.listing_id
   WHERE l.coupon_type_id = NEW.coupon_type_id
     AND p.buyer_kind = NEW.holder_kind AND p.buyer_id = NEW.holder_id;
  IF v_redeemed > v_paid THEN
    RAISE EXCEPTION '% %: % bought coupons of type % redeemed, only % paid out from escrow',
      NEW.holder_kind, NEW.holder_id, v_redeemed, NEW.coupon_type_id, v_paid
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_redeemed_coupon_paid',
            HINT = 'apply CouponRedeemed and FundsReleased of the same transaction together';
  END IF;
  RETURN NULL;
END $$;

CREATE CONSTRAINT TRIGGER trg_redeemed_coupon_paid
  AFTER UPDATE OF state ON coupon_code
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION f_redeemed_coupon_paid();
"""

PG_DOWN = """
DROP TRIGGER trg_redeemed_coupon_paid ON coupon_code;
DROP FUNCTION f_redeemed_coupon_paid();
DROP TRIGGER trg_coupon_redeemed_final ON coupon_code;
DROP FUNCTION f_coupon_redeemed_final();
DROP TRIGGER trg_redeem_admissible ON operation;
DROP FUNCTION f_redeem_admissible();
DROP TRIGGER trg_escrow_matches_ledger ON thb_balance;
DROP TRIGGER trg_escrow_matches_ledger ON escrow;
DROP FUNCTION f_escrow_matches_ledger();
DROP TRIGGER trg_escrow_progress ON escrow;
DROP FUNCTION f_escrow_progress();
DROP TRIGGER trg_escrow_terms ON escrow;
DROP FUNCTION f_escrow_terms();
ALTER TABLE escrow
  DROP CONSTRAINT ck_escrow_remaining_le_total,
  DROP CONSTRAINT ck_escrow_token_uint256,
  DROP CONSTRAINT ck_escrow_chain_id_uint256,
  DROP CONSTRAINT ck_escrow_remaining_uint256,
  DROP CONSTRAINT ck_escrow_total_uint256;
"""


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def upgrade() -> None:
    with op.batch_alter_table("escrow") as batch:
        batch.add_column(sa.Column("chain_escrow_id", sa.String(78), nullable=False))
        batch.add_column(
            sa.Column(
                "purchase_id",
                sa.Text(),
                sa.ForeignKey("purchase.id", name="fk_escrow_purchase"),
                nullable=False,
            )
        )
        batch.create_unique_constraint("uq_escrow_chain", ["contract_address", "chain_escrow_id"])
        batch.create_unique_constraint("uq_escrow_purchase", ["purchase_id"])
        batch.create_check_constraint("ck_escrow_closed", ESCROW_CLOSED)
    if _is_postgresql():
        op.execute(PG_UP)


def downgrade() -> None:
    if _is_postgresql():
        op.execute(PG_DOWN)
    with op.batch_alter_table("escrow") as batch:
        batch.drop_constraint("ck_escrow_closed", type_="check")
        batch.drop_constraint("uq_escrow_purchase", type_="unique")
        batch.drop_constraint("uq_escrow_chain", type_="unique")
        batch.drop_constraint("fk_escrow_purchase", type_="foreignkey")
        batch.drop_column("purchase_id")
        batch.drop_column("chain_escrow_id")
