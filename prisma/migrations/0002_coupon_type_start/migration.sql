BEGIN;

ALTER TABLE coupon_type ADD COLUMN starts_at timestamptz;

-- For existing on-chain types, populate this session-local table from verified
-- getCouponData(typeId).startDate before running the migration in that session.
CREATE TEMP TABLE IF NOT EXISTS coupon_type_start_backfill (
  coupon_type_id text PRIMARY KEY,
  starts_at timestamptz NOT NULL
) ON COMMIT DROP;

UPDATE coupon_type t SET starts_at = b.starts_at
FROM coupon_type_start_backfill b
WHERE t.id = b.coupon_type_id;

UPDATE coupon_type t SET starts_at = t.created_at
WHERE t.starts_at IS NULL
  AND t.contract_address IS NULL AND t.chain_type_id IS NULL
  AND NOT EXISTS (SELECT 1 FROM coupon_type_chain c WHERE c.coupon_type_id = t.id);

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM coupon_type WHERE starts_at IS NULL) THEN
    RAISE EXCEPTION 'Missing verified startDate for existing on-chain coupon types'
      USING HINT = 'Populate coupon_type_start_backfill in the same session from getCouponData(typeId).startDate and rerun; no changes have been committed.';
  END IF;
END $$;

ALTER TABLE coupon_type ALTER COLUMN starts_at SET NOT NULL;
ALTER TABLE coupon_type ADD CONSTRAINT ck_coupon_type_date_range CHECK (starts_at < expires_at);

-- The shared legacy merchant trigger raises an unnamed 23514. Give the coupon
-- trigger a constraint name so API mapping cannot swallow unrelated CHECKs.
CREATE FUNCTION f_coupon_type_merchant_active() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE merchant_state merchant_status;
BEGIN
  SELECT status INTO merchant_state FROM merchant WHERE id = NEW.merchant_id FOR SHARE;
  IF merchant_state IS NOT NULL AND merchant_state <> 'ACTIVE' THEN
    RAISE EXCEPTION 'Merchant must be ACTIVE to create coupon types'
      USING ERRCODE = '23514', CONSTRAINT = 'ck_coupon_type_merchant_active';
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER trg_coupon_type_merchant_active ON coupon_type;
CREATE TRIGGER trg_coupon_type_merchant_active BEFORE INSERT ON coupon_type
FOR EACH ROW EXECUTE FUNCTION f_coupon_type_merchant_active();

COMMIT;
