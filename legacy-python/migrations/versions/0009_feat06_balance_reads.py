"""FEAT-06 point balance reads on the lot model: forecast, expiry report, notices, watermark.

reference/feat-06 is read-only: functions that answer "how much, at time t" from the balance table,
the expiry-notice bookkeeping, and the projection watermark. Ported onto the lots of 0008
(docs/reference-findings.md has why there are no buckets):

  T5   f_available_points never goes below zero: a lot can expire while a reservation on it is
       still open. The reference test asked a case that came out at 50 and never exercised this;
       tests/sql/feat06_acceptance.sql asks one that would be -250.
  T7 T8 f_expiry_forecast(point type, holder, as_of, horizon_days): per day, what is still alive
       at as_of and expires within the horizon. Days are UTC dates, as in the reference.
  T9   f_expired_total(point type, as_of): what has expired by as_of, all holders.
       Both use f_point_lot_expired, the complement of 0008's f_point_lot_active for a started
       lot: a D lot is expired at exactly expireDate, an A/B/C lot only after it (T9b).
  T13  trg_notice_not_expired: no "expires soon" notice for a lot that has already expired at
       notified_at.
  T14  trg_watermark_monotonic: projection_watermark.last_block never moves back.
  T20  needs no new index: point_lot_balance's primary key starts with (point_type_id,
       holder_kind, holder_id), the equality part of every balance query, and EXPLAIN shows the
       planner using it. The reference's ix_bucket_live existed because buckets were keyed by an id.

No read function calls now(): each takes the time it answers for (T19), so the past and the future
are the same query, and tests repeat.

PostgreSQL only: the functions and triggers.
"""

from alembic import op

revision = "0009_feat06_balance_reads"
down_revision = "0008_feat05_lots_inventory"
branch_labels = None
depends_on = None

_AVAILABLE = r"""
CREATE OR REPLACE FUNCTION f_available_points(p_point_type_id text, p_holder_kind text,
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

  RETURN {result};
END $$;
"""
AVAILABLE_0009 = _AVAILABLE.format(result="greatest(v_total - v_held, 0)")
AVAILABLE_0008 = _AVAILABLE.format(result="v_total - v_held")

PG_UP = r"""
-- Expired at p_at, per NewPointToken v3: D (4) at exactly expire; A, B, C (1-3) only after it.
CREATE FUNCTION f_point_lot_expired(p_business smallint, p_expire timestamptz, p_at timestamptz)
RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT p_at > p_expire OR (p_at = p_expire AND p_business = 4)
$$;

CREATE FUNCTION f_expiry_forecast(p_point_type_id text, p_holder_kind text, p_holder_id text,
                                  p_as_of timestamptz, p_horizon_days integer)
RETURNS TABLE (expires_on date, amount numeric) LANGUAGE sql STABLE AS $$
  SELECT (l.expire_at AT TIME ZONE 'UTC')::date, sum(b.amount::numeric)
    FROM point_lot_balance b
    JOIN point_lot l USING (point_type_id, lot_key)
    LEFT JOIN point_type_chain c ON c.point_type_id = b.point_type_id
   WHERE b.point_type_id = p_point_type_id
     AND b.holder_kind = p_holder_kind AND b.holder_id = p_holder_id
     AND b.amount <> '0'
     AND NOT f_point_lot_expired(c.business_model, l.expire_at, p_as_of)
     AND l.expire_at <= p_as_of + make_interval(days => p_horizon_days)
   GROUP BY 1
   ORDER BY 1
$$;

CREATE FUNCTION f_expired_total(p_point_type_id text, p_as_of timestamptz)
RETURNS numeric LANGUAGE sql STABLE AS $$
  SELECT coalesce(sum(b.amount::numeric), 0)
    FROM point_lot_balance b
    JOIN point_lot l USING (point_type_id, lot_key)
    LEFT JOIN point_type_chain c ON c.point_type_id = b.point_type_id
   WHERE b.point_type_id = p_point_type_id
     AND f_point_lot_expired(c.business_model, l.expire_at, p_as_of)
$$;

CREATE FUNCTION f_notice_not_expired() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_expire timestamptz; v_business smallint;
BEGIN
  SELECT l.expire_at, c.business_model INTO v_expire, v_business
    FROM point_lot l LEFT JOIN point_type_chain c ON c.point_type_id = l.point_type_id
   WHERE l.point_type_id = NEW.point_type_id AND l.lot_key = NEW.lot_key;
  IF f_point_lot_expired(v_business, v_expire, NEW.notified_at) THEN
    RAISE EXCEPTION 'lot % of % expired at %, too late to warn about it',
      NEW.lot_key, NEW.point_type_id, v_expire
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_notice_not_expired';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_notice_not_expired
  BEFORE INSERT ON expiry_notice
  FOR EACH ROW EXECUTE FUNCTION f_notice_not_expired();

CREATE FUNCTION f_watermark_monotonic() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.last_block < OLD.last_block THEN
    RAISE EXCEPTION 'projection watermark cannot move back from % to %',
      OLD.last_block, NEW.last_block
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_watermark_monotonic',
            HINT = 'a projection that moves back means a replay the read side should not see';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_watermark_monotonic
  BEFORE UPDATE ON projection_watermark
  FOR EACH ROW EXECUTE FUNCTION f_watermark_monotonic();
"""

PG_DOWN = """
DROP TRIGGER trg_watermark_monotonic ON projection_watermark;
DROP FUNCTION f_watermark_monotonic();
DROP TRIGGER trg_notice_not_expired ON expiry_notice;
DROP FUNCTION f_notice_not_expired();
DROP FUNCTION f_expired_total(text, timestamptz);
DROP FUNCTION f_expiry_forecast(text, text, text, timestamptz, integer);
DROP FUNCTION f_point_lot_expired(smallint, timestamptz, timestamptz);
"""


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def upgrade() -> None:
    if _is_postgresql():
        op.execute(PG_UP)
        op.execute(AVAILABLE_0009)


def downgrade() -> None:
    if _is_postgresql():
        op.execute(AVAILABLE_0008)
        op.execute(PG_DOWN)
