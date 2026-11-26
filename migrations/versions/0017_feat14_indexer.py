"""FEAT-14: the indexer and the projector (reference/feat-14, adapted to the delivered contracts).

The pack's three promises, held by the database:

  L8          one indexer: the cursor moves only under a live lease, held by the process that
              moves it; the cursor moves in the transaction that writes the blocks and events
              it covers (checked at commit).
  RFD 5       a reorg is detected by hash: a block whose parent is not the live block below it
              cannot be stored.
  RFD 6 §3.2  the projections can be deleted and rebuilt from chain_event with the same result:
              f_apply_event is the one place that turns an event into projection rows, it uses
              only the event and identity tables, and f_replay_projections rebuilds from it.

Adapted to the delivered contracts (abi/Coupon.json, the point token):
  The Coupon contract is ERC-1155: TransferSingle / TransferBatch carry a type id and an amount,
  never a code. coupon_holding is therefore an amount per (coupon type, holder), as the backend
  already modelled it, not a row per code as in the pack. coupon_code_id leaves it. The only
  per-code fact on chain is CouponRedeemed.redeemCodes (the code_ref, see FEAT-07): it is
  projected into coupon_redemption. coupon_code.state stays written by the redeem operation
  (FEAT-11, final once REDEEMED); the projector never rewrites it.
  Normalised args written by the decoder, addresses as 0x-hex, amounts as decimal strings:
    CouponTransfer   {"from", "to", "ids": [...], "values": [...]}   Coupon TransferSingle/Batch
    PointTransfer    {"from", "to", "value"}                          point token Transfer
    CouponRedeemed   {"typeId", "owner", "amount", "redeemCodes": [code_ref, ...]}
  The coupon type is found from (event address, typeId) in coupon_type_chain, the point type from
  the event address in point_type_chain. An address is a holder through wallet; the zero address
  is mint or burn; any other address (the marketplace contract holding listed coupons) is held as
  holder_kind EXTERNAL, holder_id the address. Wallet addresses are stored lowercase
  (ck_wallet_address_lower), so the lookup uses the unique index on address.

Beyond the pack (each is a gap of the pack, see docs/reference-findings.md):
  trg_cursor_needs_lease / trg_cursor_lease  the lease names a process: the one that moves the
                     cursor sets app.indexer_owner and must be the lease owner; a live lease
                     cannot be taken or released by another process
  trg_cursor_rewind  the cursor steps back only by a ROLLED_BACK reorg_log row of this
                     transaction for exactly that step (the pack trusted app.reorg_in_progress)
  trg_reorg_log_facts / trg_reorg_outcome_matches_depth  old_tip, finality_depth and
                     orphaned_events are what the database sees; MANUAL_REVIEW iff deeper than N
                     or a final event is in range (the pack's own f_handle_reorg chose
                     MANUAL_REVIEW for final events that its trigger then refused)
  trg_reorg_log_append_only, trg_raw_block_guard, trg_chain_event_guard  history is not edited:
                     events keep what the chain said; args change only while unapplied;
                     orphaned_at and finalized_at are set once
  trg_event_finality an event is marked final only when applied and at least N below the cursor
  trg_event_matches_block  an event carries its block's number and time, on a live block
  trg_orphan_consistent    a live event sits on a live block, an orphaned one on an orphaned block
  trg_block_within_cursor / trg_cursor_on_live_block  no live block above the cursor; the cursor
                     names a live block
  An advisory transaction lock serialises apply, replay and reorg (open in the pack).

Upgrading fails if a wallet address is stored with upper-case letters:
    SELECT id, address FROM wallet WHERE address <> lower(address);
Upgrading deletes coupon_holding's rows: it is a projection, rebuilt by
SELECT f_replay_projections(). Downgrading drops coupon_redemption.
"""

import sqlalchemy as sa
from alembic import op

revision = "0017_feat14_indexer"
down_revision = "0016_feat13_operation_status"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
HASH = sa.String(66)
AMOUNT = sa.String(78)
HOLDERS = "holder_kind IN ('MERCHANT', 'CUSTOMER', 'PLATFORM', 'EXTERNAL')"
OLD_HOLDERS = "holder_kind IN ('MERCHANT', 'CUSTOMER')"

PG_UP = r"""
-- an address is stored in one spelling, so the projector finds a wallet through uq on address
ALTER TABLE wallet ADD CONSTRAINT ck_wallet_address_lower CHECK (address = lower(address));
ALTER TABLE coupon_holding ADD CONSTRAINT ck_coupon_holding_quantity_uint256
  CHECK (f_is_uint256(quantity, true));
ALTER TABLE point_balance ADD CONSTRAINT ck_point_balance_amount_uint256
  CHECK (f_is_uint256(amount, true));

-- T32 · N by the time asked, so changing it does not rewrite the past
CREATE FUNCTION f_finality_depth(p_at timestamptz) RETURNS integer LANGUAGE sql STABLE AS $$
  SELECT depth_blocks FROM finality_config
   WHERE effective_from <= p_at ORDER BY effective_from DESC LIMIT 1
$$;

CREATE FUNCTION f_indexer_owner() RETURNS text LANGUAGE sql STABLE AS $$
  SELECT nullif(current_setting('app.indexer_owner', true), '')
$$;

-- T2-T6 · L8: one process holds the lease and only it moves the cursor
CREATE FUNCTION f_indexer_cursor_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_me text := f_indexer_owner();
BEGIN
  IF NEW.lease_owner IS DISTINCT FROM OLD.lease_owner
     OR NEW.lease_until IS DISTINCT FROM OLD.lease_until THEN
    IF NEW.lease_owner IS DISTINCT FROM OLD.lease_owner AND NEW.last_block <> OLD.last_block THEN
      RAISE EXCEPTION 'the lease changes hands and the cursor moves in one statement'
        USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_cursor_needs_lease',
              HINT = 'take the lease, then move the cursor in the next statement';
    END IF;
    IF OLD.lease_owner IS NOT NULL AND OLD.lease_until > now()
       AND OLD.lease_owner IS DISTINCT FROM v_me THEN
      RAISE EXCEPTION 'the lease is held by % until %', OLD.lease_owner, OLD.lease_until
        USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_cursor_lease';
    END IF;
    IF NEW.lease_owner IS NOT NULL AND NEW.lease_owner IS DISTINCT FROM v_me THEN
      RAISE EXCEPTION 'a lease is taken by the process itself (app.indexer_owner is %)',
        coalesce(v_me, 'not set')
        USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_cursor_lease';
    END IF;
  END IF;

  IF NEW.last_block = OLD.last_block
     AND NEW.last_block_hash IS NOT DISTINCT FROM OLD.last_block_hash THEN
    RETURN NEW;
  END IF;
  IF NEW.lease_owner IS NULL OR NEW.lease_until <= now() THEN
    RAISE EXCEPTION 'the cursor moves only under a live lease'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_cursor_needs_lease';
  END IF;
  IF NEW.lease_owner IS DISTINCT FROM v_me THEN
    RAISE EXCEPTION 'the lease is %''s, not this process''s (%)', NEW.lease_owner,
      coalesce(v_me, 'app.indexer_owner not set')
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_cursor_needs_lease';
  END IF;
  IF NEW.last_block < OLD.last_block
     OR (NEW.last_block = OLD.last_block
         AND NEW.last_block_hash IS DISTINCT FROM OLD.last_block_hash) THEN
    IF NOT EXISTS (
      SELECT 1 FROM reorg_log
       WHERE outcome = 'ROLLED_BACK' AND detected_at = now()
         AND fork_block = NEW.last_block + 1 AND old_tip = OLD.last_block) THEN
      RAISE EXCEPTION 'the cursor steps back from % to % only by a reorg rolled back now',
        OLD.last_block, NEW.last_block
        USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_cursor_rewind';
    END IF;
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_indexer_cursor_guard
  BEFORE UPDATE ON indexer_cursor
  FOR EACH ROW EXECUTE FUNCTION f_indexer_cursor_guard();

-- T7 T8 · RFD 5: the live chain is linked by hash, in both directions
CREATE FUNCTION f_block_links_to_parent() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_below text; v_above_parent text;
BEGIN
  IF NEW.orphaned_at IS NOT NULL OR NEW.parent_hash = NEW.block_hash THEN
    RETURN NEW;  -- an orphaned block links to nothing; its own parent is ck_not_own_parent's
  END IF;
  SELECT block_hash INTO v_below FROM raw_block
   WHERE block_number = NEW.block_number - 1 AND orphaned_at IS NULL;
  IF FOUND AND NEW.parent_hash <> v_below THEN
    RAISE EXCEPTION 'block % names parent % but the live block % is %', NEW.block_number,
      NEW.parent_hash, NEW.block_number - 1, v_below
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_block_links_to_parent',
            HINT = 'a reorg: orphan the old blocks first';
  END IF;
  SELECT parent_hash INTO v_above_parent FROM raw_block
   WHERE block_number = NEW.block_number + 1 AND orphaned_at IS NULL;
  IF FOUND AND v_above_parent <> NEW.block_hash THEN
    RAISE EXCEPTION 'the live block % does not descend from block %', NEW.block_number + 1,
      NEW.block_hash
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_block_links_to_parent';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_block_links_to_parent
  BEFORE INSERT ON raw_block
  FOR EACH ROW EXECUTE FUNCTION f_block_links_to_parent();

-- T10b · a block is what the chain said; it is orphaned once and never comes back
CREATE FUNCTION f_raw_block_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE'
     OR NEW.block_hash <> OLD.block_hash OR NEW.block_number <> OLD.block_number
     OR NEW.parent_hash <> OLD.parent_hash OR NEW."timestamp" <> OLD."timestamp"
     OR NEW.tx_count <> OLD.tx_count OR NEW.read_at <> OLD.read_at
     OR (OLD.orphaned_at IS NOT NULL AND NEW.orphaned_at IS DISTINCT FROM OLD.orphaned_at) THEN
    RAISE EXCEPTION 'raw_block %: only orphaned_at is set, once', OLD.block_hash
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_raw_block_guard';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_raw_block_guard
  BEFORE UPDATE OR DELETE ON raw_block
  FOR EACH ROW EXECUTE FUNCTION f_raw_block_guard();

-- T15b T15c · an event is read from a live block and carries that block's number and time
CREATE FUNCTION f_event_matches_block() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE b raw_block;
BEGIN
  SELECT * INTO b FROM raw_block WHERE block_hash = NEW.block_hash;
  IF NOT FOUND THEN
    RETURN NEW;  -- the foreign key's
  END IF;
  IF NEW.block_number <> b.block_number OR NEW.block_timestamp <> b."timestamp" THEN
    RAISE EXCEPTION 'event %: block % at % but its block is % at %', NEW.id, NEW.block_number,
      NEW.block_timestamp, b.block_number, b."timestamp"
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_event_matches_block';
  END IF;
  IF b.orphaned_at IS NOT NULL OR NEW.orphaned_at IS NOT NULL OR NEW.applied_at IS NOT NULL
     OR NEW.finalized_at IS NOT NULL THEN
    RAISE EXCEPTION 'event % is read from a live block, unapplied and not final', NEW.id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_event_matches_block';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_event_matches_block
  BEFORE INSERT ON chain_event
  FOR EACH ROW EXECUTE FUNCTION f_event_matches_block();

-- T15d T27 T27b · what the chain said stays; orphaned, finalized are set once; final is final
CREATE FUNCTION f_chain_event_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_tip bigint;
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'chain_event %: events are not deleted; a reorg orphans them', OLD.id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_chain_event_guard';
  END IF;
  IF NEW.orphaned_at IS NOT NULL AND OLD.finalized_at IS NOT NULL THEN
    RAISE EXCEPTION 'event % is final since % and cannot be orphaned', OLD.id, OLD.finalized_at
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_final_not_orphanable',
            HINT = 'a reorg deeper than N goes to MANUAL_REVIEW';
  END IF;
  IF NEW.id <> OLD.id OR NEW.block_number <> OLD.block_number OR NEW.block_hash <> OLD.block_hash
     OR NEW.tx_hash <> OLD.tx_hash OR NEW.log_index <> OLD.log_index
     OR NEW.address <> OLD.address OR NEW.topic0 <> OLD.topic0
     OR NEW.topics::text <> OLD.topics::text OR NEW.data <> OLD.data
     OR NEW.block_timestamp <> OLD.block_timestamp
     OR (OLD.operation_id IS NOT NULL AND NEW.operation_id IS DISTINCT FROM OLD.operation_id)
     OR (OLD.orphaned_at IS NOT NULL AND NEW.orphaned_at IS DISTINCT FROM OLD.orphaned_at)
     OR ((NEW.event_name <> OLD.event_name OR NEW.args::text IS DISTINCT FROM OLD.args::text)
         AND (OLD.applied_at IS NOT NULL OR NEW.applied_at IS NOT NULL)) THEN
    RAISE EXCEPTION 'chain_event %: what the chain said is kept; args change only unapplied',
      OLD.id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_chain_event_guard';
  END IF;
  IF NEW.finalized_at IS DISTINCT FROM OLD.finalized_at THEN
    IF OLD.finalized_at IS NOT NULL THEN
      RAISE EXCEPTION 'event % is final since %', OLD.id, OLD.finalized_at
        USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_event_finality';
    END IF;
    SELECT last_block INTO v_tip FROM indexer_cursor WHERE id = 'main';
    IF NEW.orphaned_at IS NOT NULL OR NEW.applied_at IS NULL
       OR NEW.block_number > coalesce(v_tip, 0) - f_finality_depth(now()) THEN
      RAISE EXCEPTION 'event % at block % is not % blocks below the cursor %', NEW.id,
        NEW.block_number, f_finality_depth(now()), v_tip
        USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_event_finality';
    END IF;
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_chain_event_guard
  BEFORE UPDATE OR DELETE ON chain_event
  FOR EACH ROW EXECUTE FUNCTION f_chain_event_guard();

-- T11 T36 · L8 at commit: the cursor covers every live block and event, names a live block,
-- and a live event sits on a live block (each row checks its own side; one trigger name
-- per table so a test can fire one side: SET CONSTRAINTS trg_event_consistent IMMEDIATE)
CREATE FUNCTION f_indexer_consistent() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE c indexer_cursor; v_n bigint; v_number bigint; v_hash text; v_orphaned timestamptz;
BEGIN
  SELECT * INTO c FROM indexer_cursor WHERE id = 'main';
  IF TG_TABLE_NAME = 'indexer_cursor' THEN
    IF NOT FOUND THEN
      RETURN NULL;
    END IF;
    IF c.last_block > 0 AND NOT EXISTS (
      SELECT 1 FROM raw_block
       WHERE block_number = c.last_block AND block_hash = c.last_block_hash
         AND orphaned_at IS NULL) THEN
      RAISE EXCEPTION 'the cursor names block % %, which is not a live block', c.last_block,
        c.last_block_hash
        USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_cursor_on_live_block';
    END IF;
    SELECT max(block_number) INTO v_n FROM chain_event
     WHERE orphaned_at IS NULL AND block_number > c.last_block;
    IF v_n IS NOT NULL THEN
      RAISE EXCEPTION 'a live event at block % is above the cursor %', v_n, c.last_block
        USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_event_within_cursor';
    END IF;
    SELECT max(block_number) INTO v_n FROM raw_block
     WHERE orphaned_at IS NULL AND block_number > c.last_block;
    IF v_n IS NOT NULL THEN
      RAISE EXCEPTION 'a live block % is above the cursor %', v_n, c.last_block
        USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_block_within_cursor';
    END IF;
    RETURN NULL;
  END IF;

  -- the row as it is now, not as it was when the event was queued
  IF TG_TABLE_NAME = 'chain_event' THEN
    SELECT block_number, block_hash, orphaned_at INTO v_number, v_hash, v_orphaned
      FROM chain_event WHERE id = NEW.id;
  ELSE
    SELECT block_number, block_hash, orphaned_at INTO v_number, v_hash, v_orphaned
      FROM raw_block WHERE block_hash = NEW.block_hash;
  END IF;
  IF NOT FOUND THEN
    RETURN NULL;
  END IF;
  IF v_orphaned IS NULL AND (c.id IS NULL OR v_number > c.last_block) THEN
    RAISE EXCEPTION '% at block % is above the cursor %', TG_TABLE_NAME, v_number,
      coalesce(c.last_block::text, '(no indexer_cursor row)')
      USING ERRCODE = 'check_violation',
            CONSTRAINT = CASE TG_TABLE_NAME WHEN 'chain_event' THEN 'trg_event_within_cursor'
                                            ELSE 'trg_block_within_cursor' END,
            HINT = 'L8: move the cursor in the transaction that writes what it covers';
  END IF;
  IF TG_TABLE_NAME = 'chain_event' THEN
    IF EXISTS (SELECT 1 FROM raw_block b WHERE b.block_hash = v_hash
                AND (b.orphaned_at IS NULL) <> (v_orphaned IS NULL)) THEN
      RAISE EXCEPTION 'event %: live or orphaned with its block', NEW.id
        USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_orphan_consistent';
    END IF;
  ELSIF EXISTS (SELECT 1 FROM chain_event e WHERE e.block_hash = v_hash
                 AND (e.orphaned_at IS NULL) <> (v_orphaned IS NULL)) THEN
    RAISE EXCEPTION 'block %: its events are live or orphaned with it', v_hash
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_orphan_consistent';
  END IF;
  RETURN NULL;
END $$;

CREATE CONSTRAINT TRIGGER trg_event_consistent
  AFTER INSERT OR UPDATE ON chain_event
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION f_indexer_consistent();
CREATE CONSTRAINT TRIGGER trg_block_consistent
  AFTER INSERT OR UPDATE ON raw_block
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION f_indexer_consistent();
CREATE CONSTRAINT TRIGGER trg_cursor_consistent
  AFTER INSERT OR UPDATE ON indexer_cursor
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION f_indexer_consistent();

-- T29-T31b · a reorg record states what the database sees, and is kept as written
CREATE FUNCTION f_reorg_log_facts() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_tip bigint; v_n integer; v_events integer; v_final integer;
BEGIN
  IF TG_OP <> 'INSERT' THEN
    RAISE EXCEPTION 'reorg_log %: a record of what happened is not edited', OLD.id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_reorg_log_append_only';
  END IF;
  IF NEW.depth IS DISTINCT FROM NEW.old_tip - NEW.fork_block + 1 OR NEW.fork_block < 1 THEN
    RETURN NEW;  -- ck_depth_matches / ck_reorg_fork_block_pos
  END IF;
  NEW.detected_at := now();
  SELECT last_block INTO v_tip FROM indexer_cursor WHERE id = 'main';
  v_n := f_finality_depth(now());
  SELECT count(*), count(*) FILTER (WHERE finalized_at IS NOT NULL) INTO v_events, v_final
    FROM chain_event WHERE block_number >= NEW.fork_block AND orphaned_at IS NULL;
  IF NEW.old_tip IS DISTINCT FROM v_tip OR NEW.finality_depth IS DISTINCT FROM v_n
     OR NEW.orphaned_events <> v_events THEN
    RAISE EXCEPTION 'reorg %: tip %, N %, % live events from block %, not % / % / %', NEW.id,
      v_tip, v_n, v_events, NEW.fork_block, NEW.old_tip, NEW.finality_depth, NEW.orphaned_events
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_reorg_log_facts';
  END IF;
  IF NEW.outcome IS DISTINCT FROM
     (CASE WHEN NEW.depth > v_n OR v_final > 0 THEN 'MANUAL_REVIEW' ELSE 'ROLLED_BACK' END) THEN
    RAISE EXCEPTION 'reorg % of depth % (N %, % final events) cannot be %', NEW.id, NEW.depth,
      v_n, v_final, NEW.outcome
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_reorg_outcome_matches_depth';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_reorg_log_facts
  BEFORE INSERT OR UPDATE OR DELETE ON reorg_log
  FOR EACH ROW EXECUTE FUNCTION f_reorg_log_facts();

-- the projector ----------------------------------------------------------------------------

CREATE FUNCTION f_holder_of(p_address text, OUT kind text, OUT id text)
LANGUAGE sql STABLE AS $$
  SELECT coalesce(w.owner_kind, 'EXTERNAL'), coalesce(w.owner_id, lower(p_address))
    FROM (SELECT 1) one LEFT JOIN wallet w ON w.address = lower(p_address)
$$;

CREATE FUNCTION f_is_zero_address(p_address text) RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
  SELECT lower(p_address) = '0x' || repeat('0', 40)
$$;

CREATE FUNCTION f_apply_fail(p_event text, p_name text, p_what text) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'event %: %', p_event, p_what
    USING ERRCODE = 'check_violation', CONSTRAINT = p_name;
END $$;

-- one holder's amount in a projection moves by v (negative: out); zero rows are removed
CREATE FUNCTION f_project_coupon(e chain_event, p_type text, p_address text, p_delta numeric)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE h record; v_have numeric;
BEGIN
  IF f_is_zero_address(p_address) THEN
    RETURN;
  END IF;
  h := f_holder_of(p_address);
  SELECT quantity::numeric INTO v_have FROM coupon_holding
   WHERE coupon_type_id = p_type AND holder_kind = h.kind AND holder_id = h.id FOR UPDATE;
  IF coalesce(v_have, 0) + p_delta < 0 THEN
    PERFORM f_apply_fail(e.id, 'apply_insufficient_balance',
      format('%s holds %s of %s, sends %s', p_address, coalesce(v_have, 0), p_type, -p_delta));
  ELSIF coalesce(v_have, 0) + p_delta = 0 THEN
    DELETE FROM coupon_holding
     WHERE coupon_type_id = p_type AND holder_kind = h.kind AND holder_id = h.id;
  ELSIF v_have IS NULL THEN
    INSERT INTO coupon_holding (id, coupon_type_id, holder_kind, holder_id, quantity,
                                chain_event_id, as_of_block, as_of_time)
    VALUES (p_type || ':' || h.kind || ':' || h.id, p_type, h.kind, h.id, p_delta::text,
            e.id, e.block_number, e.block_timestamp);
  ELSE
    UPDATE coupon_holding
       SET quantity = (v_have + p_delta)::text, chain_event_id = e.id,
           as_of_block = e.block_number, as_of_time = e.block_timestamp
     WHERE coupon_type_id = p_type AND holder_kind = h.kind AND holder_id = h.id;
  END IF;
END $$;

CREATE FUNCTION f_project_point(e chain_event, p_type text, p_address text, p_delta numeric)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE h record; v_have numeric;
BEGIN
  IF f_is_zero_address(p_address) THEN
    RETURN;
  END IF;
  h := f_holder_of(p_address);
  SELECT amount::numeric INTO v_have FROM point_balance
   WHERE point_type_id = p_type AND holder_kind = h.kind AND holder_id = h.id FOR UPDATE;
  IF coalesce(v_have, 0) + p_delta < 0 THEN
    PERFORM f_apply_fail(e.id, 'apply_insufficient_balance',
      format('%s holds %s of %s, sends %s', p_address, coalesce(v_have, 0), p_type, -p_delta));
  ELSIF coalesce(v_have, 0) + p_delta = 0 THEN
    DELETE FROM point_balance
     WHERE point_type_id = p_type AND holder_kind = h.kind AND holder_id = h.id;
  ELSIF v_have IS NULL THEN
    INSERT INTO point_balance (point_type_id, holder_kind, holder_id, amount, as_of_block)
    VALUES (p_type, h.kind, h.id, p_delta::text, e.block_number);
  ELSE
    UPDATE point_balance SET amount = (v_have + p_delta)::text, as_of_block = e.block_number
     WHERE point_type_id = p_type AND holder_kind = h.kind AND holder_id = h.id;
  END IF;
END $$;

-- T16-T19 · the one function that turns an event into projection rows
CREATE FUNCTION f_apply_event(p_event_id text) RETURNS boolean LANGUAGE plpgsql AS $$
DECLARE
  e chain_event; v_args jsonb; v_type text; v_code text; v_ref text; i integer;
  v_ids jsonb; v_values jsonb;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtext('indexer'));
  SELECT * INTO e FROM chain_event WHERE id = p_event_id FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM f_apply_fail(p_event_id, 'apply_unknown_event', 'no such chain_event');
  END IF;
  IF e.orphaned_at IS NOT NULL OR e.applied_at IS NOT NULL THEN
    RETURN false;
  END IF;
  v_args := e.args::jsonb;

  CASE e.event_name
  WHEN 'CouponTransfer' THEN
    v_ids := v_args -> 'ids';
    v_values := v_args -> 'values';
    IF jsonb_typeof(v_ids) IS DISTINCT FROM 'array'
       OR jsonb_typeof(v_values) IS DISTINCT FROM 'array'
       OR jsonb_array_length(v_ids) <> jsonb_array_length(v_values)
       OR jsonb_array_length(v_ids) = 0 THEN
      PERFORM f_apply_fail(e.id, 'apply_bad_args', 'ids and values are two lists of one length');
    END IF;
    FOR i IN 0 .. jsonb_array_length(v_ids) - 1 LOOP
      IF NOT f_is_uint256(v_values ->> i, false) THEN
        PERFORM f_apply_fail(e.id, 'apply_bad_args', 'a value is not a uint256');
      END IF;
      SELECT coupon_type_id INTO v_type FROM coupon_type_chain
       WHERE lower(contract_address) = lower(e.address) AND chain_type_id = v_ids ->> i;
      IF NOT FOUND THEN
        PERFORM f_apply_fail(e.id, 'apply_unknown_coupon_type',
          format('no coupon type %s on %s', v_ids ->> i, e.address));
      END IF;
      PERFORM f_project_coupon(e, v_type, v_args ->> 'from', -(v_values ->> i)::numeric);
      PERFORM f_project_coupon(e, v_type, v_args ->> 'to', (v_values ->> i)::numeric);
    END LOOP;

  WHEN 'PointTransfer' THEN
    IF NOT f_is_uint256(v_args ->> 'value', false) THEN
      PERFORM f_apply_fail(e.id, 'apply_bad_args', 'value is not a uint256');
    END IF;
    SELECT point_type_id INTO v_type FROM point_type_chain
     WHERE lower(contract_address) = lower(e.address);
    IF NOT FOUND THEN
      PERFORM f_apply_fail(e.id, 'apply_unknown_point_type', 'no point type at ' || e.address);
    END IF;
    PERFORM f_project_point(e, v_type, v_args ->> 'from', -(v_args ->> 'value')::numeric);
    PERFORM f_project_point(e, v_type, v_args ->> 'to', (v_args ->> 'value')::numeric);

  WHEN 'CouponRedeemed' THEN
    SELECT coupon_type_id INTO v_type FROM coupon_type_chain
     WHERE lower(contract_address) = lower(e.address) AND chain_type_id = v_args ->> 'typeId';
    IF NOT FOUND THEN
      PERFORM f_apply_fail(e.id, 'apply_unknown_coupon_type',
        format('no coupon type %s on %s', v_args ->> 'typeId', e.address));
    END IF;
    IF jsonb_typeof(v_args -> 'redeemCodes') IS DISTINCT FROM 'array'
       OR (v_args ->> 'amount') IS DISTINCT FROM jsonb_array_length(v_args -> 'redeemCodes')::text
    THEN
      PERFORM f_apply_fail(e.id, 'apply_bad_args', 'amount is the number of redeemCodes');
    END IF;
    FOR v_ref IN SELECT jsonb_array_elements_text(v_args -> 'redeemCodes') LOOP
      SELECT id INTO v_code FROM coupon_code
       WHERE code_ref = lower(v_ref) AND coupon_type_id = v_type;
      IF NOT FOUND THEN
        PERFORM f_apply_fail(e.id, 'apply_unknown_code',
          format('%s is no code of %s', v_ref, v_type));
      END IF;
      INSERT INTO coupon_redemption (coupon_code_id, redeemer_kind, redeemer_id, chain_event_id,
                                     as_of_block, as_of_time)
      SELECT v_code, h.kind, h.id, e.id, e.block_number, e.block_timestamp
        FROM f_holder_of(v_args ->> 'owner') h;
    END LOOP;

  ELSE
    NULL;  -- UNKNOWN: kept, marked applied so it does not wait forever, projections untouched
  END CASE;

  UPDATE chain_event SET applied_at = now() WHERE id = e.id;
  RETURN true;
END $$;

-- in chain order: (block_number, log_index)
CREATE FUNCTION f_apply_pending(p_limit integer DEFAULT 1000) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE r record; n integer := 0;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtext('indexer'));
  FOR r IN SELECT id FROM chain_event WHERE applied_at IS NULL AND orphaned_at IS NULL
            ORDER BY block_number, log_index LIMIT p_limit LOOP
    IF f_apply_event(r.id) THEN
      n := n + 1;
    END IF;
  END LOOP;
  RETURN n;
END $$;

-- T20-T23 · RFD 6 §3.2: delete every projection, apply every live event again
CREATE FUNCTION f_replay_projections() RETURNS integer LANGUAGE plpgsql AS $$
BEGIN
  PERFORM pg_advisory_xact_lock(hashtext('indexer'));
  DELETE FROM coupon_redemption;
  DELETE FROM coupon_holding;
  DELETE FROM point_balance;
  UPDATE chain_event SET applied_at = NULL WHERE orphaned_at IS NULL AND applied_at IS NOT NULL;
  RETURN f_apply_pending(2147483647);
END $$;

-- T24 T29 · orphan and replay, never undo event by event; deeper than N stops for a person
CREATE FUNCTION f_handle_reorg(p_fork_block bigint, p_owner text) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE c indexer_cursor; v_id text; v_n integer; v_events integer; v_final integer;
        v_outcome text;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtext('indexer'));
  SELECT * INTO c FROM indexer_cursor WHERE id = 'main' FOR UPDATE;
  IF NOT FOUND OR c.lease_owner IS DISTINCT FROM p_owner OR c.lease_until <= now()
     OR p_owner IS DISTINCT FROM f_indexer_owner() THEN
    RAISE EXCEPTION 'a reorg is handled by the process holding the lease (%), not %',
      c.lease_owner, p_owner
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_cursor_needs_lease';
  END IF;
  IF p_fork_block < 1 OR p_fork_block > c.last_block THEN
    RAISE EXCEPTION 'fork block % is not at or below the tip %', p_fork_block, c.last_block
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_reorg_log_facts';
  END IF;
  v_n := f_finality_depth(now());
  SELECT count(*), count(*) FILTER (WHERE finalized_at IS NOT NULL) INTO v_events, v_final
    FROM chain_event WHERE block_number >= p_fork_block AND orphaned_at IS NULL;
  v_outcome := CASE WHEN c.last_block - p_fork_block + 1 > v_n OR v_final > 0
                    THEN 'MANUAL_REVIEW' ELSE 'ROLLED_BACK' END;
  v_id := 'rg_' || p_fork_block || '_' || to_char(clock_timestamp(), 'YYYYMMDDHH24MISSUS');
  INSERT INTO reorg_log (id, fork_block, old_tip, depth, finality_depth, orphaned_events,
                         outcome, note)
  VALUES (v_id, p_fork_block, c.last_block, c.last_block - p_fork_block + 1, v_n, v_events,
          v_outcome,
          CASE WHEN v_final > 0 THEN v_final || ' final events in the orphaned range' END);
  IF v_outcome = 'MANUAL_REVIEW' THEN
    RETURN v_id;  -- nothing moves; a person decides
  END IF;

  UPDATE chain_event SET applied_at = NULL
   WHERE block_number >= p_fork_block AND orphaned_at IS NULL;
  UPDATE chain_event SET orphaned_at = now()
   WHERE block_number >= p_fork_block AND orphaned_at IS NULL;
  UPDATE raw_block SET orphaned_at = now()
   WHERE block_number >= p_fork_block AND orphaned_at IS NULL;
  UPDATE indexer_cursor
     SET last_block = p_fork_block - 1,
         last_block_hash = (SELECT block_hash FROM raw_block
                             WHERE block_number = p_fork_block - 1 AND orphaned_at IS NULL),
         updated_at = now()
   WHERE id = 'main';
  PERFORM f_replay_projections();
  RETURN v_id;
END $$;

-- T28 · after the cursor moves: applied events at least N below it become final
CREATE FUNCTION f_mark_finalized() RETURNS integer LANGUAGE plpgsql AS $$
DECLARE v_tip bigint; v_rows integer;
BEGIN
  SELECT last_block INTO v_tip FROM indexer_cursor WHERE id = 'main';
  UPDATE chain_event SET finalized_at = now()
   WHERE finalized_at IS NULL AND orphaned_at IS NULL AND applied_at IS NOT NULL
     AND block_number <= v_tip - f_finality_depth(now());
  GET DIAGNOSTICS v_rows = ROW_COUNT;
  RETURN v_rows;
END $$;
"""

PG_DOWN = r"""
DROP FUNCTION f_mark_finalized();
DROP FUNCTION f_handle_reorg(bigint, text);
DROP FUNCTION f_replay_projections();
DROP FUNCTION f_apply_pending(integer);
DROP FUNCTION f_apply_event(text);
DROP FUNCTION f_project_point(chain_event, text, text, numeric);
DROP FUNCTION f_project_coupon(chain_event, text, text, numeric);
DROP FUNCTION f_apply_fail(text, text, text);
DROP FUNCTION f_is_zero_address(text);
DROP FUNCTION f_holder_of(text);
DROP TRIGGER trg_reorg_log_facts ON reorg_log;
DROP FUNCTION f_reorg_log_facts();
DROP TRIGGER trg_cursor_consistent ON indexer_cursor;
DROP TRIGGER trg_block_consistent ON raw_block;
DROP TRIGGER trg_event_consistent ON chain_event;
DROP FUNCTION f_indexer_consistent();
DROP TRIGGER trg_chain_event_guard ON chain_event;
DROP FUNCTION f_chain_event_guard();
DROP TRIGGER trg_event_matches_block ON chain_event;
DROP FUNCTION f_event_matches_block();
DROP TRIGGER trg_raw_block_guard ON raw_block;
DROP FUNCTION f_raw_block_guard();
DROP TRIGGER trg_block_links_to_parent ON raw_block;
DROP FUNCTION f_block_links_to_parent();
DROP TRIGGER trg_indexer_cursor_guard ON indexer_cursor;
DROP FUNCTION f_indexer_cursor_guard();
DROP FUNCTION f_indexer_owner();
DROP FUNCTION f_finality_depth(timestamptz);
ALTER TABLE point_balance DROP CONSTRAINT ck_point_balance_amount_uint256;
ALTER TABLE coupon_holding DROP CONSTRAINT ck_coupon_holding_quantity_uint256;
ALTER TABLE wallet DROP CONSTRAINT ck_wallet_address_lower;
"""


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_index(
        "uq_block_live",
        "raw_block",
        ["block_number"],
        unique=True,
        postgresql_where=sa.text("orphaned_at IS NULL"),
        sqlite_where=sa.text("orphaned_at IS NULL"),
    )
    op.create_index("ix_block_number", "raw_block", ["block_number"])
    op.create_index(
        "ix_event_unapplied",
        "chain_event",
        ["block_number", "log_index"],
        postgresql_where=sa.text("applied_at IS NULL AND orphaned_at IS NULL"),
        sqlite_where=sa.text("applied_at IS NULL AND orphaned_at IS NULL"),
    )
    op.create_index("ix_event_by_block", "chain_event", ["block_number"])

    # a projection: rebuilt by f_replay_projections()
    op.execute("DELETE FROM coupon_holding")
    with op.batch_alter_table("coupon_holding") as batch:
        batch.drop_column("updated_at")
        batch.drop_column("applied_at")
        batch.drop_column("coupon_code_id")
        batch.alter_column("chain_event_id", existing_type=sa.Text(), nullable=False)
        batch.alter_column("as_of_block", existing_type=sa.BigInteger(), nullable=False)
        batch.alter_column("as_of_time", existing_type=TS, nullable=False)
        batch.drop_constraint("ck_coupon_holding_holder_kind_enum", type_="check")
        batch.create_check_constraint("ck_coupon_holding_holder_kind_enum", HOLDERS)
    with op.batch_alter_table("point_balance") as batch:
        batch.drop_constraint("ck_point_balance_holder_kind_enum", type_="check")
        batch.create_check_constraint("ck_point_balance_holder_kind_enum", HOLDERS)

    op.create_table(
        "coupon_redemption",
        sa.Column("coupon_code_id", sa.Text(), sa.ForeignKey("coupon_code.id"), primary_key=True),
        sa.Column("redeemer_kind", sa.String(40), nullable=False),
        sa.Column("redeemer_id", sa.Text(), nullable=False),
        sa.Column("chain_event_id", sa.Text(), sa.ForeignKey("chain_event.id"), nullable=False),
        sa.Column("as_of_block", sa.BigInteger(), nullable=False),
        sa.Column("as_of_time", TS, nullable=False),
        sa.CheckConstraint(
            "redeemer_kind IN ('MERCHANT', 'CUSTOMER', 'PLATFORM', 'EXTERNAL')",
            name="ck_coupon_redemption_redeemer_kind_enum",
        ),
    )

    if _is_postgresql():
        op.execute(PG_UP)


def downgrade() -> None:
    if _is_postgresql():
        op.execute(PG_DOWN)
    op.drop_table("coupon_redemption")
    op.execute("DELETE FROM coupon_holding")
    op.execute("DELETE FROM point_balance WHERE NOT (" + OLD_HOLDERS + ")")
    with op.batch_alter_table("point_balance") as batch:
        batch.drop_constraint("ck_point_balance_holder_kind_enum", type_="check")
        batch.create_check_constraint("ck_point_balance_holder_kind_enum", OLD_HOLDERS)
    with op.batch_alter_table("coupon_holding") as batch:
        batch.drop_constraint("ck_coupon_holding_holder_kind_enum", type_="check")
        batch.create_check_constraint("ck_coupon_holding_holder_kind_enum", OLD_HOLDERS)
        batch.alter_column("as_of_time", existing_type=TS, nullable=True)
        batch.alter_column("as_of_block", existing_type=sa.BigInteger(), nullable=True)
        batch.alter_column("chain_event_id", existing_type=sa.Text(), nullable=True)
        batch.add_column(sa.Column("coupon_code_id", sa.Text(), nullable=True))
        batch.create_foreign_key(
            "coupon_holding_coupon_code_id_fkey", "coupon_code", ["coupon_code_id"], ["id"]
        )
        batch.add_column(sa.Column("applied_at", TS, nullable=True, server_default=sa.func.now()))
        batch.add_column(sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()))
    op.drop_index("ix_event_by_block", table_name="chain_event")
    op.drop_index("ix_event_unapplied", table_name="chain_event")
    op.drop_index("ix_block_number", table_name="raw_block")
    op.drop_index("uq_block_live", table_name="raw_block")
