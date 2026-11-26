"""FEAT-10 buy coupon: the THB ledger rules, aligned to the Marketplace and Vault ABIs.

Corrections to reference/feat-10 (docs/reference-findings.md has the evidence):

  1. The delivered Marketplace's `buyCoupon(listingId, amount)` sends the coupons to the buyer and
     locks the buyer's THB in the Vault (`FundsLocked(escrowId, seller, buyer, amount)`). The seller
     is paid when the buyer redeems (`releaseFundsPartial`, FEAT-11); expired coupons refund the
     buyer (`refundExpiredCoupon` → `returnFunds`). The pack credits the seller and the platform at
     purchase, so the seller's database balance would hold THB the chain still has locked. Here a
     purchase moves buyer → ESCROW:<purchase id>, a new ledger account; FEAT-11 moves it on.

  2. The contract takes no fee. fee_schedule, purchase.fee_bps / fee_amount / net_to_seller and the
     pack's three-way arithmetic stay (the split is decided at purchase and applied at release),
     but how a fee above 0 reaches the platform on chain is open.

  3. The pack's balance check fired only when thb_balance changed: a ledger row written without
     touching the balance passed. trg_balance_matches_ledger also fires from ledger_entry (T14b).

  4. Added: the ledger is append-only (T18b); a purchase must carry its listing's price, quantity
     and seller, its own LISTING_BUY operation and the fee rate in force (T20b T23b); a listing's
     price, quantity, seller and type never change (T24); every amount is checked as a number
     (f_is_int256 for signed ledger and balance amounts); an account_id starts with its account
     (`CUSTOMER_THB:cus_1`, T12b), so a THB reservation names its account unambiguously.

Rules:

  both dialects
    ck_ledger_entry_account_enum / ck_thb_balance_account_enum gain ESCROW
    T12b ck_ledger_entry_account_id, ck_thb_balance_account_id
    T33  ck_thb_balance_sign  only ISSUANCE may be negative
    T30  uq_purchase_listing  one purchase per listing unless it FAILED (whole listings only, as
         in the pack; the contract allows partial buys)
         ix_ledger_account    the balance check sums one account's rows
  PostgreSQL only
    T18 ck_ledger_entry_amount (non-zero int256), ck_thb_balance_amount (int256),
        ck_purchase_*_uint256
    T12 ck_reservation_thb_subject  a THB reservation spends CUSTOMER_THB or MERCHANT_THB
    T6 T10 T11 T31 f_available_thb, trg_thb_reservation_balance (advisory lock per account)
    T3-T5 trg_buy_admissible   the listing exists, is ACTIVE and is not the buyer's own
    T19-T22 trg_purchase_math  total = unit × qty, fee + net = total, fee = div(total × bps, 10000)
    T20b T23b trg_purchase_terms, f_fee_bps_at
    T24 trg_listing_terms_fixed
    T13 trg_ledger_balanced (at commit) · T14 T14b trg_balance_matches_ledger (at commit)
    T18b trg_ledger_append_only

Upgrading fails if an existing row breaks a new CHECK, e.g. an account_id without its account prefix
or a malformed amount:

    SELECT account, account_id FROM thb_balance WHERE account_id NOT LIKE account || ':%';
"""

import sqlalchemy as sa
from alembic import op

revision = "0013_feat10_purchase"
down_revision = "0012_feat09_delist"
branch_labels = None
depends_on = None

ACCOUNTS_0002 = "('CUSTOMER_THB', 'MERCHANT_THB', 'PLATFORM_THB', 'ISSUANCE')"
ACCOUNTS = "('CUSTOMER_THB', 'MERCHANT_THB', 'PLATFORM_THB', 'ISSUANCE', 'ESCROW')"
ACCOUNT_ID_PREFIX = (
    "substr(account_id, 1, length(account) + 1) = account || ':'"
    " AND length(account_id) > length(account) + 1"
)
BALANCE_SIGN = "substr(amount, 1, 1) <> '-' OR account = 'ISSUANCE'"

PG_UP = r"""
-- A decimal string that is a valid int256 magnitude: optional minus, no leading zero, no '-0'.
CREATE FUNCTION f_is_int256(p_value text, p_nonzero boolean) RETURNS boolean
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE
    WHEN p_value IS NULL OR p_value !~ '^(0|-?[1-9][0-9]{0,77})$' THEN false
    WHEN p_nonzero AND p_value = '0' THEN false
    ELSE abs(p_value::numeric)
         <= 115792089237316195423570985008687907853269984665640564039457584007913129639935
  END
$$;

ALTER TABLE ledger_entry ADD CONSTRAINT ck_ledger_entry_amount CHECK (f_is_int256(amount, true));
ALTER TABLE thb_balance ADD CONSTRAINT ck_thb_balance_amount CHECK (f_is_int256(amount, false));
ALTER TABLE purchase
  ADD CONSTRAINT ck_purchase_unit_price_uint256 CHECK (f_is_uint256(unit_price, true)),
  ADD CONSTRAINT ck_purchase_total_uint256 CHECK (f_is_uint256(total_price, true)),
  ADD CONSTRAINT ck_purchase_fee_uint256 CHECK (f_is_uint256(fee_amount, false)),
  ADD CONSTRAINT ck_purchase_net_uint256 CHECK (f_is_uint256(net_to_seller, false));
ALTER TABLE reservation ADD CONSTRAINT ck_reservation_thb_subject
  CHECK (subject_type <> 'THB_BALANCE' OR subject_id ~ '^(CUSTOMER_THB|MERCHANT_THB):.+$');

CREATE FUNCTION f_fee_bps_at(p_at timestamptz) RETURNS integer LANGUAGE sql STABLE AS $$
  SELECT bps FROM fee_schedule WHERE effective_from <= p_at ORDER BY effective_from DESC LIMIT 1
$$;

-- T6 T10 T11 T31 · THB adds up: open reservations share the balance
CREATE FUNCTION f_available_thb(p_account_id text) RETURNS numeric LANGUAGE sql STABLE AS $$
  SELECT greatest(
    coalesce((SELECT amount::numeric FROM thb_balance WHERE account_id = p_account_id
                AND account = split_part(p_account_id, ':', 1)), 0)
    - coalesce((SELECT sum(amount::numeric) FROM reservation
                 WHERE subject_type = 'THB_BALANCE' AND subject_id = p_account_id
                   AND released_at IS NULL), 0),
    0)
$$;

CREATE FUNCTION f_thb_reservation_balance() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_available numeric;
BEGIN
  IF NEW.subject_type <> 'THB_BALANCE' OR NEW.released_at IS NOT NULL
     OR NOT f_is_uint256(NEW.amount, true)
     OR NEW.subject_id !~ '^(CUSTOMER_THB|MERCHANT_THB):.+$' THEN
    RETURN NEW;  -- the CHECKs refuse malformed rows
  END IF;
  -- one account's reservations queue here; other accounts do not wait (F1)
  PERFORM pg_advisory_xact_lock(hashtextextended('thb:' || NEW.subject_id, 0));
  v_available := f_available_thb(NEW.subject_id);
  IF NEW.amount::numeric > v_available THEN
    RAISE EXCEPTION '% has % THB available, % requested', NEW.subject_id, v_available, NEW.amount
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_thb_reservation_balance',
            HINT = 'F4 · not enough is a refusal; nothing is minted to cover it';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_thb_reservation_balance
  BEFORE INSERT ON reservation
  FOR EACH ROW EXECUTE FUNCTION f_thb_reservation_balance();

-- T3-T5 · who may buy is decided where the row is written
CREATE FUNCTION f_buy_admissible() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  l record;
  v_kind text := split_part(NEW.actor_scope, ':', 1);
  v_id   text := substr(NEW.actor_scope, length(split_part(NEW.actor_scope, ':', 1)) + 2);
BEGIN
  IF NEW.kind <> 'LISTING_BUY'
     OR NEW.actor_scope !~ '^(merchant|customer|admin|anon|system):.+$' THEN
    RETURN NEW;
  END IF;
  IF NEW.subject_id IS NULL THEN
    RAISE EXCEPTION 'LISTING_BUY needs the listing id in subject_id'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_buy_admissible';
  END IF;
  IF v_kind NOT IN ('merchant', 'customer') THEN
    RAISE EXCEPTION 'scope % cannot buy: only a merchant or a customer holds THB', NEW.actor_scope
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_buy_admissible';
  END IF;
  SELECT seller_kind, seller_id, status INTO l FROM listing WHERE id = NEW.subject_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'listing % not found', NEW.subject_id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_buy_admissible';
  END IF;
  IF l.status <> 'ACTIVE' THEN
    RAISE EXCEPTION 'listing % is %, only an ACTIVE listing can be bought', NEW.subject_id, l.status
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_buy_admissible';
  END IF;
  IF upper(v_kind) = l.seller_kind AND v_id = l.seller_id THEN
    RAISE EXCEPTION '% cannot buy its own listing %', NEW.actor_scope, NEW.subject_id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_buy_admissible';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_buy_admissible
  BEFORE INSERT OR UPDATE OF kind, subject_id, actor_scope ON operation
  FOR EACH ROW EXECUTE FUNCTION f_buy_admissible();

-- T19-T22 · the numbers agree three ways; the remainder of the fee rounding stays with the seller
CREATE FUNCTION f_purchase_math() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_total numeric; v_fee numeric; v_net numeric; v_expect numeric;
BEGIN
  IF NOT (f_is_uint256(NEW.unit_price, true) AND f_is_uint256(NEW.total_price, true)
          AND f_is_uint256(NEW.fee_amount, false) AND f_is_uint256(NEW.net_to_seller, false)) THEN
    RETURN NEW;  -- the CHECKs refuse malformed amounts
  END IF;
  v_total := NEW.total_price::numeric;
  v_fee := NEW.fee_amount::numeric;
  v_net := NEW.net_to_seller::numeric;
  IF v_total <> NEW.unit_price::numeric * NEW.quantity THEN
    RAISE EXCEPTION 'purchase %: total % is not unit price % x quantity %',
      NEW.id, v_total, NEW.unit_price, NEW.quantity
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_purchase_math';
  END IF;
  IF v_fee + v_net <> v_total THEN
    RAISE EXCEPTION 'purchase %: fee % + net % is not total %', NEW.id, v_fee, v_net, v_total
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_purchase_math';
  END IF;
  v_expect := div(v_total * NEW.fee_bps, 10000);
  IF v_fee <> v_expect THEN
    RAISE EXCEPTION 'purchase %: fee % at % bps should be %', NEW.id, v_fee, NEW.fee_bps, v_expect
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_purchase_math';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_purchase_math
  BEFORE INSERT OR UPDATE OF unit_price, quantity, total_price, fee_bps, fee_amount, net_to_seller
  ON purchase
  FOR EACH ROW EXECUTE FUNCTION f_purchase_math();

-- T20b T23b · a purchase copies its listing and operation at the rate in force, then is fixed
CREATE FUNCTION f_purchase_terms() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE l record; o record; v_bps integer;
BEGIN
  IF TG_OP = 'UPDATE' THEN
    IF (NEW.listing_id, NEW.buyer_kind, NEW.buyer_id, NEW.seller_kind, NEW.seller_id,
        NEW.unit_price, NEW.quantity, NEW.fee_bps, NEW.operation_id)
       IS DISTINCT FROM
       (OLD.listing_id, OLD.buyer_kind, OLD.buyer_id, OLD.seller_kind, OLD.seller_id,
        OLD.unit_price, OLD.quantity, OLD.fee_bps, OLD.operation_id) THEN
      RAISE EXCEPTION 'purchase %: the terms of a purchase cannot change', NEW.id
        USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_purchase_terms';
    END IF;
    RETURN NEW;
  END IF;
  SELECT seller_kind, seller_id, unit_price, quantity INTO l FROM listing WHERE id = NEW.listing_id;
  IF (NEW.seller_kind, NEW.seller_id, NEW.unit_price, NEW.quantity)
     IS DISTINCT FROM (l.seller_kind, l.seller_id, l.unit_price, l.quantity) THEN
    RAISE EXCEPTION 'purchase % does not match listing % (seller, unit price, quantity)',
      NEW.id, NEW.listing_id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_purchase_terms';
  END IF;
  SELECT kind, subject_id, actor_scope INTO o FROM operation WHERE id = NEW.operation_id;
  IF o.kind IS DISTINCT FROM 'LISTING_BUY' OR o.subject_id IS DISTINCT FROM NEW.listing_id
     OR o.actor_scope IS DISTINCT FROM lower(NEW.buyer_kind) || ':' || NEW.buyer_id THEN
    RAISE EXCEPTION 'purchase % is not the LISTING_BUY of % on listing %',
      NEW.id, lower(NEW.buyer_kind) || ':' || NEW.buyer_id, NEW.listing_id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_purchase_terms';
  END IF;
  v_bps := f_fee_bps_at(now());
  IF v_bps IS DISTINCT FROM NEW.fee_bps THEN
    RAISE EXCEPTION 'purchase % uses % bps; the rate in force is %', NEW.id, NEW.fee_bps,
      coalesce(v_bps::text, 'missing')
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_purchase_terms',
            HINT = 'no fee_schedule row in force means no price: there is no silent zero';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_purchase_terms
  BEFORE INSERT OR UPDATE ON purchase
  FOR EACH ROW EXECUTE FUNCTION f_purchase_terms();

-- T24 · what a buyer saw is what a buyer pays
CREATE FUNCTION f_listing_terms_fixed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW.unit_price, NEW.quantity, NEW.seller_kind, NEW.seller_id, NEW.coupon_type_id)
     IS DISTINCT FROM
     (OLD.unit_price, OLD.quantity, OLD.seller_kind, OLD.seller_id, OLD.coupon_type_id) THEN
    RAISE EXCEPTION 'listing %: price, quantity, seller and coupon type are fixed', OLD.id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_listing_terms_fixed',
            HINT = 'delist and list again';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_listing_terms_fixed
  BEFORE UPDATE OF unit_price, quantity, seller_kind, seller_id, coupon_type_id ON listing
  FOR EACH ROW EXECUTE FUNCTION f_listing_terms_fixed();

-- T18b · a mistake is corrected by a reversing entry, never by editing history
CREATE FUNCTION f_ledger_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'ledger_entry %: the ledger is append-only', OLD.id
    USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_ledger_append_only',
          HINT = 'write a reversing entry under a new operation';
END $$;

CREATE TRIGGER trg_ledger_append_only
  BEFORE UPDATE OR DELETE ON ledger_entry
  FOR EACH ROW EXECUTE FUNCTION f_ledger_append_only();

-- T13 · F8: every operation's THB entries sum to zero, at commit
CREATE FUNCTION f_ledger_balanced() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_sum numeric;
BEGIN
  SELECT coalesce(sum(amount::numeric), 0) INTO v_sum
    FROM ledger_entry WHERE operation_id = NEW.operation_id;
  IF v_sum <> 0 THEN
    RAISE EXCEPTION 'operation %: THB ledger sums to %, not 0', NEW.operation_id, v_sum
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_ledger_balanced',
            HINT = 'F8 · money that leaves one account enters another';
  END IF;
  RETURN NULL;
END $$;

CREATE CONSTRAINT TRIGGER trg_ledger_balanced
  AFTER INSERT ON ledger_entry
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION f_ledger_balanced();

-- T14 T14b · F4: a balance is its ledger, checked at commit from either side.
-- The current row is read again: NEW of a deferred trigger is the row as it was when queued.
CREATE FUNCTION f_balance_matches_ledger() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_account text; v_id text; v_balance numeric; v_ledger numeric;
BEGIN
  IF TG_OP = 'DELETE' THEN
    v_account := OLD.account; v_id := OLD.account_id;
  ELSE
    v_account := NEW.account; v_id := NEW.account_id;
  END IF;
  SELECT coalesce((SELECT amount::numeric FROM thb_balance
                    WHERE account = v_account AND account_id = v_id), 0)
    INTO v_balance;
  SELECT coalesce(sum(amount::numeric), 0) INTO v_ledger
    FROM ledger_entry WHERE account = v_account AND account_id = v_id;
  IF v_balance <> v_ledger THEN
    RAISE EXCEPTION '% balance is % but its ledger sums to %', v_id, v_balance, v_ledger
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_balance_matches_ledger',
            HINT = 'F4 · a balance is a projection of the ledger, never set on its own';
  END IF;
  RETURN NULL;
END $$;

CREATE CONSTRAINT TRIGGER trg_balance_matches_ledger
  AFTER INSERT OR UPDATE OR DELETE ON thb_balance
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION f_balance_matches_ledger();
CREATE CONSTRAINT TRIGGER trg_balance_matches_ledger
  AFTER INSERT ON ledger_entry
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION f_balance_matches_ledger();
"""

PG_DOWN = """
DROP TRIGGER trg_balance_matches_ledger ON ledger_entry;
DROP TRIGGER trg_balance_matches_ledger ON thb_balance;
DROP FUNCTION f_balance_matches_ledger();
DROP TRIGGER trg_ledger_balanced ON ledger_entry;
DROP FUNCTION f_ledger_balanced();
DROP TRIGGER trg_ledger_append_only ON ledger_entry;
DROP FUNCTION f_ledger_append_only();
DROP TRIGGER trg_listing_terms_fixed ON listing;
DROP FUNCTION f_listing_terms_fixed();
DROP TRIGGER trg_purchase_terms ON purchase;
DROP FUNCTION f_purchase_terms();
DROP TRIGGER trg_purchase_math ON purchase;
DROP FUNCTION f_purchase_math();
DROP TRIGGER trg_buy_admissible ON operation;
DROP FUNCTION f_buy_admissible();
DROP TRIGGER trg_thb_reservation_balance ON reservation;
DROP FUNCTION f_thb_reservation_balance();
DROP FUNCTION f_available_thb(text);
DROP FUNCTION f_fee_bps_at(timestamptz);
ALTER TABLE reservation DROP CONSTRAINT ck_reservation_thb_subject;
ALTER TABLE purchase
  DROP CONSTRAINT ck_purchase_net_uint256,
  DROP CONSTRAINT ck_purchase_fee_uint256,
  DROP CONSTRAINT ck_purchase_total_uint256,
  DROP CONSTRAINT ck_purchase_unit_price_uint256;
ALTER TABLE thb_balance DROP CONSTRAINT ck_thb_balance_amount;
ALTER TABLE ledger_entry DROP CONSTRAINT ck_ledger_entry_amount;
DROP FUNCTION f_is_int256(text, boolean);
"""


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def _accounts(table: str, accounts: str) -> None:
    with op.batch_alter_table(table) as batch:
        batch.drop_constraint(f"ck_{table}_account_enum", type_="check")
        batch.create_check_constraint(f"ck_{table}_account_enum", f"account IN {accounts}")


def upgrade() -> None:
    _accounts("ledger_entry", ACCOUNTS)
    _accounts("thb_balance", ACCOUNTS)
    with op.batch_alter_table("ledger_entry") as batch:
        batch.create_check_constraint("ck_ledger_entry_account_id", ACCOUNT_ID_PREFIX)
    with op.batch_alter_table("thb_balance") as batch:
        batch.create_check_constraint("ck_thb_balance_account_id", ACCOUNT_ID_PREFIX)
        batch.create_check_constraint("ck_thb_balance_sign", BALANCE_SIGN)
    op.create_index("ix_ledger_account", "ledger_entry", ["account", "account_id"])
    op.create_index(
        "uq_purchase_listing",
        "purchase",
        ["listing_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'FAILED'"),
        sqlite_where=sa.text("status <> 'FAILED'"),
    )
    if _is_postgresql():
        op.execute(PG_UP)


def downgrade() -> None:
    # Needs no ESCROW rows: 0012 has no such account.
    if _is_postgresql():
        op.execute(PG_DOWN)
    op.drop_index("uq_purchase_listing", table_name="purchase")
    op.drop_index("ix_ledger_account", table_name="ledger_entry")
    with op.batch_alter_table("thb_balance") as batch:
        batch.drop_constraint("ck_thb_balance_sign", type_="check")
        batch.drop_constraint("ck_thb_balance_account_id", type_="check")
    with op.batch_alter_table("ledger_entry") as batch:
        batch.drop_constraint("ck_ledger_entry_account_id", type_="check")
    _accounts("thb_balance", ACCOUNTS_0002)
    _accounts("ledger_entry", ACCOUNTS_0002)
