"""FEAT-13 operation status and webhooks: the reference guarantees, with the history written here.

Decision (docs/reference-findings.md): the pack lets the service write operation_event and checks at
commit that the history is contiguous, truthful and complete. Here the database writes the event
itself whenever an operation is created or changes status (trg_operation_event_write), and nothing
else may write, change or delete an event (trg_operation_event_guard). A silent status change, a
gap, a wrong from_status or an event without a change are then impossible by construction rather
than refused at commit, and callers have one thing to do: UPDATE operation.status. Who caused the
change and why travel in transaction settings, read by the trigger:

    SELECT set_config('app.operation_actor', 'PLATFORM_ADMIN', true);          -- default SYSTEM
    SELECT set_config('app.operation_event_metadata', '{"by": "adm_7"}', true); -- default {}
    SELECT set_config('app.operation_event_tx_hash', '0x…', true);              -- default NULL

Existing operations whose history does not end at their status get one backfilled event
(metadata {"backfill": true}).

Rules:

  both dialects
    T18 T24e uq_delivery_event  one first delivery per (endpoint, operation, event); replays exempt
  PostgreSQL only
    T9-T12 trg_operation_status_transition  a new operation is PENDING; then
           PENDING → SUBMITTED | MANUAL_REVIEW,
           SUBMITTED → CONFIRMED | CHAIN_REVERTED | ORPHANED | MANUAL_REVIEW,
           CONFIRMED → FINALIZED | ORPHANED | MANUAL_REVIEW (reorg),
           ORPHANED → SUBMITTED | MANUAL_REVIEW, MANUAL_REVIEW → SUBMITTED | CHAIN_REVERTED |
           FINALIZED; FINALIZED and CHAIN_REVERTED are final
    T1-T8 T13 trg_operation_event_write, trg_operation_event_guard
    T14-T16 ck_webhook_endpoint_url_https, ck_webhook_endpoint_secret_ref,
           ck_webhook_endpoint_event_types (a non-empty list of non-empty names)
    T17 T17b T17c trg_delivery_admissible  the event exists, the endpoint is ACTIVE, subscribed to
           the event type and belongs to the operation's merchant; delivery_seq has no gap
    T21-T24 trg_delivery_order_gate  delivery n is sent after n-1 is SENT or DEAD, per endpoint
    T24b-d trg_replay_valid  a replay repeats a DEAD delivery of the same event
    T25 T26 trg_delivery_immutable  SENT and DEAD are final; what was signed never changes
    T29 T29b trg_attempt_count_matches (at commit, from both tables) · T29c trg_attempt_append_only
    T30 f_backoff

The pack's rule that ftp:// passes (`NOT LIKE 'http://%'`), that the attempt counter is checked only
when an attempt row is added, that a missing previous delivery opens the gate, and that nothing
stops one merchant's operation from being sent to another merchant's endpoint, are closed here.

Upgrading fails if an existing row breaks a new CHECK (an endpoint URL that is not https, a
secret_ref that is not a kms:// reference, an empty event list).
"""

import sqlalchemy as sa
from alembic import op

revision = "0016_feat13_operation_status"
down_revision = "0015_feat12_deposit_mint"
branch_labels = None
depends_on = None

PG_UP = r"""
-- T9-T12 · the lifecycle; reorg may step back from CONFIRMED to ORPHANED
CREATE FUNCTION f_operation_status_transition() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'PENDING' THEN
      RAISE EXCEPTION 'operation % must start PENDING, not %', NEW.id, NEW.status
        USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_operation_status_transition';
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.status = OLD.status THEN
    RETURN NEW;
  END IF;
  IF NOT (
       (OLD.status = 'PENDING' AND NEW.status IN ('SUBMITTED', 'MANUAL_REVIEW'))
    OR (OLD.status = 'SUBMITTED'
        AND NEW.status IN ('CONFIRMED', 'CHAIN_REVERTED', 'ORPHANED', 'MANUAL_REVIEW'))
    OR (OLD.status = 'CONFIRMED' AND NEW.status IN ('FINALIZED', 'ORPHANED', 'MANUAL_REVIEW'))
    OR (OLD.status = 'ORPHANED' AND NEW.status IN ('SUBMITTED', 'MANUAL_REVIEW'))
    OR (OLD.status = 'MANUAL_REVIEW'
        AND NEW.status IN ('SUBMITTED', 'CHAIN_REVERTED', 'FINALIZED'))) THEN
    RAISE EXCEPTION 'operation %: % → % is not allowed', OLD.id, OLD.status, NEW.status
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_operation_status_transition';
  END IF;
  IF NEW.status = 'FINALIZED' AND NEW.finalized_at IS NULL THEN
    NEW.finalized_at := now();
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_operation_status_transition
  BEFORE INSERT OR UPDATE OF status ON operation
  FOR EACH ROW EXECUTE FUNCTION f_operation_status_transition();

-- T1-T8 T13 · the history is written by the database, one event per change, seq without gaps
CREATE FUNCTION f_operation_event_write() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_seq integer;
BEGIN
  IF TG_OP = 'UPDATE' AND NEW.status = OLD.status THEN
    RETURN NULL;
  END IF;
  SELECT coalesce(max(seq), 0) + 1 INTO v_seq FROM operation_event WHERE operation_id = NEW.id;
  INSERT INTO operation_event (id, operation_id, seq, from_status, to_status, actor, tx_hash,
                               metadata)
  VALUES (NEW.id || '#' || v_seq, NEW.id, v_seq,
          CASE WHEN TG_OP = 'UPDATE' THEN OLD.status END, NEW.status,
          coalesce(nullif(current_setting('app.operation_actor', true), ''), 'SYSTEM'),
          nullif(current_setting('app.operation_event_tx_hash', true), ''),
          coalesce(nullif(current_setting('app.operation_event_metadata', true), ''), '{}')::json);
  RETURN NULL;
END $$;

CREATE TRIGGER trg_operation_event_write
  AFTER INSERT OR UPDATE OF status ON operation
  FOR EACH ROW EXECUTE FUNCTION f_operation_event_write();

CREATE FUNCTION f_operation_event_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  -- depth 1 is a statement aimed at operation_event itself; the writer above runs at depth 2,
  -- and so does the cascade when an operation is deleted
  IF pg_trigger_depth() < 2 THEN
    RAISE EXCEPTION 'operation_event is written by the database when operation.status changes'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_operation_event_guard',
            HINT = 'UPDATE operation.status; app.operation_actor and _metadata say who and why';
  END IF;
  IF TG_OP = 'UPDATE' THEN
    RAISE EXCEPTION 'operation_event %: history is not edited', OLD.id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_operation_event_guard';
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;

-- trg_operation_event_guard is created after the backfill at the end (PG_GUARD)

-- T16 · a non-empty JSON list of non-empty event names
CREATE FUNCTION f_is_event_type_list(p json) RETURNS boolean
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT json_typeof(p) = 'array' AND json_array_length(p) >= 1
     AND NOT EXISTS (SELECT 1 FROM json_array_elements(p) e
                      WHERE json_typeof(e) <> 'string' OR e #>> '{}' = '')
$$;

ALTER TABLE webhook_endpoint
  ADD CONSTRAINT ck_webhook_endpoint_url_https CHECK (url ~ '^https://[^/\s]+'),
  ADD CONSTRAINT ck_webhook_endpoint_secret_ref
    CHECK (length(secret_ref) <= 200 AND secret_ref ~ '^kms://[A-Za-z0-9/_.:+=-]+$'),
  ADD CONSTRAINT ck_webhook_endpoint_event_types CHECK (f_is_event_type_list(event_types));

-- T17 T17b T17c · what may be queued for a merchant
CREATE FUNCTION f_delivery_admissible() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE e record; v_scope text; v_next integer;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM operation_event
                  WHERE operation_id = NEW.operation_id AND seq = NEW.event_seq) THEN
    RAISE EXCEPTION 'operation % has no event %', NEW.operation_id, NEW.event_seq
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_delivery_admissible',
            HINT = 'a webhook reports something that happened';
  END IF;
  SELECT merchant_id, status, event_types INTO e FROM webhook_endpoint WHERE id = NEW.endpoint_id;
  SELECT actor_scope INTO v_scope FROM operation WHERE id = NEW.operation_id;
  IF v_scope IS DISTINCT FROM 'merchant:' || e.merchant_id THEN
    RAISE EXCEPTION 'endpoint % belongs to merchant %, operation % to %',
      NEW.endpoint_id, e.merchant_id, NEW.operation_id, v_scope
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_delivery_admissible',
            HINT = 'a merchant hears about its own operations only';
  END IF;
  IF NEW.replay_of IS NULL AND (e.status <> 'ACTIVE' OR NOT EXISTS (
        SELECT 1 FROM json_array_elements_text(e.event_types) t WHERE t = NEW.event_type)) THEN
    RAISE EXCEPTION 'endpoint % is % and does not take % now',
      NEW.endpoint_id, e.status, NEW.event_type
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_delivery_admissible';
  END IF;
  SELECT coalesce(max(delivery_seq), 0) + 1 INTO v_next FROM webhook_delivery
   WHERE endpoint_id = NEW.endpoint_id AND operation_id = NEW.operation_id;
  IF NEW.delivery_seq > v_next THEN  -- a repeat of an existing number is uq_delivery_seq's
    RAISE EXCEPTION 'delivery % of operation % to %: the next delivery_seq is %',
      NEW.delivery_seq, NEW.operation_id, NEW.endpoint_id, v_next
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_delivery_order_gate';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_delivery_admissible
  BEFORE INSERT ON webhook_delivery
  FOR EACH ROW EXECUTE FUNCTION f_delivery_admissible();

-- T24b-d · a replay repeats a DEAD delivery, the same event to the same endpoint
CREATE FUNCTION f_replay_valid() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE o record;
BEGIN
  IF NEW.replay_of IS NULL THEN
    RETURN NEW;
  END IF;
  SELECT status, endpoint_id, operation_id, event_seq INTO o
    FROM webhook_delivery WHERE id = NEW.replay_of;
  IF o.status IS DISTINCT FROM 'DEAD' THEN
    RAISE EXCEPTION 'delivery % is %, only a DEAD delivery is replayed', NEW.replay_of, o.status
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_replay_valid';
  END IF;
  IF (NEW.endpoint_id, NEW.operation_id, NEW.event_seq)
     IS DISTINCT FROM (o.endpoint_id, o.operation_id, o.event_seq) THEN
    RAISE EXCEPTION 'a replay of % repeats its endpoint, operation and event', NEW.replay_of
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_replay_valid';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_replay_valid
  BEFORE INSERT ON webhook_delivery
  FOR EACH ROW EXECUTE FUNCTION f_replay_valid();

-- T25 T26 · a delivery is evidence of what we sent
CREATE FUNCTION f_delivery_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status IN ('SENT', 'DEAD') THEN
    RAISE EXCEPTION 'delivery % is %: it does not change; a replay is a new row', OLD.id, OLD.status
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_delivery_immutable';
  END IF;
  IF (NEW.endpoint_id, NEW.operation_id, NEW.event_seq, NEW.delivery_seq, NEW.event_type,
      NEW.payload::text, NEW.signature, NEW.signed_at, NEW.replay_of)
     IS DISTINCT FROM
     (OLD.endpoint_id, OLD.operation_id, OLD.event_seq, OLD.delivery_seq, OLD.event_type,
      OLD.payload::text, OLD.signature, OLD.signed_at, OLD.replay_of) THEN
    RAISE EXCEPTION 'delivery %: what was signed does not change', OLD.id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_delivery_immutable';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_delivery_immutable
  BEFORE UPDATE ON webhook_delivery
  FOR EACH ROW EXECUTE FUNCTION f_delivery_immutable();

-- T21-T24 · one operation's webhooks reach an endpoint in order; DEAD does not block
CREATE FUNCTION f_delivery_order_gate() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_prev text;
BEGIN
  IF NEW.status <> 'IN_FLIGHT' OR OLD.status = 'IN_FLIGHT' OR NEW.delivery_seq = 1
     OR NEW.claimed_at IS NULL THEN  -- an unclaimed IN_FLIGHT is ck_delivery_claim's
    RETURN NEW;
  END IF;
  SELECT status INTO v_prev FROM webhook_delivery
   WHERE endpoint_id = NEW.endpoint_id AND operation_id = NEW.operation_id
     AND delivery_seq = NEW.delivery_seq - 1;
  IF v_prev IS DISTINCT FROM 'SENT' AND v_prev IS DISTINCT FROM 'DEAD' THEN
    RAISE EXCEPTION 'delivery % of % waits: delivery % is %', NEW.delivery_seq, NEW.operation_id,
      NEW.delivery_seq - 1, coalesce(v_prev, 'missing')
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_delivery_order_gate';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_delivery_order_gate
  BEFORE UPDATE OF status ON webhook_delivery
  FOR EACH ROW EXECUTE FUNCTION f_delivery_order_gate();

-- T29 T29b · the attempt counter is the number of attempt rows, at commit, from both sides
CREATE FUNCTION f_attempt_count_matches() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_id text; v_claimed integer; v_rows integer;
BEGIN
  v_id := to_jsonb(NEW)
          ->> CASE WHEN TG_TABLE_NAME = 'webhook_delivery' THEN 'id' ELSE 'delivery_id' END;
  SELECT attempt INTO v_claimed FROM webhook_delivery WHERE id = v_id;
  SELECT count(*) INTO v_rows FROM webhook_delivery_attempt WHERE delivery_id = v_id;
  IF v_claimed IS NOT NULL AND v_claimed <> v_rows THEN  -- a removed delivery takes its attempts
    RAISE EXCEPTION 'delivery % counts % attempts but has % attempt rows', v_id, v_claimed, v_rows
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_attempt_count_matches';
  END IF;
  RETURN NULL;
END $$;

CREATE CONSTRAINT TRIGGER trg_attempt_count_matches
  AFTER INSERT ON webhook_delivery_attempt
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION f_attempt_count_matches();
CREATE CONSTRAINT TRIGGER trg_attempt_count_matches
  AFTER INSERT OR UPDATE OF attempt ON webhook_delivery
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION f_attempt_count_matches();

-- T29c · an attempt and its answer are evidence
CREATE FUNCTION f_attempt_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' AND pg_trigger_depth() >= 2 THEN
    RETURN OLD;  -- the cascade from its delivery
  END IF;
  RAISE EXCEPTION 'webhook_delivery_attempt %: attempts are not edited or removed', OLD.id
    USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_attempt_append_only';
END $$;

CREATE TRIGGER trg_attempt_append_only
  BEFORE UPDATE OR DELETE ON webhook_delivery_attempt
  FOR EACH ROW EXECUTE FUNCTION f_attempt_append_only();

-- T30 · retry n waits 2^(n-1) minutes, at most an hour
CREATE FUNCTION f_backoff(p_attempt integer) RETURNS interval
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT least(power(2, greatest(p_attempt - 1, 0))::int, 60) * interval '1 minute'
$$;

-- histories that do not end at the operation's status get one backfilled event
INSERT INTO operation_event (id, operation_id, seq, from_status, to_status, actor, metadata)
SELECT o.id || '#' || (coalesce(l.seq, 0) + 1), o.id, coalesce(l.seq, 0) + 1,
       l.to_status, o.status, 'SYSTEM', '{"backfill": true}'::json
  FROM operation o
  LEFT JOIN LATERAL (SELECT seq, to_status FROM operation_event e
                      WHERE e.operation_id = o.id ORDER BY seq DESC LIMIT 1) l ON true
 WHERE l.to_status IS DISTINCT FROM o.status;
"""

PG_GUARD = """
CREATE TRIGGER trg_operation_event_guard
  BEFORE INSERT OR UPDATE OR DELETE ON operation_event
  FOR EACH ROW EXECUTE FUNCTION f_operation_event_guard();
"""

PG_DOWN = """
DROP FUNCTION f_backoff(integer);
DROP TRIGGER trg_attempt_append_only ON webhook_delivery_attempt;
DROP FUNCTION f_attempt_append_only();
DROP TRIGGER trg_attempt_count_matches ON webhook_delivery;
DROP TRIGGER trg_attempt_count_matches ON webhook_delivery_attempt;
DROP FUNCTION f_attempt_count_matches();
DROP TRIGGER trg_delivery_order_gate ON webhook_delivery;
DROP FUNCTION f_delivery_order_gate();
DROP TRIGGER trg_delivery_immutable ON webhook_delivery;
DROP FUNCTION f_delivery_immutable();
DROP TRIGGER trg_replay_valid ON webhook_delivery;
DROP FUNCTION f_replay_valid();
DROP TRIGGER trg_delivery_admissible ON webhook_delivery;
DROP FUNCTION f_delivery_admissible();
ALTER TABLE webhook_endpoint
  DROP CONSTRAINT ck_webhook_endpoint_event_types,
  DROP CONSTRAINT ck_webhook_endpoint_secret_ref,
  DROP CONSTRAINT ck_webhook_endpoint_url_https;
DROP FUNCTION f_is_event_type_list(json);
DROP TRIGGER trg_operation_event_guard ON operation_event;
DROP FUNCTION f_operation_event_guard();
DROP TRIGGER trg_operation_event_write ON operation;
DROP FUNCTION f_operation_event_write();
DROP TRIGGER trg_operation_status_transition ON operation;
DROP FUNCTION f_operation_status_transition();
"""


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_index(
        "uq_delivery_event",
        "webhook_delivery",
        ["endpoint_id", "operation_id", "event_seq"],
        unique=True,
        postgresql_where=sa.text("replay_of IS NULL"),
        sqlite_where=sa.text("replay_of IS NULL"),
    )
    if _is_postgresql():
        op.execute(PG_UP)  # ends with the backfill, written before the guard exists
        op.execute(PG_GUARD)


def downgrade() -> None:
    if _is_postgresql():
        op.execute(PG_DOWN)
    op.drop_index("uq_delivery_event", table_name="webhook_delivery")
