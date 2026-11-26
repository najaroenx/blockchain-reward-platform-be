"""FEAT-09 delist: the reference's five rules, aligned to the Marketplace ABI.

Corrections to reference/feat-09 (docs/reference-findings.md has the evidence):

  1. The delivered Marketplace delists with one transaction, `delistCoupon(listingId)`, which hands
     the coupons back to the seller and emits CouponDelisted. The pack's cancelListing →
     releaseFromVault pair does not exist. The step-order gate still matters for the multi-step
     listing operations (setApprovalForAll → listCoupon, and the purchase in FEAT-10).

  2. The pack gates every operation's steps. Applied to COUPON_ISSUE, a 1,000,000-code issue
     (2,000 mint steps of 500) would wait for finality between every batch although the batches do
     not depend on each other. trg_step_order_gate covers LISTING_CREATE, LISTING_DELETE and
     LISTING_BUY only.

  3. actor_scope also accepts `system:` (FEAT-02 provisions customers as `system:provision`).

Rules, as in the pack:

  both dialects
    T12-T15 uq_listing_inflight now includes LISTING_BUY: one unfinished create, delete or purchase
            per listing; a delist and a purchase race on this index (FEAT-09 and FEAT-10 agree)
  PostgreSQL only
    T8      ck_operation_actor_scope  ^(merchant|customer|admin|anon|system):.+$
    T3-T6 T9-T11 trg_delist_admissible  the listing exists, belongs to the actor (admin: any) and
            is ACTIVE. "Not found" is a refusal, never "assume it is the caller's" (F5)
    T26 T26b T23 trg_listing_status_transition  PENDING → ACTIVE | FAILED, ACTIVE → SOLD | DELISTED,
            a new listing starts PENDING; fills active_at / closed_at when missing
    T18 T19 trg_step_order_gate  a listing operation's step n leaves PENDING only after step n-1 is
            FINALIZED (on insert too)
    T21 T22 trg_coupon_hold_until_closed  a COUPON_UNIT reservation is released only once no
            PENDING / ACTIVE listing holds the code

Upgrading fails if an operation's actor_scope breaks the pattern, or a listing already has an
unfinished purchase next to another unfinished operation.

    SELECT id, actor_scope FROM operation
     WHERE actor_scope !~ '^(merchant|customer|admin|anon|system):.+$';
"""

import sqlalchemy as sa
from alembic import op

revision = "0012_feat09_delist"
down_revision = "0011_feat08_listing"
branch_labels = None
depends_on = None

INFLIGHT_0011 = (
    "kind IN ('LISTING_CREATE', 'LISTING_DELETE') AND status NOT IN ('FINALIZED', 'CHAIN_REVERTED')"
)
INFLIGHT = (
    "kind IN ('LISTING_CREATE', 'LISTING_DELETE', 'LISTING_BUY')"
    " AND status NOT IN ('FINALIZED', 'CHAIN_REVERTED')"
)

PG_UP = r"""
ALTER TABLE operation ADD CONSTRAINT ck_operation_actor_scope
  CHECK (actor_scope ~ '^(merchant|customer|admin|anon|system):.+$');

-- T3-T6 T9-T11 · F5: who may delist is decided where the row is written
CREATE FUNCTION f_delist_admissible() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  l record;
  v_kind text := split_part(NEW.actor_scope, ':', 1);
  v_id   text := substr(NEW.actor_scope, length(split_part(NEW.actor_scope, ':', 1)) + 2);
BEGIN
  -- a malformed actor_scope is ck_operation_actor_scope's to refuse (it runs after this trigger)
  IF NEW.kind <> 'LISTING_DELETE'
     OR NEW.actor_scope !~ '^(merchant|customer|admin|anon|system):.+$' THEN
    RETURN NEW;
  END IF;
  IF NEW.subject_id IS NULL THEN
    RAISE EXCEPTION 'LISTING_DELETE needs the listing id in subject_id'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_delist_admissible';
  END IF;
  SELECT seller_kind, seller_id, status INTO l FROM listing WHERE id = NEW.subject_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'listing % not found', NEW.subject_id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_delist_admissible',
            HINT = 'F5 · not found is a refusal, never the caller''s listing';
  END IF;
  IF v_kind <> 'admin'
     AND (upper(v_kind) IS DISTINCT FROM l.seller_kind OR v_id IS DISTINCT FROM l.seller_id) THEN
    RAISE EXCEPTION 'listing % does not belong to %', NEW.subject_id, NEW.actor_scope
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_delist_admissible';
  END IF;
  IF l.status <> 'ACTIVE' THEN
    RAISE EXCEPTION 'listing % is %, only an ACTIVE listing can be delisted',
      NEW.subject_id, l.status
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_delist_admissible';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_delist_admissible
  BEFORE INSERT OR UPDATE OF kind, subject_id, actor_scope ON operation
  FOR EACH ROW EXECUTE FUNCTION f_delist_admissible();

-- T23 T26 T26b · the listing lifecycle only moves forward
CREATE FUNCTION f_listing_status_transition() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'PENDING' THEN
      RAISE EXCEPTION 'listing % must start PENDING, not %', NEW.id, NEW.status
        USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_listing_status_transition';
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.status <> OLD.status AND NOT (
       (OLD.status = 'PENDING' AND NEW.status IN ('ACTIVE', 'FAILED'))
    OR (OLD.status = 'ACTIVE' AND NEW.status IN ('SOLD', 'DELISTED'))) THEN
    RAISE EXCEPTION 'listing %: % → % is not allowed', OLD.id, OLD.status, NEW.status
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_listing_status_transition',
            HINT = 'SOLD, DELISTED and FAILED are final; relist as a new listing';
  END IF;
  IF NEW.status = 'ACTIVE' AND NEW.active_at IS NULL THEN
    NEW.active_at := now();
  END IF;
  IF NEW.status IN ('SOLD', 'DELISTED') AND NEW.closed_at IS NULL THEN
    NEW.closed_at := now();
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_listing_status_transition
  BEFORE INSERT OR UPDATE OF status ON listing
  FOR EACH ROW EXECUTE FUNCTION f_listing_status_transition();

-- T18 T19 · steps of a listing operation run in order
CREATE FUNCTION f_step_order_gate() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_kind text; v_prev text;
BEGIN
  IF NEW.status = 'PENDING' OR NEW.step_index = 1
     OR (TG_OP = 'UPDATE' AND OLD.status <> 'PENDING') THEN
    RETURN NEW;
  END IF;
  SELECT kind INTO v_kind FROM operation WHERE id = NEW.operation_id;
  IF v_kind NOT IN ('LISTING_CREATE', 'LISTING_DELETE', 'LISTING_BUY') THEN
    RETURN NEW;
  END IF;
  SELECT status INTO v_prev FROM operation_step
   WHERE operation_id = NEW.operation_id AND step_index = NEW.step_index - 1;
  IF v_prev IS DISTINCT FROM 'FINALIZED' THEN
    RAISE EXCEPTION 'step % of operation % cannot leave PENDING: step % is %',
      NEW.step_index, NEW.operation_id, NEW.step_index - 1, coalesce(v_prev, 'missing')
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_step_order_gate';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_step_order_gate
  BEFORE INSERT OR UPDATE OF status ON operation_step
  FOR EACH ROW EXECUTE FUNCTION f_step_order_gate();

-- T21 T22 · close the listing first, then release its codes
CREATE FUNCTION f_coupon_hold_until_closed() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_listing text; v_status text;
BEGIN
  IF OLD.released_at IS NOT NULL OR NEW.released_at IS NULL
     OR NEW.subject_type <> 'COUPON_UNIT' THEN
    RETURN NEW;
  END IF;
  SELECT l.id, l.status INTO v_listing, v_status
    FROM listing_item i JOIN listing l ON l.id = i.listing_id
   WHERE i.coupon_code_id = NEW.subject_id AND l.status IN ('PENDING', 'ACTIVE')
   LIMIT 1;
  IF FOUND THEN
    RAISE EXCEPTION 'coupon_code % cannot be released: listing % is %',
      NEW.subject_id, v_listing, v_status
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_coupon_hold_until_closed';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_coupon_hold_until_closed
  BEFORE UPDATE OF released_at ON reservation
  FOR EACH ROW EXECUTE FUNCTION f_coupon_hold_until_closed();
"""

PG_DOWN = """
DROP TRIGGER trg_coupon_hold_until_closed ON reservation;
DROP FUNCTION f_coupon_hold_until_closed();
DROP TRIGGER trg_step_order_gate ON operation_step;
DROP FUNCTION f_step_order_gate();
DROP TRIGGER trg_listing_status_transition ON listing;
DROP FUNCTION f_listing_status_transition();
DROP TRIGGER trg_delist_admissible ON operation;
DROP FUNCTION f_delist_admissible();
ALTER TABLE operation DROP CONSTRAINT ck_operation_actor_scope;
"""


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def _inflight_index(where: str) -> None:
    op.create_index(
        "uq_listing_inflight",
        "operation",
        ["subject_id"],
        unique=True,
        postgresql_where=sa.text(where),
        sqlite_where=sa.text(where),
    )


def upgrade() -> None:
    op.drop_index("uq_listing_inflight", table_name="operation")
    _inflight_index(INFLIGHT)
    if _is_postgresql():
        op.execute(PG_UP)


def downgrade() -> None:
    if _is_postgresql():
        op.execute(PG_DOWN)
    op.drop_index("uq_listing_inflight", table_name="operation")
    _inflight_index(INFLIGHT_0011)
