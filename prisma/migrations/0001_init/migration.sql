-- =====================================================================
-- 0001_init · schema รวมของทั้งระบบ
-- สร้างจาก reference/feat-NN/db/migration.sql ทั้ง 14 ไฟล์
-- ลำดับรวม: feat-01 -> feat-03 -> feat-02 -> feat-04 -> feat-05 -> feat-06 -> feat-07 -> feat-11 -> feat-08 -> feat-09 -> feat-10 -> feat-12 -> feat-13 -> feat-14
--
-- ค่าเริ่มต้นของข้อที่ยังไม่ตัดสิน (BE-0002 · C1 C2 C3)
--   C1  coupon_type.expires_at = nullable (ของ feat-11)
--   C2  coupon_type.total_supply = CHECK (total_supply > 0) (ของ feat-08/09/10)
--   C3  wallet.address = คอลัมน์ธรรมดา + unique index (ของ feat-01/04/05/11)
-- =====================================================================

BEGIN;
-- function บางตัวถูกเรียกใน CHECK ของตาราง และบางตัวอ้างถึงตาราง
-- จึงต้องประกาศ function ก่อนตาราง แล้วปิดการตรวจ body ชั่วคราว
SET LOCAL check_function_bodies = off;

-- ---------------------------------------------------------- 1 · enum
DO $do$ BEGIN
CREATE TYPE actor_type AS ENUM ('SYSTEM', 'MERCHANT_ADMIN', 'CUSTOMER', 'PLATFORM_ADMIN');
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;

DO $do$ BEGIN
CREATE TYPE attempt_result AS ENUM ('OK','WRONG_CODE','EXPIRED','LOCKED','ALREADY_USED');
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;

DO $do$ BEGIN
CREATE TYPE chain_event_name AS ENUM (
  'CouponTransfer',   -- คูปองย้ายมือ
  'PointTransfer',    -- point ย้ายมือ
  'CouponRedeemed',   -- คูปองถูกใช้
  'UNKNOWN'           -- topic0 ที่ยังไม่รู้จัก
);
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;

DO $do$ BEGIN
CREATE TYPE challenge_status AS ENUM (
  'PENDING',   -- ส่ง OTP ไปแล้ว รอยืนยัน
  'VERIFIED',  -- ยืนยันถูกแล้ว · ใช้ได้ครั้งเดียว
  'CONSUMED',  -- ถูกใช้สมัครเรียบร้อย
  'EXPIRED',
  'LOCKED'     -- ใส่ผิดครบจำนวนครั้ง
);
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;

DO $do$ BEGIN
CREATE TYPE coupon_code_state AS ENUM (
  'ISSUED',      -- ออกแล้ว ยังไม่มีผู้ถือ
  'HELD',        -- มีผู้ถือ
  'REDEEMED',    -- ใช้ไปแล้ว
  'EXPIRED',     -- หมดอายุ
  'VOIDED'       -- ร้านยกเลิกก่อนแจก
);
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;

DO $do$ BEGIN
CREATE TYPE coupon_type_status AS ENUM (
  'REQUESTED',   -- ประกาศแล้ว ยังไม่มี contract
  'LIVE',        -- deploy เสร็จ ออกรหัสได้
  'FAILED',      -- deploy ล้ม · ชื่อถูกปล่อยคืน
  'RETIRED'      -- เลิกออกเพิ่ม · ใบที่ออกไปแล้วยังใช้ได้
);
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;

DO $do$ BEGIN
CREATE TYPE customer_status AS ENUM (
  'PENDING_WALLET',  -- ยืนยันเบอร์แล้ว รอ wallet ขึ้น chain
  'ACTIVE',
  'SUSPENDED',
  'CLOSED'
);
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;

DO $do$ BEGIN
CREATE TYPE delivery_status AS ENUM (
  'PENDING',    -- รอยิง
  'IN_FLIGHT',  -- กำลังยิง · มี worker ถือไว้
  'SENT',       -- ร้านตอบ 2xx
  'DEAD'        -- ลองครบจำนวนแล้วยังไม่ผ่าน
);
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;

DO $do$ BEGIN
CREATE TYPE deposit_status AS ENUM (
  'PENDING_PAYMENT',  -- สร้างรายการแล้ว รอเงินเข้า
  'CONFIRMED',        -- ผู้ให้บริการยืนยันว่าเงินเข้าแล้ว · mint ได้
  'MINTED',           -- THB ถูกสร้างบน chain แล้ว
  'REVERSED',         -- ถูกตีกลับ (chargeback) หลัง mint ไปแล้ว
  'FAILED',           -- จ่ายไม่สำเร็จ · ไม่เคยมีเงินเข้า
  'EXPIRED'           -- หมดเวลารอจ่าย
);
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;

DO $do$ BEGIN
CREATE TYPE endpoint_status AS ENUM ('ACTIVE', 'PAUSED', 'DISABLED');
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;

DO $do$ BEGIN
CREATE TYPE holder_kind AS ENUM ('MERCHANT', 'CUSTOMER');
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;

DO $do$ BEGIN
CREATE TYPE ledger_account AS ENUM (
  'CUSTOMER_THB', 'MERCHANT_THB', 'PLATFORM_THB', 'ISSUANCE'
);
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;

DO $do$ BEGIN
CREATE TYPE ledger_direction AS ENUM ('DEBIT', 'CREDIT');
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;

DO $do$ BEGIN
CREATE TYPE listing_close_reason AS ENUM ('CANCELLED', 'SOLD');
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;

DO $do$ BEGIN
CREATE TYPE listing_status AS ENUM (
  'PENDING',    -- ส่งคำขอแล้ว ยังไม่อยู่บน chain
  'ACTIVE',     -- ขายอยู่
  'SOLD',       -- ขายหมดแล้ว
  'DELISTED',   -- ถอดออกเอง
  'FAILED'      -- สร้างบน chain ไม่สำเร็จ
);
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;

DO $do$ BEGIN
CREATE TYPE merchant_status AS ENUM ('PENDING', 'ACTIVE', 'SUSPENDED', 'CLOSED');
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;

DO $do$ BEGIN
CREATE TYPE operation_kind AS ENUM (
  'MERCHANT_PROVISION', 'CUSTOMER_PROVISION', 'POINT_TYPE_CREATE',
  'POINT_TRANSFER', 'COUPON_TYPE_CREATE', 'COUPON_ISSUE', 'COUPON_REDEEM',
  'LISTING_CREATE', 'LISTING_DELETE', 'LISTING_BUY', 'THB_MINT'
);
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;

DO $do$ BEGIN
CREATE TYPE operation_status AS ENUM (
  'PENDING', 'SUBMITTED', 'CONFIRMED', 'FINALIZED',
  'CHAIN_REVERTED', 'ORPHANED', 'MANUAL_REVIEW'
);
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;

DO $do$ BEGIN
CREATE TYPE point_type_status AS ENUM (
  'REQUESTED',   -- ร้านประกาศแล้ว · ยังไม่มี contract
  'LIVE',        -- deploy เสร็จ ใช้งานได้
  'FAILED',      -- deploy ล้ม · ชื่อถูกปล่อยคืน
  'RETIRED'      -- เลิกใช้ · ยอดเดิมยังอยู่
);
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;

DO $do$ BEGIN
CREATE TYPE purchase_status AS ENUM (
  'PENDING',   -- รับคำขอแล้ว · เงินถูกกันไว้ ยังไม่ย้าย
  'SETTLED',   -- executePurchase FINALIZED · เงินและของย้ายแล้ว
  'FAILED'     -- tx revert · เงินที่กันไว้ถูกปล่อยคืน
);
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;

DO $do$ BEGIN
CREATE TYPE reorg_outcome AS ENUM (
  'ROLLED_BACK',   -- ลึกน้อยกว่า N · ย้อน projection แล้วอ่านใหม่ได้
  'MANUAL_REVIEW'  -- ลึกกว่า N · มี event ที่ถือว่า final แล้วหลุด
);
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;

DO $do$ BEGIN
CREATE TYPE reservation_subject AS ENUM (
  'POINT_BALANCE', 'COUPON_UNIT', 'LISTING', 'THB_BALANCE', 'ESCROW'
);
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;

DO $do$ BEGIN
CREATE TYPE reversal_outcome AS ENUM (
  'BURNED',         -- ยอดพอ · เผา THB คืนได้ครบ
  'MANUAL_REVIEW'   -- ยอดไม่พอ · ใช้ไปแล้ว ต้องมีคนตัดสิน
);
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;

DO $do$ BEGIN
CREATE TYPE revoke_reason AS ENUM (
  'LOGOUT',            -- ผู้ใช้กดออกจากระบบเอง
  'ROTATED',           -- หมุนแล้ว ใบเก่าหมดหน้าที่
  'REUSE_DETECTED',    -- มีคนเอาใบที่ใช้ไปแล้วมาใช้อีก
  'PASSWORD_CHANGED',
  'ADMIN_REVOKED',
  'EXPIRED'
);
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;

DO $do$ BEGIN
CREATE TYPE step_status AS ENUM (
  'PENDING', 'SUBMITTED', 'CONFIRMED', 'FINALIZED',
  'CHAIN_REVERTED', 'ORPHANED'
);
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;

DO $do$ BEGIN
CREATE TYPE subject_kind AS ENUM ('MERCHANT_ADMIN', 'CUSTOMER', 'PLATFORM_ADMIN');
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;

DO $do$ BEGIN
CREATE TYPE wallet_owner AS ENUM ('MERCHANT', 'CUSTOMER', 'PLATFORM');
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;

DO $do$ BEGIN
CREATE TYPE wallet_status AS ENUM ('REQUESTED','LIVE','FAILED');
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;

-- ------------------------------------------------------ 2 · function
CREATE OR REPLACE FUNCTION f_normalize_phone(p_raw text) RETURNS text
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE d text; has_plus boolean;
BEGIN
  IF p_raw IS NULL THEN RETURN NULL; END IF;
  has_plus := left(btrim(p_raw), 1) = '+';
  d := regexp_replace(p_raw, '[^0-9]', '', 'g');
  IF d = '' THEN RETURN NULL; END IF;

  IF has_plus THEN
    RETURN '+' || d;
  END IF;
  -- เบอร์ไทย · สามรูปที่คนพิมพ์จริง
  IF left(d,2) = '66' AND length(d) = 11 THEN RETURN '+' || d; END IF;
  IF left(d,1) = '0'  AND length(d) = 10 THEN RETURN '+66' || substr(d,2); END IF;
  IF length(d) = 9 THEN RETURN '+66' || d; END IF;
  -- รูปที่ไม่รู้จัก · คืนค่าที่ CHECK จะปฏิเสธ ดีกว่าเดาแล้วผิด
  RETURN d;
END $$;

CREATE OR REPLACE FUNCTION f_no_secret_columns() RETURNS event_trigger
LANGUAGE plpgsql AS $$
DECLARE r record; c record;
BEGIN
  FOR r IN SELECT * FROM pg_event_trigger_ddl_commands()
           WHERE command_tag IN ('ALTER TABLE','CREATE TABLE')
  LOOP
    FOR c IN
      SELECT a.attname, cl.relname
        FROM pg_attribute a
        JOIN pg_class cl ON cl.oid = a.attrelid
       WHERE a.attrelid = r.objid
         AND a.attnum > 0 AND NOT a.attisdropped
         AND (
              a.attname ~* '(private|privkey|priv_key|secret|mnemonic|seed_phrase|seedphrase|keystore|encrypted_key|enc_key)'
           OR a.attname ~* '^(sk|xprv)$'
         )
    LOOP
      RAISE EXCEPTION 'ห้ามมีคอลัมน์ % ในตาราง % (L6)', c.attname, c.relname
        USING ERRCODE = 'insufficient_privilege',
              HINT = 'key อยู่ใน KMS เท่านั้น · เก็บได้แค่ kms_key_ref · F10 และ F13 เกิดจากการเก็บ key ไว้เอง';
    END LOOP;
  END LOOP;
END $$;

CREATE OR REPLACE FUNCTION f_challenge_transition() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  -- ค่าที่ตั้งตอนสร้างห้ามเปลี่ยน · เปลี่ยนเบอร์หรือ hash ได้คือ F7 กลับมา
  IF NEW.phone_e164 <> OLD.phone_e164
     OR NEW.code_hash <> OLD.code_hash
     OR NEW.pepper_ref <> OLD.pepper_ref
     OR NEW.expires_at <> OLD.expires_at THEN
    RAISE EXCEPTION 'challenge % แก้เบอร์ รหัส หรือเวลาหมดอายุไม่ได้', OLD.id
      USING ERRCODE = 'check_violation',
            HINT = 'F7 · ระบบเดิมเขียนทับเบอร์ด้วยค่าจาก body';
  END IF;

  IF OLD.status = NEW.status THEN RETURN NEW; END IF;

  IF NOT (
       (OLD.status = 'PENDING'  AND NEW.status IN ('VERIFIED','EXPIRED','LOCKED'))
    OR (OLD.status = 'VERIFIED' AND NEW.status IN ('CONSUMED','EXPIRED'))
  ) THEN
    RAISE EXCEPTION 'challenge % เปลี่ยนสถานะจาก % ไป % ไม่ได้',
      OLD.id, OLD.status, NEW.status USING ERRCODE = 'check_violation';
  END IF;

  IF NEW.status = 'VERIFIED' AND NEW.verified_at IS NULL THEN
    NEW.verified_at := now();
  END IF;
  IF NEW.status = 'CONSUMED' AND NEW.consumed_at IS NULL THEN
    NEW.consumed_at := now();
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_attempt_count_matches() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_n integer; v_claimed integer;
BEGIN
  SELECT attempts INTO v_claimed FROM otp_challenge WHERE id = NEW.challenge_id;
  SELECT count(*) INTO v_n FROM otp_attempt
   WHERE challenge_id = NEW.challenge_id AND result IN ('OK','WRONG_CODE');
  IF v_claimed <> v_n THEN
    RAISE EXCEPTION 'challenge % นับว่าเดาไป % ครั้ง แต่มีแถวที่เป็นการเดา % แถว',
      NEW.challenge_id, v_claimed, v_n
      USING ERRCODE = 'check_violation',
            HINT = 'attempts นับการเดา ไม่ใช่การเรียก · LOCKED และ EXPIRED ไม่นับ';
  END IF;
  RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION f_throttle_check() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v record; v_cool interval := interval '60 seconds'; v_max integer := 5;
BEGIN
  SELECT * INTO v FROM phone_throttle WHERE phone_e164 = NEW.phone_e164;
  IF NOT FOUND THEN RETURN NEW; END IF;

  IF v.locked_until IS NOT NULL AND v.locked_until > now() THEN
    RAISE EXCEPTION 'เบอร์ % ถูกล็อกถึง %', NEW.phone_e164, v.locked_until
      USING ERRCODE = 'check_violation';
  END IF;
  IF v.last_sent_at IS NOT NULL AND v.last_sent_at + v_cool > now() THEN
    RAISE EXCEPTION 'ขอ OTP ถี่เกินไป · รออีก % วินาที',
      ceil(extract(epoch FROM (v.last_sent_at + v_cool - now())))
      USING ERRCODE = 'check_violation';
  END IF;
  IF v.window_start + interval '1 hour' > now() AND v.sent_count >= v_max THEN
    RAISE EXCEPTION 'เบอร์ % ขอ OTP ครบ % ครั้งในหนึ่งชั่วโมงแล้ว', NEW.phone_e164, v_max
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_customer_phone_from_challenge() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_ch text; v_phone text; v_st challenge_status;
BEGIN
  -- challenge ที่ใช้สมัครถูกส่งมาทาง parameter ของ session
  v_ch := current_setting('app.verified_challenge', true);
  IF v_ch IS NULL OR v_ch = '' THEN
    RAISE EXCEPTION 'สร้างลูกค้าโดยไม่อ้าง challenge ที่ยืนยันแล้วไม่ได้'
      USING ERRCODE = 'check_violation',
            HINT = 'F7 · เบอร์ต้องมาจาก challenge ไม่ใช่จาก request';
  END IF;

  SELECT phone_e164, status INTO v_phone, v_st
    FROM otp_challenge WHERE id = v_ch;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'ไม่พบ challenge %', v_ch USING ERRCODE = 'check_violation';
  END IF;
  IF v_st <> 'VERIFIED' THEN
    RAISE EXCEPTION 'challenge % อยู่สถานะ % · ใช้สมัครไม่ได้', v_ch, v_st
      USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.phone_e164 <> v_phone THEN
    RAISE EXCEPTION 'เบอร์ที่สมัคร % ไม่ตรงกับเบอร์ที่ยืนยันไว้ %',
      NEW.phone_e164, v_phone
      USING ERRCODE = 'check_violation',
            HINT = 'F7 · ระบบเดิมเขียนทับเบอร์ด้วยค่าจาก body';
  END IF;
  IF NEW.phone_verified_at IS DISTINCT FROM NULL
     AND NEW.phone_verified_at < (SELECT verified_at FROM otp_challenge WHERE id = v_ch)
  THEN
    RAISE EXCEPTION 'phone_verified_at เก่ากว่าเวลาที่ยืนยันจริง'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_customer_phone_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.phone_e164 <> OLD.phone_e164 THEN
    RAISE EXCEPTION 'เปลี่ยนเบอร์ของลูกค้า % ตรงๆ ไม่ได้ · ต้องยืนยันเบอร์ใหม่', OLD.id
      USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.phone_verified_at <> OLD.phone_verified_at THEN
    RAISE EXCEPTION 'แก้เวลาที่ยืนยันเบอร์ไม่ได้' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_verify_otp(
  p_challenge_id text, p_code text, p_pepper text, p_ip_hash bytea DEFAULT NULL
) RETURNS attempt_result
LANGUAGE plpgsql AS $$
DECLARE c otp_challenge; v_n integer; v_hash bytea; v_res attempt_result;
BEGIN
  -- ล็อกแถวไว้ · สองคำขอพร้อมกันจึงเข้าคิว ไม่ใช่นับครั้งทับกัน
  SELECT * INTO c FROM otp_challenge WHERE id = p_challenge_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'ไม่พบ challenge %', p_challenge_id USING ERRCODE = 'check_violation';
  END IF;

  IF c.status IN ('CONSUMED','VERIFIED') THEN v_res := 'ALREADY_USED';
  ELSIF c.status = 'LOCKED'              THEN v_res := 'LOCKED';
  ELSIF c.status = 'EXPIRED'
     OR c.expires_at <= now()            THEN v_res := 'EXPIRED';
  ELSE
    v_hash := hmac(p_challenge_id || ':' || p_code, p_pepper, 'sha256');
    --
    -- ข้อจำกัดที่ต้องพูดให้ชัด · การเทียบ bytea ใน Postgres ใช้ memcmp
    -- ซึ่ง **ไม่ใช่ constant time** · เทียบ hash สองค่าที่ต่างกันตั้งแต่ byte แรก
    -- เร็วกว่าเทียบค่าที่ต่างกันที่ byte สุดท้าย
    --
    -- เหตุผลที่ยังยอมรับได้ที่นี่
    --   สิ่งที่เทียบคือ hash ไม่ใช่ OTP · คนที่วัดเวลาได้ก็ต้องเดา hash
    --   ไม่ใช่เดา OTP และ hash มี 2^256 ความเป็นไปได้
    --   และการจำกัดครั้งที่ลอง (max_attempts) ตัดการวัดซ้ำที่จำเป็น
    --   สำหรับการโจมตีแบบนี้ไปแล้ว
    --
    -- ถ้าวันหนึ่งต้องการ constant time จริง ต้องย้ายการเทียบไปที่โค้ด
    -- แล้วใช้ timingSafeEqual ซึ่งแลกมาด้วยการที่ hash ต้องออกจากฐานข้อมูล
    IF v_hash = c.code_hash THEN v_res := 'OK'; ELSE v_res := 'WRONG_CODE'; END IF;
  END IF;

  -- บันทึกทุกการเรียก · attempt_no เดินตามจำนวนแถว ไม่ใช่ตาม attempts
  SELECT count(*) + 1 INTO v_n FROM otp_attempt WHERE challenge_id = p_challenge_id;
  INSERT INTO otp_attempt (id, challenge_id, attempt_no, result, ip_hash)
  VALUES ('oa_' || p_challenge_id || '_' || v_n, p_challenge_id, v_n, v_res, p_ip_hash);

  -- นับเฉพาะที่เอารหัสมาเทียบจริง · ล็อกแล้วหรือหมดอายุไม่ใช่การเดา
  IF v_res IN ('OK','WRONG_CODE') THEN
    UPDATE otp_challenge SET attempts = c.attempts + 1 WHERE id = p_challenge_id;
  END IF;

  IF v_res = 'OK' THEN
    UPDATE otp_challenge SET status = 'VERIFIED' WHERE id = p_challenge_id;
  ELSIF v_res = 'WRONG_CODE' AND c.attempts + 1 >= c.max_attempts THEN
    UPDATE otp_challenge SET status = 'LOCKED' WHERE id = p_challenge_id;
    UPDATE phone_throttle SET locked_until = now() + interval '30 minutes'
     WHERE phone_e164 = c.phone_e164;
  ELSIF v_res = 'EXPIRED' AND c.status = 'PENDING' THEN
    UPDATE otp_challenge SET status = 'EXPIRED' WHERE id = p_challenge_id;
  END IF;

  RETURN v_res;
END $$;

CREATE OR REPLACE FUNCTION f_point_chain_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.contract_address <> OLD.contract_address
     OR NEW.genesis_at    <> OLD.genesis_at
     OR NEW.epoch_seconds <> OLD.epoch_seconds THEN
    RAISE EXCEPTION 'point_type_chain เปลี่ยนค่าที่มาจาก chain ไม่ได้'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_merchant_must_be_active() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE s merchant_status;
BEGIN
  SELECT status INTO s FROM merchant WHERE id = NEW.merchant_id;
  IF s <> 'ACTIVE' THEN
    RAISE EXCEPTION 'ร้าน % ยังไม่ ACTIVE (status = %)', NEW.merchant_id, s
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_available_points(
  p_point_type_id text, p_holder_kind holder_kind, p_holder_id text, p_as_of timestamptz
) RETURNS numeric LANGUAGE sql STABLE AS $$
  SELECT COALESCE((
    SELECT sum(amount) FROM point_balance_bucket
     WHERE point_type_id = p_point_type_id
       AND holder_kind   = p_holder_kind
       AND holder_id     = p_holder_id
       AND expires_at    > p_as_of
  ), 0)
  - COALESCE((
    SELECT sum(amount) FROM reservation
     WHERE subject_type = 'POINT_BALANCE'
       AND released_at IS NULL
       AND subject_id = p_point_type_id || '|' || p_holder_kind::text || '|' || p_holder_id
  ), 0)
$$;

CREATE OR REPLACE FUNCTION f_check_point_reservation() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  v_pt text; v_kind_text text; v_kind holder_kind; v_holder text; v_avail numeric;
BEGIN
  IF NEW.subject_type <> 'POINT_BALANCE' THEN RETURN NEW; END IF;

  v_pt        := split_part(NEW.subject_id, '|', 1);
  v_kind_text := split_part(NEW.subject_id, '|', 2);
  v_holder    := split_part(NEW.subject_id, '|', 3);

  -- ตรวจรูปแบบให้ครบก่อน cast · ไม่งั้นจะได้ error 22P02 ที่อ่านไม่รู้เรื่อง
  IF v_pt = '' OR v_holder = '' OR v_kind_text NOT IN ('MERCHANT','CUSTOMER') THEN
    RAISE EXCEPTION 'subject_id ของ POINT_BALANCE ต้องเป็น <point_type_id>|<holder_kind>|<holder_id>'
      USING ERRCODE = 'check_violation';
  END IF;
  v_kind := v_kind_text::holder_kind;

  -- serialize เฉพาะ subject นี้จนจบ transaction
  PERFORM pg_advisory_xact_lock(hashtextextended(NEW.subject_id, 0));

  v_avail := f_available_points(v_pt, v_kind, v_holder, now());
  IF NEW.amount > v_avail THEN
    RAISE EXCEPTION 'ยอดไม่พอ · ต้องการ % แต่ใช้ได้ %', NEW.amount, v_avail
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_check_release_timing() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE st operation_status;
BEGIN
  IF OLD.released_at IS NOT NULL OR NEW.released_at IS NULL THEN RETURN NEW; END IF;
  SELECT status INTO st FROM operation WHERE id = NEW.operation_id;
  IF st NOT IN ('FINALIZED','CHAIN_REVERTED') THEN
    RAISE EXCEPTION 'ปล่อยจองไม่ได้ · operation % ยังอยู่สถานะ %', NEW.operation_id, st
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_watermark_monotonic() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.last_block < OLD.last_block THEN
    RAISE EXCEPTION 'watermark ถอยหลังไม่ได้ · เดิม % ใหม่ %', OLD.last_block, NEW.last_block
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_notice_not_expired() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_exp timestamptz;
BEGIN
  SELECT expires_at INTO v_exp FROM point_balance_bucket WHERE id = NEW.bucket_id;
  IF v_exp <= NEW.notified_at THEN
    RAISE EXCEPTION 'bucket % หมดอายุไปแล้ว แจ้งเตือนล่วงหน้าไม่ได้', NEW.bucket_id
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_unexpired_total(
  p_point_type_id text, p_holder_kind holder_kind, p_holder_id text, p_as_of timestamptz
) RETURNS numeric LANGUAGE sql STABLE AS $$
  SELECT COALESCE(sum(amount), 0) FROM point_balance_bucket
   WHERE point_type_id = p_point_type_id
     AND holder_kind   = p_holder_kind
     AND holder_id     = p_holder_id
     AND amount > 0
     AND expires_at > p_as_of
$$;

CREATE OR REPLACE FUNCTION f_reserved_total(
  p_point_type_id text, p_holder_kind holder_kind, p_holder_id text
) RETURNS numeric LANGUAGE sql STABLE AS $$
  SELECT COALESCE(sum(amount), 0) FROM reservation
   WHERE subject_type = 'POINT_BALANCE'
     AND released_at IS NULL
     AND subject_id = p_point_type_id || '|' || p_holder_kind::text || '|' || p_holder_id
$$;

CREATE OR REPLACE FUNCTION f_expiry_forecast(
  p_point_type_id text, p_holder_kind holder_kind, p_holder_id text,
  p_as_of timestamptz, p_horizon_days integer
) RETURNS TABLE (expires_on date, amount numeric)
LANGUAGE sql STABLE AS $$
  SELECT (expires_at AT TIME ZONE 'UTC')::date AS expires_on, sum(amount)
    FROM point_balance_bucket
   WHERE point_type_id = p_point_type_id
     AND holder_kind   = p_holder_kind
     AND holder_id     = p_holder_id
     AND amount > 0
     AND expires_at >  p_as_of
     AND expires_at <= p_as_of + make_interval(days => p_horizon_days)
   GROUP BY 1 ORDER BY 1
$$;

CREATE OR REPLACE FUNCTION f_expired_total(
  p_point_type_id text, p_as_of timestamptz
) RETURNS numeric LANGUAGE sql STABLE AS $$
  SELECT COALESCE(sum(amount), 0) FROM point_balance_bucket
   WHERE point_type_id = p_point_type_id
     AND expires_at <= p_as_of
$$;

CREATE OR REPLACE FUNCTION f_coupon_chain_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.contract_address <> OLD.contract_address
     OR NEW.chain_type_id <> OLD.chain_type_id THEN
    RAISE EXCEPTION 'coupon_type_chain เปลี่ยนค่าที่มาจาก chain ไม่ได้'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_code_ref_derived() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.code_ref <> sha256(convert_to(NEW.id, 'UTF8')) THEN
    RAISE EXCEPTION 'code_ref ต้องเท่ากับ sha256(id) เสมอ'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_issue_within_supply() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_supply integer; v_issued integer;
BEGIN
  SELECT total_supply INTO v_supply FROM coupon_type WHERE id = NEW.coupon_type_id;
  -- serialize เฉพาะประเภทนี้จนจบ transaction
  PERFORM pg_advisory_xact_lock(hashtextextended(NEW.coupon_type_id, 1));
  SELECT count(*) INTO v_issued FROM coupon_code
   WHERE coupon_type_id = NEW.coupon_type_id AND state <> 'VOIDED';
  IF v_issued >= v_supply THEN
    RAISE EXCEPTION 'ออกรหัสเกินจำนวนที่ประกาศไว้ · supply % ออกไปแล้ว %', v_supply, v_issued
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_listing_chain_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.contract_address <> OLD.contract_address
     OR NEW.chain_listing_id <> OLD.chain_listing_id THEN
    RAISE EXCEPTION 'listing_chain เปลี่ยนค่าที่มาจาก chain ไม่ได้'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_listing_item_valid() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  v_state coupon_code_state; v_hk holder_kind; v_hid text;
  v_type text; v_exp timestamptz; v_lt text;
  v_sk holder_kind; v_sid text; v_ltype text;
BEGIN
  SELECT c.state, c.holder_kind, c.holder_id, c.coupon_type_id, t.expires_at
    INTO v_state, v_hk, v_hid, v_type, v_exp
    FROM coupon_code c JOIN coupon_type t ON t.id = c.coupon_type_id
   WHERE c.id = NEW.coupon_code_id;

  SELECT seller_kind, seller_id, coupon_type_id INTO v_sk, v_sid, v_ltype
    FROM listing WHERE id = NEW.listing_id;

  IF v_state <> 'HELD' THEN
    RAISE EXCEPTION 'คูปอง % อยู่สถานะ % ลงขายไม่ได้', NEW.coupon_code_id, v_state
      USING ERRCODE = 'check_violation';
  END IF;
  IF v_hk IS DISTINCT FROM v_sk OR v_hid IS DISTINCT FROM v_sid THEN
    RAISE EXCEPTION 'คูปอง % ไม่ได้อยู่ในมือผู้ขาย', NEW.coupon_code_id
      USING ERRCODE = 'check_violation';
  END IF;
  IF v_type IS DISTINCT FROM v_ltype THEN
    RAISE EXCEPTION 'คูปอง % คนละประเภทกับรายการขาย', NEW.coupon_code_id
      USING ERRCODE = 'check_violation';
  END IF;
  IF v_exp <= now() THEN
    RAISE EXCEPTION 'คูปอง % หมดอายุไปแล้ว', NEW.coupon_code_id
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_listing_quantity_match() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_q integer; v_n integer;
BEGIN
  SELECT quantity INTO v_q FROM listing WHERE id = NEW.listing_id;
  SELECT count(*) INTO v_n FROM listing_item WHERE listing_id = NEW.listing_id;
  IF v_n > v_q THEN
    RAISE EXCEPTION 'ใส่คูปอง % ใบ เกิน quantity ที่ประกาศไว้ %', v_n, v_q
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_listing_status_transition() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status = NEW.status THEN RETURN NEW; END IF;

  IF NOT (
       (OLD.status = 'PENDING' AND NEW.status IN ('ACTIVE','FAILED'))
    OR (OLD.status = 'ACTIVE'  AND NEW.status IN ('SOLD','DELISTED'))
  ) THEN
    RAISE EXCEPTION 'รายการขาย % เปลี่ยนสถานะจาก % ไป % ไม่ได้',
      OLD.id, OLD.status, NEW.status
      USING ERRCODE = 'check_violation';
  END IF;

  -- เติมเวลาให้เอง เพื่อให้ ck_listing_active / ck_listing_closed
  -- เป็นไปได้เสมอโดยที่ caller ไม่ต้องจำ
  IF NEW.status = 'ACTIVE' AND NEW.active_at IS NULL THEN
    NEW.active_at := now();
  END IF;
  IF NEW.status IN ('SOLD','DELISTED') AND NEW.closed_at IS NULL THEN
    NEW.closed_at := now();
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_delist_admissible() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  v_sk holder_kind; v_sid text; v_st listing_status;
  v_actor_kind text; v_actor_id text; v_expect holder_kind;
BEGIN
  IF NEW.kind <> 'LISTING_DELETE' THEN RETURN NEW; END IF;

  IF NEW.subject_id IS NULL THEN
    RAISE EXCEPTION 'operation LISTING_DELETE ต้องระบุ subject_id'
      USING ERRCODE = 'check_violation';
  END IF;

  SELECT seller_kind, seller_id, status INTO v_sk, v_sid, v_st
    FROM listing WHERE id = NEW.subject_id;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'ไม่พบรายการขาย %', NEW.subject_id
      USING ERRCODE = 'check_violation';
  END IF;

  v_actor_kind := split_part(NEW.actor_scope, ':', 1);
  v_actor_id   := split_part(NEW.actor_scope, ':', 2);

  IF v_actor_kind = 'admin' THEN
    -- platform admin ถอดได้ทุกใบ · บันทึกไว้ใน operation_event ว่าใครสั่ง
    NULL;
  ELSE
    v_expect := CASE v_actor_kind
                  WHEN 'merchant' THEN 'MERCHANT'::holder_kind
                  WHEN 'customer' THEN 'CUSTOMER'::holder_kind
                  ELSE NULL
                END;
    IF v_expect IS NULL OR v_expect <> v_sk OR v_actor_id <> v_sid THEN
      RAISE EXCEPTION 'รายการขาย % ไม่ใช่ของ %', NEW.subject_id, NEW.actor_scope
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;

  IF v_st <> 'ACTIVE' THEN
    RAISE EXCEPTION 'รายการขาย % อยู่สถานะ % ถอดไม่ได้', NEW.subject_id, v_st
      USING ERRCODE = 'check_violation';
  END IF;

  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_step_order_gate() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_prev step_status;
BEGIN
  IF NEW.status = 'PENDING' OR NEW.step_index = 1 THEN RETURN NEW; END IF;
  IF OLD.status <> 'PENDING' THEN RETURN NEW; END IF;

  SELECT status INTO v_prev FROM operation_step
   WHERE operation_id = NEW.operation_id AND step_index = NEW.step_index - 1;

  IF v_prev IS DISTINCT FROM 'FINALIZED' THEN
    RAISE EXCEPTION 'ขั้นที่ % ยิงไม่ได้ · ขั้นที่ % อยู่สถานะ %',
      NEW.step_index, NEW.step_index - 1, coalesce(v_prev::text, 'ไม่มีแถว')
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_coupon_hold_until_closed() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_lst text; v_st listing_status;
BEGIN
  IF OLD.released_at IS NOT NULL OR NEW.released_at IS NULL THEN RETURN NEW; END IF;
  IF NEW.subject_type <> 'COUPON_UNIT' THEN RETURN NEW; END IF;

  SELECT l.id, l.status INTO v_lst, v_st
    FROM listing_item li JOIN listing l ON l.id = li.listing_id
   WHERE li.coupon_code_id = NEW.subject_id
     AND l.status IN ('PENDING','ACTIVE')
   LIMIT 1;

  IF FOUND THEN
    RAISE EXCEPTION 'ปล่อยคูปอง % ไม่ได้ · รายการขาย % ยังอยู่สถานะ %',
      NEW.subject_id, v_lst, v_st
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_listing_price_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status <> 'PENDING'
     AND (NEW.unit_price <> OLD.unit_price OR NEW.quantity <> OLD.quantity) THEN
    RAISE EXCEPTION 'รายการ % ขึ้นขายแล้ว แก้ราคาหรือจำนวนไม่ได้', OLD.id
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_fee_bps_at(p_at timestamptz) RETURNS integer
LANGUAGE sql STABLE AS $$
  SELECT bps FROM fee_schedule
   WHERE effective_from <= p_at
   ORDER BY effective_from DESC LIMIT 1
$$;

CREATE OR REPLACE FUNCTION f_purchase_math() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_expect_fee numeric(78,0);
BEGIN
  IF NEW.total_price <> NEW.unit_price * NEW.quantity THEN
    RAISE EXCEPTION 'total_price % ไม่เท่ากับ unit_price % คูณ %',
      NEW.total_price, NEW.unit_price, NEW.quantity
      USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.fee_amount + NEW.net_to_seller <> NEW.total_price THEN
    RAISE EXCEPTION 'fee % บวก net % ไม่เท่ากับ total %',
      NEW.fee_amount, NEW.net_to_seller, NEW.total_price
      USING ERRCODE = 'check_violation';
  END IF;
  -- ปัดลงเสมอ · ส่วนที่ปัดหายไปตกเป็นของผู้ขาย ไม่ใช่ของ platform
  v_expect_fee := div(NEW.total_price * NEW.fee_bps, 10000);
  IF NEW.fee_amount <> v_expect_fee THEN
    RAISE EXCEPTION 'fee % ไม่ตรงกับที่คำนวณจาก bps % (ควรเป็น %)',
      NEW.fee_amount, NEW.fee_bps, v_expect_fee
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_buy_admissible() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  v_sk holder_kind; v_sid text; v_st listing_status;
  v_actor_kind text; v_actor_id text; v_buyer holder_kind;
BEGIN
  IF NEW.kind <> 'LISTING_BUY' THEN RETURN NEW; END IF;

  IF NEW.subject_id IS NULL THEN
    RAISE EXCEPTION 'operation LISTING_BUY ต้องระบุ subject_id'
      USING ERRCODE = 'check_violation';
  END IF;

  SELECT seller_kind, seller_id, status INTO v_sk, v_sid, v_st
    FROM listing WHERE id = NEW.subject_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'ไม่พบรายการขาย %', NEW.subject_id
      USING ERRCODE = 'check_violation';
  END IF;

  IF v_st <> 'ACTIVE' THEN
    RAISE EXCEPTION 'รายการขาย % อยู่สถานะ % ซื้อไม่ได้', NEW.subject_id, v_st
      USING ERRCODE = 'check_violation';
  END IF;

  v_actor_kind := split_part(NEW.actor_scope, ':', 1);
  v_actor_id   := split_part(NEW.actor_scope, ':', 2);
  v_buyer := CASE v_actor_kind
               WHEN 'merchant' THEN 'MERCHANT'::holder_kind
               WHEN 'customer' THEN 'CUSTOMER'::holder_kind
               ELSE NULL END;
  IF v_buyer IS NULL THEN
    RAISE EXCEPTION 'scope % ซื้อไม่ได้ · ต้องเป็น merchant หรือ customer',
      NEW.actor_scope USING ERRCODE = 'check_violation';
  END IF;
  IF v_buyer = v_sk AND v_actor_id = v_sid THEN
    RAISE EXCEPTION 'ซื้อรายการขายของตัวเองไม่ได้'
      USING ERRCODE = 'check_violation';
  END IF;

  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_reserved_thb(p_account_id text) RETURNS numeric
LANGUAGE sql STABLE AS $$
  SELECT coalesce(sum(amount), 0) FROM reservation
   WHERE subject_type = 'THB_BALANCE' AND subject_id = p_account_id
     AND released_at IS NULL
$$;

CREATE OR REPLACE FUNCTION f_available_thb(
  p_account ledger_account, p_account_id text
) RETURNS numeric
LANGUAGE sql STABLE AS $$
  SELECT greatest(
    coalesce((SELECT amount FROM thb_balance
               WHERE account = p_account AND account_id = p_account_id), 0)
    - f_reserved_thb(p_account_id), 0)
$$;

CREATE OR REPLACE FUNCTION f_thb_reservation_balance() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_acct ledger_account; v_avail numeric;
BEGIN
  IF NEW.subject_type <> 'THB_BALANCE' THEN RETURN NEW; END IF;

  -- subject_id เขียนเป็น '<ACCOUNT>:<id>' · ตรวจส่วนประกอบก่อน cast
  -- cast ก่อนตรวจจะได้ 22P02 ซึ่งบอกอะไรไม่ได้
  IF position(':' in NEW.subject_id) = 0 THEN
    RAISE EXCEPTION 'subject_id ของ THB_BALANCE ต้องเป็น <ACCOUNT>:<id> · ได้ %',
      NEW.subject_id USING ERRCODE = 'check_violation';
  END IF;
  IF split_part(NEW.subject_id, ':', 1)
       NOT IN ('CUSTOMER_THB','MERCHANT_THB','PLATFORM_THB') THEN
    RAISE EXCEPTION 'บัญชี % ใช้จอง THB ไม่ได้', split_part(NEW.subject_id, ':', 1)
      USING ERRCODE = 'check_violation';
  END IF;
  v_acct := split_part(NEW.subject_id, ':', 1)::ledger_account;

  PERFORM pg_advisory_xact_lock(hashtextextended(NEW.subject_id, 0));

  v_avail := f_available_thb(v_acct, NEW.subject_id);
  IF NEW.amount > v_avail THEN
    RAISE EXCEPTION 'ยอด THB ไม่พอ · ต้องการ % มีใช้ได้ %', NEW.amount, v_avail
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_ledger_balanced() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_sum numeric;
BEGIN
  SELECT coalesce(sum(amount), 0) INTO v_sum
    FROM ledger_entry WHERE operation_id = NEW.operation_id;
  IF v_sum <> 0 THEN
    RAISE EXCEPTION 'ledger ของ operation % รวมได้ % ไม่ใช่ศูนย์',
      NEW.operation_id, v_sum
      USING ERRCODE = 'check_violation',
            HINT = 'เงินออกจากที่หนึ่งต้องเข้าอีกที่หนึ่งเสมอ';
  END IF;
  RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION f_balance_matches_ledger() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_ledger numeric; v_current numeric;
BEGIN
  SELECT amount INTO v_current FROM thb_balance
   WHERE account = NEW.account AND account_id = NEW.account_id;
  IF NOT FOUND THEN RETURN NULL; END IF;   -- แถวถูกลบไปภายหลัง

  SELECT coalesce(sum(amount), 0) INTO v_ledger
    FROM ledger_entry
   WHERE account = NEW.account AND account_id = NEW.account_id;

  IF v_current <> v_ledger THEN
    RAISE EXCEPTION 'ยอดของบัญชี % เป็น % แต่ ledger รวมได้ %',
      NEW.account_id, v_current, v_ledger
      USING ERRCODE = 'check_violation',
            HINT = 'ยอดคงเหลือเป็น projection ของ ledger ไม่ใช่ค่าที่ตั้งเองได้';
  END IF;
  RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION f_psp_webhook_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.raw_body <> OLD.raw_body
     OR NEW.signature <> OLD.signature
     OR NEW.provider_event_id <> OLD.provider_event_id
     OR NEW.received_at <> OLD.received_at THEN
    RAISE EXCEPTION 'psp_webhook แก้เนื้อหาที่รับมาไม่ได้ · แก้ได้แค่ processed_at'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_satang_to_base(p_satang bigint) RETURNS numeric
LANGUAGE sql IMMUTABLE AS $$ SELECT p_satang::numeric * 10000000000000000 $$;

CREATE OR REPLACE FUNCTION f_deposit_amount_exact() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.amount_base <> f_satang_to_base(NEW.amount_satang) THEN
    RAISE EXCEPTION 'ยอดไม่ตรงกัน · % สตางค์ ต้องเป็น % base units ไม่ใช่ %',
      NEW.amount_satang, f_satang_to_base(NEW.amount_satang), NEW.amount_base
      USING ERRCODE = 'check_violation',
            HINT = 'ตัวคูณคือ 10^16 · 1 บาท = 100 สตางค์ = 10^18 base units';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_deposit_status_transition() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  -- ยอดเงินแก้ไม่ได้หลังยืนยัน · ยืนยัน 100 แล้ว mint 1000 ไม่ได้
  --
  -- ข้อนี้ต้องอยู่ "ก่อน" การคืนค่าเมื่อสถานะไม่เปลี่ยน
  -- เพราะ UPDATE ที่แก้แต่ยอดโดยไม่แตะสถานะ จะข้ามทุกอย่างใต้บรรทัดนั้นไป
  -- ครั้งแรกเขียนไว้ท้ายฟังก์ชัน และ T15 จับได้ว่าแก้ยอดผ่านฉลุย
  IF OLD.status <> 'PENDING_PAYMENT'
     AND (NEW.amount_satang <> OLD.amount_satang
          OR NEW.amount_base <> OLD.amount_base) THEN
    RAISE EXCEPTION 'การฝาก % ยืนยันแล้ว แก้ยอดไม่ได้', OLD.id
      USING ERRCODE = 'check_violation';
  END IF;

  IF OLD.status = NEW.status THEN RETURN NEW; END IF;

  IF NOT (
       (OLD.status = 'PENDING_PAYMENT' AND NEW.status IN ('CONFIRMED','FAILED','EXPIRED'))
    OR (OLD.status = 'CONFIRMED'       AND NEW.status = 'MINTED')
    OR (OLD.status = 'MINTED'          AND NEW.status = 'REVERSED')
  ) THEN
    RAISE EXCEPTION 'การฝาก % เปลี่ยนสถานะจาก % ไป % ไม่ได้',
      OLD.id, OLD.status, NEW.status
      USING ERRCODE = 'check_violation';
  END IF;

  IF NEW.status = 'CONFIRMED' AND NEW.confirmed_at IS NULL THEN
    NEW.confirmed_at := now();
  END IF;
  IF NEW.status = 'MINTED' AND NEW.minted_at IS NULL THEN
    NEW.minted_at := now();
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_mint_requires_confirmed_deposit() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_st deposit_status; v_amt numeric; v_hk holder_kind; v_hid text; v_expect ledger_account;
BEGIN
  SELECT status, amount_base, holder_kind, holder_id
    INTO v_st, v_amt, v_hk, v_hid
    FROM thb_deposit WHERE id = NEW.deposit_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'ไม่พบการฝาก %', NEW.deposit_id USING ERRCODE = 'check_violation';
  END IF;

  IF v_st <> 'CONFIRMED' THEN
    RAISE EXCEPTION 'การฝาก % อยู่สถานะ % · mint ไม่ได้', NEW.deposit_id, v_st
      USING ERRCODE = 'check_violation',
            HINT = 'mint ได้เฉพาะจากการฝากที่ผู้ให้บริการยืนยันแล้ว';
  END IF;

  IF NEW.amount_base <> v_amt THEN
    RAISE EXCEPTION 'ยอด mint % ไม่เท่ากับยอดที่ฝาก %', NEW.amount_base, v_amt
      USING ERRCODE = 'check_violation';
  END IF;

  -- ปลายทางต้องเป็นเจ้าของการฝาก ไม่ใช่ใครก็ได้
  v_expect := CASE v_hk WHEN 'MERCHANT' THEN 'MERCHANT_THB'::ledger_account
                        ELSE 'CUSTOMER_THB'::ledger_account END;
  IF NEW.to_account <> v_expect
     OR NEW.to_account_id <> v_expect::text || ':' || v_hid THEN
    RAISE EXCEPTION 'ปลายทาง %:% ไม่ใช่เจ้าของการฝาก (ควรเป็น %:%)',
      NEW.to_account, NEW.to_account_id, v_expect, v_expect::text || ':' || v_hid
      USING ERRCODE = 'check_violation';
  END IF;

  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_issuance_only_from_mint() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_kind operation_kind;
BEGIN
  IF NEW.account <> 'ISSUANCE' THEN RETURN NEW; END IF;

  SELECT kind INTO v_kind FROM operation WHERE id = NEW.operation_id;
  IF v_kind <> 'THB_MINT' THEN
    RAISE EXCEPTION 'operation kind % แตะบัญชี ISSUANCE ไม่ได้', v_kind
      USING ERRCODE = 'check_violation',
            HINT = 'THB เข้าและออกจากระบบได้ที่ FEAT-12 เท่านั้น · ที่อื่นเคลื่อนย้ายได้แต่สร้างไม่ได้';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_reversal_outcome_matches_balance() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_st deposit_status; v_amt numeric;
BEGIN
  SELECT status, amount_base INTO v_st, v_amt
    FROM thb_deposit WHERE id = NEW.deposit_id;

  -- ตีกลับได้เฉพาะการฝากที่ mint ไปแล้ว
  IF v_st <> 'MINTED' THEN
    RAISE EXCEPTION 'การฝาก % อยู่สถานะ % · ตีกลับไม่ได้', NEW.deposit_id, v_st
      USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.amount_base <> v_amt THEN
    RAISE EXCEPTION 'ยอดตีกลับ % ไม่เท่ากับยอดที่ฝาก %', NEW.amount_base, v_amt
      USING ERRCODE = 'check_violation';
  END IF;

  -- ผลลัพธ์ต้องตรงกับยอดที่มีจริงตอนนั้น · เลือกเองไม่ได้
  IF NEW.outcome = 'BURNED' AND NEW.available_at_reversal < NEW.amount_base THEN
    RAISE EXCEPTION 'ยอดที่ใช้ได้ % ไม่พอเผา % · ต้องเป็น MANUAL_REVIEW',
      NEW.available_at_reversal, NEW.amount_base
      USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.outcome = 'MANUAL_REVIEW' AND NEW.available_at_reversal >= NEW.amount_base THEN
    RAISE EXCEPTION 'ยอดที่ใช้ได้ % พอเผา % · ต้องเป็น BURNED ไม่ใช่ MANUAL_REVIEW',
      NEW.available_at_reversal, NEW.amount_base
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_operation_status_transition() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status = NEW.status THEN RETURN NEW; END IF;

  IF NOT (
       (OLD.status = 'PENDING'        AND NEW.status IN ('SUBMITTED','MANUAL_REVIEW'))
    OR (OLD.status = 'SUBMITTED'      AND NEW.status IN ('CONFIRMED','CHAIN_REVERTED',
                                                         'ORPHANED','MANUAL_REVIEW'))
    OR (OLD.status = 'CONFIRMED'      AND NEW.status IN ('FINALIZED','ORPHANED',
                                                         'MANUAL_REVIEW'))
    -- reorg · tx หลุดจาก chain แล้วถูกยิงใหม่
    OR (OLD.status = 'ORPHANED'       AND NEW.status IN ('SUBMITTED','MANUAL_REVIEW'))
    OR (OLD.status = 'MANUAL_REVIEW'  AND NEW.status IN ('SUBMITTED','CHAIN_REVERTED',
                                                         'FINALIZED'))
  ) THEN
    RAISE EXCEPTION 'operation % เปลี่ยนสถานะจาก % ไป % ไม่ได้',
      OLD.id, OLD.status, NEW.status
      USING ERRCODE = 'check_violation';
  END IF;

  IF NEW.status = 'FINALIZED' AND NEW.finalized_at IS NULL THEN
    NEW.finalized_at := now();
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_opevent_chain_intact() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_prev_to operation_status; v_prev_seq integer;
BEGIN
  SELECT to_status, seq INTO v_prev_to, v_prev_seq
    FROM operation_event
   WHERE operation_id = NEW.operation_id AND seq < NEW.seq
   ORDER BY seq DESC LIMIT 1;

  IF NEW.seq = 1 THEN
    IF v_prev_seq IS NOT NULL THEN
      RAISE EXCEPTION 'operation % มี event seq 1 อยู่แล้ว', NEW.operation_id
        USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
  END IF;

  IF v_prev_seq IS NULL THEN
    RAISE EXCEPTION 'event seq % ของ operation % ไม่มีแถวก่อนหน้า · ประวัติขาดช่วง',
      NEW.seq, NEW.operation_id USING ERRCODE = 'check_violation';
  END IF;
  IF v_prev_seq <> NEW.seq - 1 THEN
    RAISE EXCEPTION 'event ของ operation % กระโดดจาก seq % ไป % · ต้องเรียงติดกัน',
      NEW.operation_id, v_prev_seq, NEW.seq USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.from_status <> v_prev_to THEN
    RAISE EXCEPTION 'event seq % บอกว่ามาจาก % แต่แถวก่อนหน้าจบที่ %',
      NEW.seq, NEW.from_status, v_prev_to USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_status_has_event() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_status operation_status; v_last operation_status;
BEGIN
  SELECT status INTO v_status FROM operation WHERE id = NEW.id;
  IF NOT FOUND THEN RETURN NULL; END IF;

  SELECT to_status INTO v_last FROM operation_event
   WHERE operation_id = NEW.id ORDER BY seq DESC LIMIT 1;

  IF v_last IS DISTINCT FROM v_status THEN
    RAISE EXCEPTION 'operation % อยู่สถานะ % แต่ event แถวสุดท้ายจบที่ %',
      NEW.id, v_status, coalesce(v_last::text, 'ไม่มี event เลย')
      USING ERRCODE = 'check_violation',
            HINT = 'ทุกการเปลี่ยนสถานะต้องมีแถว operation_event รองรับ';
  END IF;
  RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION f_endpoint_no_plain_secret() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  -- reference ของ KMS ไม่มีเหตุผลที่จะยาวหรือดูเหมือน base64 ของ random
  IF length(NEW.secret_ref) > 200 THEN
    RAISE EXCEPTION 'secret_ref ยาวเกินไป · น่าจะเป็นค่าความลับเองไม่ใช่ reference'
      USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.secret_ref ~ '^kms://[A-Za-z0-9/_.:+=-]+$' THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'secret_ref ต้องเป็น kms:// ตามด้วย path ที่อ่านได้ · ได้ %',
    left(NEW.secret_ref, 40) USING ERRCODE = 'check_violation';
END $$;

CREATE OR REPLACE FUNCTION f_backoff(p_attempt integer) RETURNS interval
LANGUAGE sql IMMUTABLE AS $$
  SELECT least(power(2, greatest(p_attempt - 1, 0))::int, 60) * interval '1 minute'
$$;

CREATE OR REPLACE FUNCTION f_delivery_terminal_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status IN ('SENT','DEAD') THEN
    RAISE EXCEPTION 'delivery % จบที่ % ไปแล้ว แก้ไม่ได้ · ส่งซ้ำคือสร้างแถวใหม่',
      OLD.id, OLD.status USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.payload::text <> OLD.payload::text
     OR NEW.signature <> OLD.signature
     OR NEW.signed_at <> OLD.signed_at
     OR NEW.event_seq <> OLD.event_seq THEN
    RAISE EXCEPTION 'delivery % แก้เนื้อหาที่เซ็นไว้แล้วไม่ได้', OLD.id
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_delivery_order_gate() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_prev delivery_status;
BEGIN
  IF NEW.status <> 'IN_FLIGHT' OR OLD.status = 'IN_FLIGHT' THEN RETURN NEW; END IF;
  IF NEW.delivery_seq = 1 THEN RETURN NEW; END IF;

  SELECT status INTO v_prev FROM webhook_delivery
   WHERE endpoint_id = NEW.endpoint_id
     AND operation_id = NEW.operation_id
     AND delivery_seq = NEW.delivery_seq - 1;

  IF v_prev IS NULL THEN RETURN NEW; END IF;
  IF v_prev NOT IN ('SENT','DEAD') THEN
    RAISE EXCEPTION 'delivery ลำดับ % ยิงไม่ได้ · ลำดับ % อยู่สถานะ %',
      NEW.delivery_seq, NEW.delivery_seq - 1, v_prev
      USING ERRCODE = 'check_violation',
            HINT = 'ส่ง event ของงานเดียวกันต้องเรียงตามลำดับ';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_delivery_event_exists() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM operation_event
                  WHERE operation_id = NEW.operation_id AND seq = NEW.event_seq) THEN
    RAISE EXCEPTION 'ไม่มี operation_event ของ % ที่ seq %',
      NEW.operation_id, NEW.event_seq
      USING ERRCODE = 'check_violation',
            HINT = 'ส่ง webhook เรื่องที่ไม่ได้เกิดขึ้นไม่ได้';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_replay_valid() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_st delivery_status; v_ep text; v_op text; v_seq integer;
BEGIN
  IF NEW.replay_of IS NULL THEN RETURN NEW; END IF;

  SELECT status, endpoint_id, operation_id, event_seq
    INTO v_st, v_ep, v_op, v_seq
    FROM webhook_delivery WHERE id = NEW.replay_of;

  IF v_st <> 'DEAD' THEN
    RAISE EXCEPTION 'ส่งซ้ำ % ไม่ได้ · แถวเดิมอยู่สถานะ % ไม่ใช่ DEAD',
      NEW.replay_of, v_st USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.endpoint_id <> v_ep OR NEW.operation_id <> v_op
     OR NEW.event_seq <> v_seq THEN
    RAISE EXCEPTION 'การส่งซ้ำต้องเป็น endpoint · operation · event เดียวกับของเดิม'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_finality_depth(p_at timestamptz) RETURNS integer
LANGUAGE sql STABLE AS $$
  SELECT depth_blocks FROM finality_config
   WHERE effective_from <= p_at
   ORDER BY effective_from DESC LIMIT 1
$$;

CREATE OR REPLACE FUNCTION f_cursor_needs_lease() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  -- การขอหรือปล่อย lease ไม่นับเป็นการขยับ cursor
  IF NEW.last_block = OLD.last_block THEN RETURN NEW; END IF;

  IF NEW.lease_owner IS NULL THEN
    RAISE EXCEPTION 'ขยับ cursor ไม่ได้ · ไม่มีใครถือ lease'
      USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.lease_until <= now() THEN
    RAISE EXCEPTION 'ขยับ cursor ไม่ได้ · lease ของ % หมดอายุไปแล้ว', NEW.lease_owner
      USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.lease_owner IS DISTINCT FROM OLD.lease_owner THEN
    RAISE EXCEPTION 'เปลี่ยนเจ้าของ lease พร้อมกับขยับ cursor ไม่ได้'
      USING ERRCODE = 'check_violation',
            HINT = 'ขอ lease ก่อน แล้วค่อยขยับในสเตตเมนต์ถัดไป';
  END IF;
  -- cursor ถอยหลังได้เฉพาะตอนจัดการ reorg · ดู f_rewind_for_reorg
  IF NEW.last_block < OLD.last_block
     AND NOT coalesce(current_setting('app.reorg_in_progress', true)::boolean, false) THEN
    RAISE EXCEPTION 'cursor ถอยจาก % ไป % ไม่ได้ · ถอยได้เฉพาะตอนจัดการ reorg',
      OLD.last_block, NEW.last_block
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_block_links_to_parent() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_prev_hash bytea;
BEGIN
  IF NEW.orphaned_at IS NOT NULL THEN RETURN NEW; END IF;

  SELECT block_hash INTO v_prev_hash FROM raw_block
   WHERE block_number = NEW.block_number - 1 AND orphaned_at IS NULL;

  -- ยังไม่มีบล็อกก่อนหน้า · เริ่มอ่านจากจุดนี้ ไม่มีอะไรให้เทียบ
  IF NOT FOUND THEN RETURN NEW; END IF;

  IF NEW.parent_hash <> v_prev_hash THEN
    RAISE EXCEPTION 'บล็อก % ชี้ไป parent % แต่บล็อก % ที่มีอยู่คือ %',
      NEW.block_number, encode(NEW.parent_hash,'hex'),
      NEW.block_number - 1, encode(v_prev_hash,'hex')
      USING ERRCODE = 'check_violation',
            HINT = 'นี่คือ reorg · ต้อง orphan บล็อกเก่าก่อน แล้วค่อยเขียนบล็อกใหม่';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_event_within_cursor() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_last bigint; v_max bigint;
BEGIN
  SELECT last_block INTO v_last FROM indexer_cursor WHERE id = 'main';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'ไม่มีแถว indexer_cursor · ต้องสร้างก่อนเริ่มอ่าน'
      USING ERRCODE = 'check_violation';
  END IF;

  SELECT max(block_number) INTO v_max FROM chain_event WHERE orphaned_at IS NULL;
  IF v_max IS NOT NULL AND v_max > v_last THEN
    RAISE EXCEPTION 'มี chain_event ที่บล็อก % แต่ cursor อยู่ที่ %', v_max, v_last
      USING ERRCODE = 'check_violation',
            HINT = 'L8 · cursor ต้องขยับใน transaction เดียวกับการ insert event';
  END IF;
  RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION f_final_not_orphanable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.orphaned_at IS NOT NULL AND OLD.finalized_at IS NOT NULL THEN
    RAISE EXCEPTION 'event % ถือว่า final ไปแล้วตั้งแต่ % · orphan ไม่ได้',
      NEW.id, OLD.finalized_at
      USING ERRCODE = 'check_violation',
            HINT = 'reorg ที่ลึกกว่า N ต้องไป MANUAL_REVIEW ไม่ใช่ย้อนเงียบๆ';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_reorg_outcome_matches_depth() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.depth > NEW.finality_depth AND NEW.outcome <> 'MANUAL_REVIEW' THEN
    RAISE EXCEPTION 'reorg ลึก % เกิน N = % · ต้องเป็น MANUAL_REVIEW',
      NEW.depth, NEW.finality_depth USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.depth <= NEW.finality_depth AND NEW.outcome <> 'ROLLED_BACK' THEN
    RAISE EXCEPTION 'reorg ลึก % ไม่เกิน N = % · ย้อนเองได้ ต้องเป็น ROLLED_BACK',
      NEW.depth, NEW.finality_depth USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION f_apply_event(p_event_id text) RETURNS boolean
LANGUAGE plpgsql AS $$
DECLARE
  e            chain_event;
  v_code       text;
  v_pt         text;
  v_to_kind    holder_kind;
  v_to_id      text;
  v_from_kind  holder_kind;
  v_from_id    text;
  v_amount     numeric;
BEGIN
  SELECT * INTO e FROM chain_event WHERE id = p_event_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'ไม่พบ chain_event %', p_event_id USING ERRCODE = 'check_violation';
  END IF;
  IF e.orphaned_at IS NOT NULL THEN RETURN false; END IF;
  IF e.applied_at IS NOT NULL THEN RETURN false; END IF;

  CASE e.event_name

    WHEN 'CouponTransfer' THEN
      SELECT id INTO v_code FROM coupon_code
       WHERE code_ref = decode(e.args->>'codeRef', 'hex');
      IF NOT FOUND THEN
        RAISE EXCEPTION 'event % อ้าง code_ref ที่ไม่มีในระบบ', p_event_id
          USING ERRCODE = 'check_violation';
      END IF;
      v_to_kind := (e.args->>'toKind')::holder_kind;
      v_to_id   := e.args->>'toId';

      INSERT INTO coupon_holding (coupon_code_id, holder_kind, holder_id,
                                  as_of_block, as_of_time)
      VALUES (v_code, v_to_kind, v_to_id, e.block_number, e.block_timestamp)
      ON CONFLICT (coupon_code_id) DO UPDATE
        SET holder_kind = EXCLUDED.holder_kind,
            holder_id   = EXCLUDED.holder_id,
            as_of_block = EXCLUDED.as_of_block,
            as_of_time  = EXCLUDED.as_of_time;

    WHEN 'PointTransfer' THEN
      v_pt        := e.args->>'pointTypeId';
      v_amount    := (e.args->>'amount')::numeric;
      v_to_kind   := (e.args->>'toKind')::holder_kind;
      v_to_id     := e.args->>'toId';

      -- ฝั่งรับ
      INSERT INTO point_balance (point_type_id, holder_kind, holder_id, amount, as_of_block)
      VALUES (v_pt, v_to_kind, v_to_id, v_amount, e.block_number)
      ON CONFLICT (point_type_id, holder_kind, holder_id) DO UPDATE
        SET amount = point_balance.amount + v_amount,
            as_of_block = EXCLUDED.as_of_block;

      -- ฝั่งจ่าย · address ศูนย์ = mint ไม่มีคนจ่าย
      IF e.args ? 'fromId' AND e.args->>'fromId' <> '' THEN
        v_from_kind := (e.args->>'fromKind')::holder_kind;
        v_from_id   := e.args->>'fromId';
        UPDATE point_balance
           SET amount = amount - v_amount, as_of_block = e.block_number
         WHERE point_type_id = v_pt AND holder_kind = v_from_kind
           AND holder_id = v_from_id;
        IF NOT FOUND THEN
          RAISE EXCEPTION 'event % จ่าย point จากบัญชีที่ไม่มียอด', p_event_id
            USING ERRCODE = 'check_violation';
        END IF;
      END IF;

    WHEN 'CouponRedeemed' THEN
      SELECT id INTO v_code FROM coupon_code
       WHERE code_ref = decode(e.args->>'codeRef', 'hex');
      IF NOT FOUND THEN
        RAISE EXCEPTION 'event % อ้าง code_ref ที่ไม่มีในระบบ', p_event_id
          USING ERRCODE = 'check_violation';
      END IF;
      UPDATE coupon_code SET state = 'REDEEMED' WHERE id = v_code;
      DELETE FROM coupon_holding WHERE coupon_code_id = v_code;

    ELSE
      -- ชนิดที่ยังไม่รู้จัก · เก็บไว้แต่ไม่แตะ projection
      -- ทำเครื่องหมายว่า apply แล้ว เพื่อไม่ให้ค้างในคิวตลอดไป
      NULL;
  END CASE;

  UPDATE chain_event SET applied_at = now() WHERE id = p_event_id;
  RETURN true;
END $$;

CREATE OR REPLACE FUNCTION f_apply_pending(p_limit integer DEFAULT 1000)
RETURNS integer LANGUAGE plpgsql AS $$
DECLARE r record; n integer := 0;
BEGIN
  FOR r IN
    SELECT id FROM chain_event
     WHERE applied_at IS NULL AND orphaned_at IS NULL
     ORDER BY block_number, log_index
     LIMIT p_limit
  LOOP
    IF f_apply_event(r.id) THEN n := n + 1; END IF;
  END LOOP;
  RETURN n;
END $$;

CREATE OR REPLACE FUNCTION f_replay_projections() RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE n integer;
BEGIN
  DELETE FROM coupon_holding;
  DELETE FROM point_balance;
  -- state ของ coupon_code เป็น projection ด้วย · ต้องย้อนกลับไปค่าตั้งต้น
  UPDATE coupon_code SET state = 'ISSUED' WHERE state = 'REDEEMED';
  UPDATE chain_event SET applied_at = NULL WHERE orphaned_at IS NULL;

  n := f_apply_pending(1000000);
  RETURN n;
END $$;

CREATE OR REPLACE FUNCTION f_handle_reorg(
  p_fork_block bigint, p_owner text
) RETURNS text LANGUAGE plpgsql AS $$
DECLARE
  v_tip bigint; v_depth integer; v_n integer; v_final integer;
  v_outcome reorg_outcome; v_id text; v_stuck integer;
BEGIN
  SELECT last_block INTO v_tip FROM indexer_cursor WHERE id = 'main';
  IF p_fork_block > v_tip THEN
    RAISE EXCEPTION 'fork block % อยู่หลัง tip % · ไม่ใช่ reorg', p_fork_block, v_tip
      USING ERRCODE = 'check_violation';
  END IF;

  v_depth := v_tip - p_fork_block + 1;
  v_final := f_finality_depth(now());

  -- มี event ที่ถือว่า final แล้วอยู่ในช่วงที่จะหลุดหรือไม่
  SELECT count(*) INTO v_stuck FROM chain_event
   WHERE block_number >= p_fork_block AND orphaned_at IS NULL
     AND finalized_at IS NOT NULL;

  IF v_depth > v_final OR v_stuck > 0 THEN
    v_outcome := 'MANUAL_REVIEW';
  ELSE
    v_outcome := 'ROLLED_BACK';
  END IF;

  SELECT count(*) INTO v_n FROM chain_event
   WHERE block_number >= p_fork_block AND orphaned_at IS NULL;

  v_id := 'rg_' || to_char(clock_timestamp(), 'YYYYMMDDHH24MISSUS');
  INSERT INTO reorg_log (id, fork_block, old_tip, depth, finality_depth,
                         orphaned_events, outcome,
                         note)
  VALUES (v_id, p_fork_block, v_tip, v_depth, v_final, v_n, v_outcome,
          CASE WHEN v_stuck > 0
               THEN 'มี event ที่ final แล้ว ' || v_stuck || ' แถวอยู่ในช่วงที่หลุด'
               ELSE NULL END);

  IF v_outcome = 'MANUAL_REVIEW' THEN
    -- ไม่แตะอะไรเลย · ปล่อยให้คนตัดสิน
    RETURN v_id;
  END IF;

  -- ย้อนได้ · ต้องล้าง applied_at ก่อน เพราะ ck_orphan_not_applied
  UPDATE chain_event SET applied_at = NULL
   WHERE block_number >= p_fork_block AND orphaned_at IS NULL;
  UPDATE chain_event SET orphaned_at = now()
   WHERE block_number >= p_fork_block AND orphaned_at IS NULL;
  UPDATE raw_block SET orphaned_at = now()
   WHERE block_number >= p_fork_block AND orphaned_at IS NULL;

  -- ถอย cursor · ต้องบอกว่ากำลังจัดการ reorg ไม่งั้น trigger ไม่ยอม
  PERFORM set_config('app.reorg_in_progress', 'true', true);
  UPDATE indexer_cursor
     SET last_block = p_fork_block - 1,
         last_block_hash = (SELECT block_hash FROM raw_block
                             WHERE block_number = p_fork_block - 1
                               AND orphaned_at IS NULL),
         updated_at = now()
   WHERE id = 'main';
  PERFORM set_config('app.reorg_in_progress', 'false', true);

  PERFORM f_replay_projections();
  RETURN v_id;
END $$;

CREATE OR REPLACE FUNCTION f_mark_finalized() RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE v_tip bigint; v_depth integer; v_n integer;
BEGIN
  SELECT last_block INTO v_tip FROM indexer_cursor WHERE id = 'main';
  v_depth := f_finality_depth(now());

  UPDATE chain_event SET finalized_at = now()
   WHERE finalized_at IS NULL AND orphaned_at IS NULL
     AND applied_at IS NOT NULL
     AND block_number <= v_tip - v_depth;
  GET DIAGNOSTICS v_n = ROW_COUNT;
  RETURN v_n;
END $$;

-- --------------------------------------------------------- 3 · ตาราง
-- จาก feat-03 feat-02 feat-05 feat-06 feat-11 feat-09 feat-10 feat-12 feat-14
CREATE TABLE IF NOT EXISTS customer (
  id                     text PRIMARY KEY,
  phone_e164             text NOT NULL CHECK (phone_e164 ~ '^\+[1-9][0-9]{7,14}$'),
  display_name           text,
  status                 customer_status NOT NULL DEFAULT 'PENDING_WALLET',
  created_at             timestamptz NOT NULL DEFAULT now(),
  phone_verified_at      timestamptz NOT NULL,
  tel_hash               bytea,
  disabled_at            timestamptz,
  CONSTRAINT ck_phone_normalized CHECK (phone_e164 = f_normalize_phone(phone_e164))
);

-- จาก feat-01 feat-03 feat-04 feat-05 feat-06 feat-07 feat-11 feat-08 feat-09 feat-10 feat-12 feat-13 feat-14
CREATE TABLE IF NOT EXISTS merchant (
  id                     text PRIMARY KEY,
  name                   text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 200),
  status                 merchant_status NOT NULL DEFAULT 'PENDING',
  created_at             timestamptz NOT NULL DEFAULT now(),
  activated_at           timestamptz,
  CONSTRAINT ck_merchant_active CHECK ((status = 'ACTIVE') = (activated_at IS NOT NULL))
);

-- จาก feat-01 feat-03
CREATE TABLE IF NOT EXISTS merchant_admin (
  id                     text PRIMARY KEY,
  merchant_id            text NOT NULL REFERENCES merchant(id),
  email                  text NOT NULL,
  email_normalized       text NOT NULL CHECK (email_normalized = lower(btrim(email_normalized))),
  password_hash          text NOT NULL CHECK (password_hash LIKE '$argon2id$%'),
  display_name           text NOT NULL,
  disabled_at            timestamptz,
  created_at             timestamptz NOT NULL DEFAULT now(),
  last_login_at          timestamptz
);

-- จาก feat-03
CREATE TABLE IF NOT EXISTS auth_session (
  id                     text PRIMARY KEY,
  subject_kind           subject_kind NOT NULL,
  merchant_admin_id      text REFERENCES merchant_admin(id),
  customer_id            text REFERENCES customer(id),
  family_id              text NOT NULL,
  refresh_token_hash     bytea NOT NULL CHECK (octet_length(refresh_token_hash) = 32),
  parent_id              text REFERENCES auth_session(id),
  user_agent_hash        bytea,
  ip_hash                bytea,
  created_at             timestamptz NOT NULL DEFAULT now(),
  expires_at             timestamptz NOT NULL,
  used_at                timestamptz,
  revoked_at             timestamptz,
  revoked_reason         revoke_reason,
  CONSTRAINT ck_one_subject CHECK (num_nonnulls(merchant_admin_id, customer_id) = 1),
  CONSTRAINT ck_subject_matches CHECK ( (subject_kind = 'MERCHANT_ADMIN' AND merchant_admin_id IS NOT NULL) OR (subject_kind = 'CUSTOMER' AND customer_id IS NOT NULL) ),
  CONSTRAINT ck_expiry_after_create CHECK (expires_at > created_at),
  CONSTRAINT ck_revoke_has_reason CHECK ((revoked_at IS NULL) = (revoked_reason IS NULL))
);

-- จาก feat-03
CREATE TABLE IF NOT EXISTS auth_event (
  id                     text PRIMARY KEY,
  subject_kind           subject_kind,
  subject_id             text,
  kind                   text NOT NULL,
  session_id             text REFERENCES auth_session(id),
  ip_hash                bytea,
  metadata               jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at             timestamptz NOT NULL DEFAULT now()
);

-- จาก feat-01 feat-02 feat-04 feat-05 feat-07 feat-11 feat-08 feat-09 feat-10 feat-12 feat-13 feat-14
CREATE TABLE IF NOT EXISTS operation (
  id                     text PRIMARY KEY,
  actor_scope            text NOT NULL,
  kind                   operation_kind NOT NULL,
  status                 operation_status NOT NULL DEFAULT 'PENDING',
  subject_id             text,
  payload                jsonb NOT NULL,
  error_note             text,
  created_at             timestamptz NOT NULL DEFAULT now(),
  finalized_at           timestamptz,
  CONSTRAINT ck_finalized_consistent CHECK ((status = 'FINALIZED') = (finalized_at IS NOT NULL))
);

-- จาก feat-01 feat-04 feat-05 feat-07 feat-11 feat-08 feat-09 feat-10 feat-12 feat-13 feat-14
CREATE TABLE IF NOT EXISTS chain_event (
  id                     text PRIMARY KEY,
  block_number           bigint NOT NULL,
  block_hash             bytea NOT NULL CHECK (octet_length(block_hash) = 32),
  tx_hash                bytea NOT NULL CHECK (octet_length(tx_hash) = 32),
  log_index              integer NOT NULL CHECK (log_index >= 0),
  address                bytea NOT NULL CHECK (octet_length(address) = 20),
  topic0                 bytea NOT NULL CHECK (octet_length(topic0) = 32),
  topics                 jsonb NOT NULL DEFAULT '[]'::jsonb,
  data                   bytea NOT NULL,
  block_timestamp        timestamptz NOT NULL,
  event_name             chain_event_name NOT NULL DEFAULT 'UNKNOWN',
  args                   jsonb,
  operation_id           text REFERENCES operation(id),
  applied_at             timestamptz,
  orphaned_at            timestamptz,
  finalized_at           timestamptz,
  CONSTRAINT ck_known_has_args CHECK (event_name = 'UNKNOWN' OR args IS NOT NULL),
  CONSTRAINT ck_orphan_not_applied CHECK (orphaned_at IS NULL OR applied_at IS NULL)
);

-- จาก feat-07 feat-11 feat-08 feat-09 feat-10 feat-14
CREATE TABLE IF NOT EXISTS coupon_type (
  id                     text PRIMARY KEY,
  merchant_id            text NOT NULL REFERENCES merchant(id),
  name                   text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
  face_value             numeric(78,0) NOT NULL CHECK (face_value >= 0),
  total_supply           integer NOT NULL CHECK (total_supply BETWEEN 1 AND 1000000),
  status                 coupon_type_status NOT NULL DEFAULT 'REQUESTED',
  expires_at             timestamptz NOT NULL,
  created_at             timestamptz NOT NULL DEFAULT now(),
  live_at                timestamptz,
  contract_address       bytea CHECK (octet_length(contract_address) = 20),
  chain_type_id          numeric(78,0),
  CONSTRAINT ck_coupon_type_expiry CHECK (expires_at > created_at),
  CONSTRAINT ck_coupon_type_live CHECK ((status = 'LIVE') = (live_at IS NOT NULL) OR status = 'RETIRED')
);

-- จาก feat-07 feat-11 feat-08 feat-09 feat-10 feat-14
CREATE TABLE IF NOT EXISTS coupon_code (
  id                     text PRIMARY KEY,
  coupon_type_id         text NOT NULL REFERENCES coupon_type(id),
  code_hash              bytea NOT NULL CHECK (octet_length(code_hash) = 32),
  code_ref               bytea NOT NULL CHECK (octet_length(code_ref) = 32),
  state                  coupon_code_state NOT NULL DEFAULT 'ISSUED',
  holder_kind            holder_kind,
  holder_id              text,
  issue_batch_id         text,
  issued_at              timestamptz NOT NULL DEFAULT now(),
  held_at                timestamptz,
  redeemed_at            timestamptz,
  CONSTRAINT ck_code_holder CHECK ((holder_kind IS NULL) = (holder_id IS NULL)),
  CONSTRAINT ck_code_held CHECK (state <> 'HELD' OR holder_id IS NOT NULL),
  CONSTRAINT ck_code_redeemed CHECK ((state = 'REDEEMED') = (redeemed_at IS NOT NULL)),
  CONSTRAINT ck_redeemed_consistent CHECK ((state = 'REDEEMED') = (redeemed_at IS NOT NULL))
);

-- จาก feat-11 feat-10 feat-14
CREATE TABLE IF NOT EXISTS coupon_holding (
  id                     text PRIMARY KEY,
  coupon_type_id         text NOT NULL REFERENCES coupon_type(id),
  holder_kind            text NOT NULL CHECK (holder_kind IN ('MERCHANT','CUSTOMER')),
  holder_id              text NOT NULL,
  quantity               numeric(78,0) NOT NULL CHECK (quantity >= 0),
  updated_at             timestamptz NOT NULL DEFAULT now(),
  coupon_code_id         text REFERENCES coupon_code(id),
  chain_event_id         text,
  applied_at             timestamptz DEFAULT now(),
  as_of_block            bigint,
  as_of_time             timestamptz
);

-- จาก feat-07
CREATE TABLE IF NOT EXISTS coupon_type_chain (
  coupon_type_id         text PRIMARY KEY REFERENCES coupon_type(id),
  contract_address       bytea NOT NULL CHECK (octet_length(contract_address) = 20),
  chain_type_id          numeric(78,0) NOT NULL,
  deployed_block         bigint NOT NULL CHECK (deployed_block > 0),
  deployed_tx_hash       bytea NOT NULL CHECK (octet_length(deployed_tx_hash) = 32),
  applied_at             timestamptz NOT NULL DEFAULT now()
);

-- จาก feat-02
CREATE TABLE IF NOT EXISTS customer_session (
  id                     text PRIMARY KEY,
  customer_id            text NOT NULL REFERENCES customer(id),
  token_hash             bytea NOT NULL CHECK (octet_length(token_hash) = 32),
  created_at             timestamptz NOT NULL DEFAULT now(),
  expires_at             timestamptz NOT NULL,
  revoked_at             timestamptz,
  CONSTRAINT ck_session_window CHECK (expires_at > created_at)
);

-- จาก feat-12
CREATE TABLE IF NOT EXISTS psp_webhook (
  id                     text PRIMARY KEY,
  provider               text NOT NULL,
  provider_event_id      text NOT NULL,
  event_type             text NOT NULL,
  raw_body               bytea NOT NULL,
  signature              text NOT NULL,
  signature_ok           boolean NOT NULL,
  received_at            timestamptz NOT NULL DEFAULT now(),
  processed_at           timestamptz
);

-- จาก feat-12
CREATE TABLE IF NOT EXISTS thb_deposit (
  id                     text PRIMARY KEY,
  holder_kind            holder_kind NOT NULL,
  holder_id              text NOT NULL,
  provider               text NOT NULL,
  provider_ref           text NOT NULL,
  amount_satang          bigint NOT NULL CHECK (amount_satang > 0),
  amount_base            numeric(78,0) NOT NULL CHECK (amount_base > 0),
  status                 deposit_status NOT NULL DEFAULT 'PENDING_PAYMENT',
  webhook_id             text REFERENCES psp_webhook(id),
  created_at             timestamptz NOT NULL DEFAULT now(),
  confirmed_at           timestamptz,
  minted_at              timestamptz,
  expires_at             timestamptz NOT NULL DEFAULT now() + interval '24 hours',
  CONSTRAINT ck_dep_confirmed CHECK ((confirmed_at IS NOT NULL) = (status IN ('CONFIRMED','MINTED','REVERSED'))),
  CONSTRAINT ck_dep_minted CHECK ((minted_at IS NOT NULL) = (status IN ('MINTED','REVERSED'))),
  CONSTRAINT ck_dep_needs_webhook CHECK (status IN ('PENDING_PAYMENT','FAILED','EXPIRED') OR webhook_id IS NOT NULL)
);

-- จาก feat-12
CREATE TABLE IF NOT EXISTS deposit_reversal (
  id                     text PRIMARY KEY,
  deposit_id             text NOT NULL REFERENCES thb_deposit(id),
  webhook_id             text NOT NULL REFERENCES psp_webhook(id),
  operation_id           text NOT NULL REFERENCES operation(id),
  amount_base            numeric(78,0) NOT NULL CHECK (amount_base > 0),
  available_at_reversal  numeric(78,0) NOT NULL CHECK (available_at_reversal >= 0),
  outcome                reversal_outcome NOT NULL,
  note                   text,
  created_at             timestamptz NOT NULL DEFAULT now()
);

-- จาก feat-01 feat-02 feat-04 feat-05 feat-11
CREATE TABLE IF NOT EXISTS wallet (
  id                     text PRIMARY KEY,
  address                bytea NOT NULL CHECK (octet_length(address) = 20),
  key_ref                text NOT NULL,
  owner_kind             text NOT NULL CHECK (owner_kind IN ('MERCHANT','CUSTOMER','PLATFORM')),
  owner_id               text NOT NULL,
  created_at             timestamptz NOT NULL DEFAULT now(),
  kms_key_ref            text CHECK (kms_key_ref ~ '^kms://[A-Za-z0-9/_.:+=-]+$' AND length(kms_key_ref) <= 200),
  status                 wallet_status DEFAULT 'REQUESTED',
  live_at                timestamptz,
  CONSTRAINT ck_wallet_live CHECK ((status = 'LIVE') = (live_at IS NOT NULL))
);

-- จาก feat-11
CREATE TABLE IF NOT EXISTS escrow (
  id                     text PRIMARY KEY,
  chain_token_id         numeric(78,0) NOT NULL,
  contract_address       bytea NOT NULL CHECK (octet_length(contract_address) = 20),
  seller_wallet_id       text NOT NULL REFERENCES wallet(id),
  buyer_wallet_id        text NOT NULL REFERENCES wallet(id),
  total_amount           numeric(78,0) NOT NULL CHECK (total_amount >= 0),
  remaining_amount       numeric(78,0) NOT NULL CHECK (remaining_amount >= 0),
  coupons_locked         integer NOT NULL CHECK (coupons_locked > 0),
  coupons_redeemed       integer NOT NULL DEFAULT 0 CHECK (coupons_redeemed >= 0),
  active                 boolean NOT NULL DEFAULT true,
  locked_at              timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_escrow_remaining CHECK (remaining_amount <= total_amount),
  CONSTRAINT ck_escrow_count CHECK (coupons_redeemed <= coupons_locked)
);

-- จาก feat-04 feat-05 feat-06 feat-14
CREATE TABLE IF NOT EXISTS point_type (
  id                     text PRIMARY KEY,
  merchant_id            text NOT NULL REFERENCES merchant(id),
  name                   text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 60),
  symbol                 text NOT NULL CHECK (symbol ~ '^[A-Z][A-Z0-9]{1,10}$'),
  decimals               smallint NOT NULL CHECK (decimals BETWEEN 0 AND 30),
  status                 point_type_status NOT NULL DEFAULT 'REQUESTED',
  created_at             timestamptz NOT NULL DEFAULT now(),
  live_at                timestamptz,
  retired_at             timestamptz,
  CONSTRAINT ck_point_type_live CHECK ((status = 'LIVE') = (live_at IS NOT NULL) OR status = 'RETIRED'),
  CONSTRAINT ck_point_type_retired CHECK ((status = 'RETIRED') = (retired_at IS NOT NULL))
);

-- จาก feat-05 feat-06
CREATE TABLE IF NOT EXISTS point_balance_bucket (
  id                     text PRIMARY KEY,
  point_type_id          text NOT NULL REFERENCES point_type(id),
  holder_kind            holder_kind NOT NULL,
  holder_id              text NOT NULL,
  epoch_index            integer NOT NULL CHECK (epoch_index >= 0),
  amount                 numeric(78,0) NOT NULL CHECK (amount >= 0),
  received_at            timestamptz NOT NULL,
  expires_at             timestamptz NOT NULL,
  updated_at             timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_bucket_expiry CHECK (expires_at > received_at)
);

-- จาก feat-06
CREATE TABLE IF NOT EXISTS expiry_notice (
  id                     text PRIMARY KEY,
  bucket_id              text NOT NULL REFERENCES point_balance_bucket(id) ON DELETE CASCADE,
  threshold_days         integer NOT NULL CHECK (threshold_days IN (30, 7, 1)),
  notified_at            timestamptz NOT NULL DEFAULT now(),
  channel                text NOT NULL CHECK (channel IN ('PUSH','EMAIL','SMS'))
);

-- จาก feat-10
CREATE TABLE IF NOT EXISTS fee_schedule (
  id                     text PRIMARY KEY,
  bps                    integer NOT NULL CHECK (bps BETWEEN 0 AND 10000),
  effective_from         timestamptz NOT NULL,
  note                   text
);

-- จาก feat-14
CREATE TABLE IF NOT EXISTS finality_config (
  id                     text PRIMARY KEY,
  depth_blocks           integer NOT NULL CHECK (depth_blocks BETWEEN 1 AND 1000),
  effective_from         timestamptz NOT NULL,
  note                   text
);

-- จาก feat-01 feat-02 feat-04 feat-05 feat-07 feat-11 feat-08 feat-09 feat-10 feat-12 feat-13
CREATE TABLE IF NOT EXISTS idempotency_key (
  id                     text PRIMARY KEY,
  scope                  text NOT NULL,
  key                    text NOT NULL CHECK (length(key) BETWEEN 8 AND 255),
  fingerprint            bytea NOT NULL CHECK (octet_length(fingerprint) = 32),
  operation_id           text,
  response_status        integer,
  response_body          jsonb,
  created_at             timestamptz NOT NULL DEFAULT now(),
  expires_at             timestamptz NOT NULL DEFAULT now() + interval '72 hours'
);

-- จาก feat-01 feat-04 feat-06 feat-14
CREATE TABLE IF NOT EXISTS indexer_cursor (
  id                     text PRIMARY KEY CHECK (id = 'main'),
  last_block             bigint NOT NULL CHECK (last_block >= 0),
  last_block_hash        bytea CHECK (last_block_hash IS NULL OR octet_length(last_block_hash) = 32),
  lease_owner            text,
  lease_until            timestamptz,
  updated_at             timestamptz NOT NULL DEFAULT now(),
  contract_address       bytea CHECK (octet_length(contract_address) = 20),
  CONSTRAINT ck_lease_pair CHECK ((lease_owner IS NULL) = (lease_until IS NULL)),
  CONSTRAINT ck_cursor_hash CHECK ((last_block = 0) = (last_block_hash IS NULL))
);

-- จาก feat-07
CREATE TABLE IF NOT EXISTS issue_batch (
  id                     text PRIMARY KEY,
  coupon_type_id         text NOT NULL REFERENCES coupon_type(id),
  operation_id           text,
  quantity               integer NOT NULL CHECK (quantity BETWEEN 1 AND 10000),
  created_at             timestamptz NOT NULL DEFAULT now()
);

-- จาก feat-05 feat-10 feat-12
CREATE TABLE IF NOT EXISTS ledger_entry (
  id                     text PRIMARY KEY,
  operation_id           text NOT NULL REFERENCES operation(id),
  entry_seq              integer NOT NULL CHECK (entry_seq >= 1),
  account                ledger_account NOT NULL,
  account_id             text NOT NULL,
  asset                  text NOT NULL DEFAULT 'THB' CHECK (asset = 'THB'),
  amount                 numeric(78,0) NOT NULL CHECK (amount <> 0),
  memo                   text,
  created_at             timestamptz NOT NULL DEFAULT now(),
  point_type_id          text REFERENCES point_type(id),
  holder_kind            holder_kind,
  holder_id              text,
  direction              ledger_direction,
  chain_event_id         text
);

-- จาก feat-08 feat-09 feat-10
CREATE TABLE IF NOT EXISTS listing (
  id                     text PRIMARY KEY,
  seller_kind            holder_kind NOT NULL,
  seller_id              text NOT NULL,
  coupon_type_id         text NOT NULL REFERENCES coupon_type(id),
  unit_price             numeric(78,0) NOT NULL CHECK (unit_price > 0),
  quantity               integer NOT NULL CHECK (quantity BETWEEN 1 AND 500),
  status                 listing_status NOT NULL DEFAULT 'PENDING',
  created_at             timestamptz NOT NULL DEFAULT now(),
  active_at              timestamptz,
  closed_at              timestamptz,
  CONSTRAINT ck_listing_active CHECK ((status = 'ACTIVE') = (active_at IS NOT NULL) OR status IN ('SOLD','DELISTED')),
  CONSTRAINT ck_listing_closed CHECK ((status IN ('SOLD','DELISTED')) = (closed_at IS NOT NULL)),
  CONSTRAINT ck_listing_close_order CHECK (closed_at IS NULL OR closed_at >= active_at)
);

-- จาก feat-08 feat-09 feat-10
CREATE TABLE IF NOT EXISTS listing_chain (
  listing_id             text PRIMARY KEY REFERENCES listing(id),
  contract_address       bytea NOT NULL CHECK (octet_length(contract_address) = 20),
  chain_listing_id       numeric(78,0) NOT NULL,
  created_block          bigint NOT NULL CHECK (created_block > 0),
  created_tx_hash        bytea NOT NULL CHECK (octet_length(created_tx_hash) = 32),
  closed_block           bigint CHECK (closed_block IS NULL OR closed_block > 0),
  closed_tx_hash         bytea CHECK (closed_tx_hash IS NULL OR octet_length(closed_tx_hash) = 32),
  close_reason           listing_close_reason,
  applied_at             timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_chain_close CHECK ((close_reason IS NULL) = (closed_block IS NULL) AND (close_reason IS NULL) = (closed_tx_hash IS NULL)),
  CONSTRAINT ck_chain_close_order CHECK (closed_block IS NULL OR closed_block >= created_block)
);

-- จาก feat-08 feat-09 feat-10
CREATE TABLE IF NOT EXISTS listing_item (
  id                     text PRIMARY KEY,
  listing_id             text NOT NULL REFERENCES listing(id) ON DELETE CASCADE,
  coupon_code_id         text NOT NULL REFERENCES coupon_code(id)
);

-- จาก feat-03
CREATE TABLE IF NOT EXISTS login_attempt (
  id                     text PRIMARY KEY,
  identifier_hash        bytea NOT NULL CHECK (octet_length(identifier_hash) = 32),
  ip_hash                bytea NOT NULL CHECK (octet_length(ip_hash) = 32),
  succeeded              boolean NOT NULL,
  failure_code           text,
  created_at             timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_failure_code CHECK (succeeded = (failure_code IS NULL))
);

-- จาก feat-03
CREATE TABLE IF NOT EXISTS login_lockout (
  identifier_hash        bytea PRIMARY KEY CHECK (octet_length(identifier_hash) = 32),
  locked_until           timestamptz NOT NULL,
  attempt_count          integer NOT NULL CHECK (attempt_count > 0),
  updated_at             timestamptz NOT NULL DEFAULT now()
);

-- จาก feat-01 feat-02 feat-04 feat-05 feat-07 feat-11 feat-08 feat-09 feat-10 feat-12 feat-13
CREATE TABLE IF NOT EXISTS operation_event (
  id                     text PRIMARY KEY,
  operation_id           text NOT NULL REFERENCES operation(id) ON DELETE CASCADE,
  seq                    integer NOT NULL CHECK (seq >= 1),
  from_status            operation_status,
  to_status              operation_status NOT NULL,
  actor                  actor_type NOT NULL DEFAULT 'SYSTEM',
  tx_hash                bytea,
  metadata               jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at             timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_first_event CHECK ((seq = 1) = (from_status IS NULL))
);

-- จาก feat-01 feat-02 feat-04 feat-05 feat-07 feat-08 feat-09 feat-10 feat-12
CREATE TABLE IF NOT EXISTS signer (
  address                bytea PRIMARY KEY CHECK (octet_length(address) = 20),
  key_ref                text NOT NULL,
  next_nonce             bigint NOT NULL DEFAULT 0 CHECK (next_nonce >= 0),
  enabled                boolean NOT NULL DEFAULT true,
  updated_at             timestamptz NOT NULL DEFAULT now()
);

-- จาก feat-01 feat-02 feat-04 feat-05 feat-07 feat-11 feat-08 feat-09 feat-10 feat-12 feat-13
CREATE TABLE IF NOT EXISTS operation_step (
  id                     text PRIMARY KEY,
  operation_id           text NOT NULL REFERENCES operation(id) ON DELETE CASCADE,
  step_index             integer NOT NULL CHECK (step_index >= 1),
  name                   text NOT NULL,
  recipient_count        integer NOT NULL DEFAULT 1 CHECK (recipient_count BETWEEN 1 AND 100),
  signer_address         bytea REFERENCES signer(address),
  nonce                  bigint CHECK (nonce IS NULL OR nonce >= 0),
  raw_tx                 bytea,
  tx_hash                bytea CHECK (tx_hash IS NULL OR octet_length(tx_hash) = 32),
  status                 step_status NOT NULL DEFAULT 'PENDING',
  submitted_at           timestamptz,
  confirmed_at           timestamptz,
  finalized_at           timestamptz,
  mint_count             integer DEFAULT 1 CHECK (mint_count BETWEEN 1 AND 500),
  CONSTRAINT ck_rawtx_before_broadcast CHECK (status = 'PENDING' OR raw_tx IS NOT NULL)
);

-- จาก feat-02
CREATE TABLE IF NOT EXISTS otp_challenge (
  id                     text PRIMARY KEY,
  phone_e164             text NOT NULL CHECK (phone_e164 ~ '^\+[1-9][0-9]{7,14}$'),
  code_hash              bytea NOT NULL CHECK (octet_length(code_hash) = 32),
  pepper_ref             text NOT NULL CHECK (pepper_ref ~ '^kms://'),
  status                 challenge_status NOT NULL DEFAULT 'PENDING',
  attempts               integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  max_attempts           integer NOT NULL DEFAULT 5 CHECK (max_attempts BETWEEN 1 AND 10),
  created_at             timestamptz NOT NULL DEFAULT now(),
  expires_at             timestamptz NOT NULL,
  verified_at            timestamptz,
  consumed_at            timestamptz,
  CONSTRAINT ck_ch_verified CHECK ((verified_at IS NOT NULL) = (status IN ('VERIFIED','CONSUMED'))),
  CONSTRAINT ck_ch_consumed CHECK ((consumed_at IS NOT NULL) = (status = 'CONSUMED')),
  CONSTRAINT ck_ch_attempts CHECK (attempts <= max_attempts),
  CONSTRAINT ck_ch_window CHECK (expires_at > created_at),
  CONSTRAINT ck_ch_normalized CHECK (phone_e164 = f_normalize_phone(phone_e164))
);

-- จาก feat-02
CREATE TABLE IF NOT EXISTS otp_attempt (
  id                     text PRIMARY KEY,
  challenge_id           text NOT NULL REFERENCES otp_challenge(id) ON DELETE CASCADE,
  attempt_no             integer NOT NULL CHECK (attempt_no >= 1),
  result                 attempt_result NOT NULL,
  ip_hash                bytea CHECK (ip_hash IS NULL OR octet_length(ip_hash) = 32),
  created_at             timestamptz NOT NULL DEFAULT now()
);

-- จาก feat-01 feat-02 feat-04 feat-05 feat-07 feat-11 feat-08 feat-09 feat-10 feat-12 feat-13
CREATE TABLE IF NOT EXISTS outbox (
  id                     text PRIMARY KEY,
  operation_id           text NOT NULL REFERENCES operation(id) ON DELETE CASCADE,
  topic                  text NOT NULL CHECK (topic ~ '\.v[0-9]+$'),
  payload                jsonb NOT NULL,
  created_at             timestamptz NOT NULL DEFAULT now(),
  sent_at                timestamptz
);

-- จาก feat-02
CREATE TABLE IF NOT EXISTS phone_throttle (
  phone_e164             text PRIMARY KEY CHECK (phone_e164 ~ '^\+[1-9][0-9]{7,14}$'),
  window_start           timestamptz NOT NULL DEFAULT now(),
  sent_count             integer NOT NULL DEFAULT 0 CHECK (sent_count >= 0),
  last_sent_at           timestamptz,
  locked_until           timestamptz
);

-- จาก feat-14
CREATE TABLE IF NOT EXISTS point_balance (
  point_type_id          text NOT NULL REFERENCES point_type(id),
  holder_kind            holder_kind NOT NULL,
  holder_id              text NOT NULL,
  amount                 numeric(78,0) NOT NULL CHECK (amount >= 0),
  as_of_block            bigint NOT NULL,
  PRIMARY KEY (point_type_id, holder_kind, holder_id)
);

-- จาก feat-04 feat-05 feat-06
CREATE TABLE IF NOT EXISTS point_type_chain (
  point_type_id          text PRIMARY KEY REFERENCES point_type(id),
  contract_address       bytea NOT NULL CHECK (octet_length(contract_address) = 20),
  deployed_block         bigint NOT NULL CHECK (deployed_block > 0),
  deployed_tx_hash       bytea NOT NULL CHECK (octet_length(deployed_tx_hash) = 32),
  genesis_at             timestamptz NOT NULL,
  epoch_seconds          integer NOT NULL CHECK (epoch_seconds > 0),
  validity_epochs        integer NOT NULL CHECK (validity_epochs > 0),
  applied_at             timestamptz NOT NULL DEFAULT now()
);

-- จาก feat-06
CREATE TABLE IF NOT EXISTS projection_watermark (
  id                     boolean PRIMARY KEY DEFAULT true CHECK (id),
  last_block             bigint NOT NULL CHECK (last_block >= 0),
  last_block_at          timestamptz NOT NULL,
  updated_at             timestamptz NOT NULL DEFAULT now()
);

-- จาก feat-10
CREATE TABLE IF NOT EXISTS purchase (
  id                     text PRIMARY KEY,
  listing_id             text NOT NULL REFERENCES listing(id),
  buyer_kind             holder_kind NOT NULL,
  buyer_id               text NOT NULL,
  seller_kind            holder_kind NOT NULL,
  seller_id              text NOT NULL,
  unit_price             numeric(78,0) NOT NULL CHECK (unit_price > 0),
  quantity               integer NOT NULL CHECK (quantity > 0),
  total_price            numeric(78,0) NOT NULL CHECK (total_price > 0),
  fee_bps                integer NOT NULL CHECK (fee_bps BETWEEN 0 AND 10000),
  fee_amount             numeric(78,0) NOT NULL CHECK (fee_amount >= 0),
  net_to_seller          numeric(78,0) NOT NULL CHECK (net_to_seller >= 0),
  status                 purchase_status NOT NULL DEFAULT 'PENDING',
  operation_id           text NOT NULL,
  created_at             timestamptz NOT NULL DEFAULT now(),
  settled_at             timestamptz,
  CONSTRAINT ck_purchase_settled CHECK ((status = 'SETTLED') = (settled_at IS NOT NULL)),
  CONSTRAINT ck_no_self_trade CHECK (NOT (buyer_kind = seller_kind AND buyer_id = seller_id))
);

-- จาก feat-14
CREATE TABLE IF NOT EXISTS raw_block (
  block_hash             bytea PRIMARY KEY CHECK (octet_length(block_hash) = 32),
  block_number           bigint NOT NULL CHECK (block_number > 0),
  parent_hash            bytea NOT NULL CHECK (octet_length(parent_hash) = 32),
  timestamp              timestamptz NOT NULL,
  tx_count               integer NOT NULL DEFAULT 0 CHECK (tx_count >= 0),
  orphaned_at            timestamptz,
  read_at                timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_not_own_parent CHECK (block_hash <> parent_hash)
);

-- จาก feat-14
CREATE TABLE IF NOT EXISTS reorg_log (
  id                     text PRIMARY KEY,
  detected_at            timestamptz NOT NULL DEFAULT now(),
  fork_block             bigint NOT NULL CHECK (fork_block > 0),
  old_tip                bigint NOT NULL,
  depth                  integer NOT NULL CHECK (depth >= 1),
  finality_depth         integer NOT NULL,
  orphaned_events        integer NOT NULL CHECK (orphaned_events >= 0),
  outcome                reorg_outcome NOT NULL,
  note                   text,
  CONSTRAINT ck_depth_matches CHECK (depth = old_tip - fork_block + 1)
);

-- จาก feat-05 feat-06 feat-11 feat-08 feat-09 feat-10 feat-12
CREATE TABLE IF NOT EXISTS reservation (
  id                     text PRIMARY KEY,
  operation_id           text NOT NULL REFERENCES operation(id) ON DELETE CASCADE,
  subject_type           reservation_subject NOT NULL,
  subject_id             text NOT NULL,
  amount                 numeric(78,0) NOT NULL CHECK (amount > 0),
  created_at             timestamptz NOT NULL DEFAULT now(),
  released_at            timestamptz
);

-- จาก feat-10 feat-12
CREATE TABLE IF NOT EXISTS thb_balance (
  account                ledger_account NOT NULL,
  account_id             text NOT NULL,
  amount                 numeric(78,0) NOT NULL DEFAULT 0,
  updated_at             timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (account, account_id),
  CONSTRAINT ck_balance_sign CHECK (amount >= 0 OR account = 'ISSUANCE')
);

-- จาก feat-12
CREATE TABLE IF NOT EXISTS thb_mint (
  deposit_id             text PRIMARY KEY REFERENCES thb_deposit(id),
  operation_id           text NOT NULL REFERENCES operation(id),
  amount_base            numeric(78,0) NOT NULL CHECK (amount_base > 0),
  to_account             ledger_account NOT NULL,
  to_account_id          text NOT NULL,
  chain_event_id         text REFERENCES chain_event(id),
  tx_hash                bytea CHECK (tx_hash IS NULL OR octet_length(tx_hash) = 32),
  minted_block           bigint CHECK (minted_block IS NULL OR minted_block > 0),
  applied_at             timestamptz,
  CONSTRAINT ck_mint_applied CHECK ((applied_at IS NULL) = (chain_event_id IS NULL))
);

-- จาก feat-13
CREATE TABLE IF NOT EXISTS webhook_endpoint (
  id                     text PRIMARY KEY,
  merchant_id            text NOT NULL REFERENCES merchant(id),
  url                    text NOT NULL CHECK (url ~ '^https://'),
  secret_ref             text NOT NULL CHECK (secret_ref ~ '^kms://'),
  event_types            text[] NOT NULL CHECK (cardinality(event_types) >= 1 AND array_position(event_types, NULL) IS NULL),
  status                 endpoint_status NOT NULL DEFAULT 'ACTIVE',
  max_attempts           integer NOT NULL DEFAULT 6 CHECK (max_attempts BETWEEN 1 AND 12),
  created_at             timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_https_only CHECK (url NOT LIKE 'http://%')
);

-- จาก feat-13
CREATE TABLE IF NOT EXISTS webhook_delivery (
  id                     text PRIMARY KEY,
  endpoint_id            text NOT NULL REFERENCES webhook_endpoint(id),
  operation_id           text NOT NULL REFERENCES operation(id),
  event_seq              integer NOT NULL CHECK (event_seq >= 1),
  delivery_seq           integer NOT NULL CHECK (delivery_seq >= 1),
  event_type             text NOT NULL,
  payload                jsonb NOT NULL,
  signature              text NOT NULL,
  signed_at              timestamptz NOT NULL,
  status                 delivery_status NOT NULL DEFAULT 'PENDING',
  attempt                integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
  next_attempt_at        timestamptz NOT NULL DEFAULT now(),
  claimed_at             timestamptz,
  sent_at                timestamptz,
  dead_at                timestamptz,
  created_at             timestamptz NOT NULL DEFAULT now(),
  replay_of              text,
  CONSTRAINT ck_delivery_sent CHECK ((status = 'SENT') = (sent_at IS NOT NULL)),
  CONSTRAINT ck_delivery_dead CHECK ((status = 'DEAD') = (dead_at IS NOT NULL)),
  CONSTRAINT ck_delivery_claim CHECK (status <> 'IN_FLIGHT' OR claimed_at IS NOT NULL),
  CONSTRAINT ck_no_self_replay CHECK (replay_of IS NULL OR replay_of <> id)
);

-- จาก feat-13
CREATE TABLE IF NOT EXISTS webhook_delivery_attempt (
  id                     text PRIMARY KEY,
  delivery_id            text NOT NULL REFERENCES webhook_delivery(id) ON DELETE CASCADE,
  attempt                integer NOT NULL CHECK (attempt >= 1),
  started_at             timestamptz NOT NULL,
  finished_at            timestamptz NOT NULL,
  response_status        integer CHECK (response_status IS NULL OR response_status BETWEEN 100 AND 599),
  response_body          text,
  error_note             text,
  CONSTRAINT ck_attempt_time CHECK (finished_at >= started_at),
  CONSTRAINT ck_attempt_outcome CHECK ((response_status IS NULL) <> (error_note IS NULL))
);

-- --------------------------------------------- 4 · constraint เพิ่มเติม
DO $do$ BEGIN
ALTER TABLE idempotency_key
  ADD CONSTRAINT fk_idem_operation FOREIGN KEY (operation_id) REFERENCES operation(id);
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
ALTER TABLE ledger_entry
  ADD CONSTRAINT fk_ledger_operation FOREIGN KEY (operation_id) REFERENCES operation(id);
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
ALTER TABLE coupon_code
  ADD CONSTRAINT fk_code_batch FOREIGN KEY (issue_batch_id) REFERENCES issue_batch(id);
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
ALTER TABLE issue_batch
  ADD CONSTRAINT fk_batch_operation FOREIGN KEY (operation_id) REFERENCES operation(id);
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
ALTER TABLE purchase
  ADD CONSTRAINT fk_purchase_operation FOREIGN KEY (operation_id) REFERENCES operation(id);
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
ALTER TABLE coupon_holding
  ADD CONSTRAINT fk_holding_event FOREIGN KEY (chain_event_id) REFERENCES chain_event(id);
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
ALTER TABLE webhook_delivery
  ADD CONSTRAINT fk_replay_of FOREIGN KEY (replay_of) REFERENCES webhook_delivery(id);
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
ALTER TABLE chain_event
  ADD CONSTRAINT fk_event_block FOREIGN KEY (block_hash) REFERENCES raw_block(block_hash);
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;

-- --------------------------------------------------------- 5 · index
CREATE INDEX IF NOT EXISTS ix_merchant_pending ON merchant (created_at) WHERE status = 'PENDING';
CREATE UNIQUE INDEX IF NOT EXISTS uq_admin_email ON merchant_admin (email_normalized);
CREATE INDEX IF NOT EXISTS ix_admin_merchant ON merchant_admin (merchant_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_wallet_address ON wallet (address);
CREATE UNIQUE INDEX IF NOT EXISTS uq_wallet_owner   ON wallet (owner_kind, owner_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_idem ON idempotency_key (scope, key);
CREATE INDEX IF NOT EXISTS ix_idem_expiry ON idempotency_key (expires_at);
CREATE INDEX IF NOT EXISTS ix_operation_open ON operation (created_at)
  WHERE status NOT IN ('FINALIZED','CHAIN_REVERTED');
CREATE UNIQUE INDEX IF NOT EXISTS uq_provision_inflight
  ON operation (subject_id)
  WHERE kind = 'MERCHANT_PROVISION'
    AND status NOT IN ('FINALIZED','CHAIN_REVERTED');
CREATE UNIQUE INDEX IF NOT EXISTS uq_step_order  ON operation_step (operation_id, step_index);
CREATE UNIQUE INDEX IF NOT EXISTS uq_step_txhash ON operation_step (tx_hash) WHERE tx_hash IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_signer_nonce ON operation_step (signer_address, nonce)
  WHERE signer_address IS NOT NULL AND nonce IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox (created_at) WHERE sent_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_opevent_operation ON operation_event (operation_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_chain_event ON chain_event (tx_hash, log_index);
CREATE UNIQUE INDEX IF NOT EXISTS uq_customer_tel ON customer (tel_hash);
CREATE UNIQUE INDEX IF NOT EXISTS uq_session_token ON auth_session (refresh_token_hash);
CREATE UNIQUE INDEX IF NOT EXISTS uq_session_parent
  ON auth_session (parent_id) WHERE parent_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_session_family ON auth_session (family_id);
CREATE INDEX IF NOT EXISTS ix_session_subject ON auth_session (subject_kind, merchant_admin_id, customer_id);
CREATE INDEX IF NOT EXISTS ix_session_live ON auth_session (expires_at)
  WHERE used_at IS NULL AND revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_attempt_identifier ON login_attempt (identifier_hash, created_at)
  WHERE NOT succeeded;
CREATE INDEX IF NOT EXISTS ix_attempt_ip ON login_attempt (ip_hash, created_at)
  WHERE NOT succeeded;
CREATE INDEX IF NOT EXISTS ix_auth_event_subject ON auth_event (subject_kind, subject_id, created_at);
CREATE INDEX IF NOT EXISTS ix_auth_event_kind ON auth_event (kind, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_customer_phone ON customer (phone_e164);
CREATE UNIQUE INDEX IF NOT EXISTS uq_challenge_live ON otp_challenge (phone_e164)
  WHERE status = 'PENDING';
CREATE INDEX IF NOT EXISTS ix_challenge_expiring ON otp_challenge (expires_at)
  WHERE status = 'PENDING';
CREATE UNIQUE INDEX IF NOT EXISTS uq_attempt_no ON otp_attempt (challenge_id, attempt_no);
CREATE INDEX IF NOT EXISTS ix_attempt_recent ON otp_attempt (created_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_customer_provision_inflight
  ON operation (subject_id)
  WHERE kind = 'CUSTOMER_PROVISION' AND status NOT IN ('FINALIZED','CHAIN_REVERTED');
CREATE UNIQUE INDEX IF NOT EXISTS uq_customer_session_token ON customer_session (token_hash);
CREATE INDEX IF NOT EXISTS ix_customer_session_live ON customer_session (customer_id)
  WHERE revoked_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_point_symbol
  ON point_type (merchant_id, symbol) WHERE status <> 'FAILED';
CREATE UNIQUE INDEX IF NOT EXISTS uq_point_name
  ON point_type (merchant_id, lower(btrim(name))) WHERE status <> 'FAILED';
CREATE INDEX IF NOT EXISTS ix_point_type_merchant ON point_type (merchant_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_point_chain_address ON point_type_chain (contract_address);
CREATE UNIQUE INDEX IF NOT EXISTS uq_point_deploy_inflight
  ON operation (subject_id)
  WHERE kind = 'POINT_TYPE_CREATE'
    AND status NOT IN ('FINALIZED','CHAIN_REVERTED');
CREATE UNIQUE INDEX IF NOT EXISTS uq_bucket
  ON point_balance_bucket (point_type_id, holder_kind, holder_id, epoch_index);
CREATE INDEX IF NOT EXISTS ix_bucket_live
  ON point_balance_bucket (point_type_id, holder_kind, holder_id, expires_at)
  WHERE amount > 0;
CREATE INDEX IF NOT EXISTS ix_ledger_holder ON ledger_entry (point_type_id, holder_kind, holder_id);
CREATE INDEX IF NOT EXISTS ix_ledger_operation ON ledger_entry (operation_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_reservation_exclusive
  ON reservation (subject_type, subject_id)
  WHERE released_at IS NULL
    AND subject_type IN ('COUPON_UNIT','LISTING','ESCROW');
CREATE INDEX IF NOT EXISTS ix_reservation_open
  ON reservation (subject_type, subject_id) WHERE released_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_bucket_live_asof
  ON point_balance_bucket (point_type_id, holder_kind, holder_id, expires_at)
  INCLUDE (amount)
  WHERE amount > 0;
CREATE INDEX IF NOT EXISTS ix_reservation_open_asof
  ON reservation (subject_type, subject_id) INCLUDE (amount)
  WHERE released_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_notice ON expiry_notice (bucket_id, threshold_days);
CREATE UNIQUE INDEX IF NOT EXISTS uq_coupon_type_name
  ON coupon_type (merchant_id, lower(btrim(name))) WHERE status <> 'FAILED';
CREATE INDEX IF NOT EXISTS ix_coupon_type_merchant ON coupon_type (merchant_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_coupon_chain ON coupon_type_chain (contract_address, chain_type_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_code_hash ON coupon_code (code_hash);
CREATE UNIQUE INDEX IF NOT EXISTS uq_code_ref  ON coupon_code (code_ref);
CREATE INDEX IF NOT EXISTS ix_code_type_state ON coupon_code (coupon_type_id, state);
CREATE INDEX IF NOT EXISTS ix_code_holder ON coupon_code (holder_kind, holder_id) WHERE state = 'HELD';
CREATE INDEX IF NOT EXISTS ix_batch_type ON issue_batch (coupon_type_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_coupon_deploy_inflight
  ON operation (subject_id)
  WHERE kind = 'COUPON_TYPE_CREATE'
    AND status NOT IN ('FINALIZED','CHAIN_REVERTED');
CREATE UNIQUE INDEX IF NOT EXISTS uq_coupon_type_chain ON coupon_type (contract_address, chain_type_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_coupon_code_hash ON coupon_code (code_hash);
CREATE UNIQUE INDEX IF NOT EXISTS uq_coupon_code_ref  ON coupon_code (code_ref);
CREATE INDEX IF NOT EXISTS ix_coupon_code_holder ON coupon_code (holder_kind, holder_id) WHERE state = 'HELD';
CREATE INDEX IF NOT EXISTS ix_chain_event_unapplied ON chain_event (block_number)
  WHERE applied_at IS NULL AND orphaned_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_holding ON coupon_holding (coupon_type_id, holder_kind, holder_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_escrow_active
  ON escrow (contract_address, chain_token_id) WHERE active;
CREATE INDEX IF NOT EXISTS ix_listing_seller ON listing (seller_kind, seller_id, status);
CREATE INDEX IF NOT EXISTS ix_listing_browse ON listing (coupon_type_id, unit_price)
  WHERE status = 'ACTIVE';
CREATE UNIQUE INDEX IF NOT EXISTS uq_item_in_listing ON listing_item (listing_id, coupon_code_id);
CREATE INDEX IF NOT EXISTS ix_item_code ON listing_item (coupon_code_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_listing_chain ON listing_chain (contract_address, chain_listing_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_listing_inflight
  ON operation (subject_id)
  WHERE kind IN ('LISTING_CREATE','LISTING_DELETE')
    AND status NOT IN ('FINALIZED','CHAIN_REVERTED');
CREATE INDEX IF NOT EXISTS ix_listing_closed ON listing (closed_at)
  WHERE status IN ('SOLD','DELISTED');
CREATE UNIQUE INDEX IF NOT EXISTS uq_listing_inflight_v2
  ON operation (subject_id)
  WHERE kind IN ('LISTING_CREATE','LISTING_DELETE','LISTING_BUY')
    AND status NOT IN ('FINALIZED','CHAIN_REVERTED');
CREATE UNIQUE INDEX IF NOT EXISTS uq_step_name    ON operation_step (operation_id, name);
CREATE INDEX IF NOT EXISTS ix_step_open ON operation_step (operation_id, step_index)
  WHERE status IN ('PENDING','SUBMITTED');
CREATE INDEX IF NOT EXISTS ix_reservation_by_op ON reservation (operation_id);
CREATE INDEX IF NOT EXISTS ix_opevent_by_op ON operation_event (operation_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_fee_effective ON fee_schedule (effective_from);
CREATE UNIQUE INDEX IF NOT EXISTS uq_ledger_seq ON ledger_entry (operation_id, entry_seq);
CREATE INDEX IF NOT EXISTS ix_ledger_account ON ledger_entry (account, account_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_purchase_listing ON purchase (listing_id)
  WHERE status <> 'FAILED';
CREATE INDEX IF NOT EXISTS ix_purchase_buyer ON purchase (buyer_kind, buyer_id, created_at);
CREATE INDEX IF NOT EXISTS ix_holding_holder ON coupon_holding (holder_kind, holder_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_psp_event ON psp_webhook (provider, provider_event_id);
CREATE INDEX IF NOT EXISTS ix_psp_unprocessed ON psp_webhook (received_at)
  WHERE processed_at IS NULL AND signature_ok;
CREATE UNIQUE INDEX IF NOT EXISTS uq_deposit_provider_ref ON thb_deposit (provider, provider_ref);
CREATE INDEX IF NOT EXISTS ix_deposit_holder ON thb_deposit (holder_kind, holder_id, created_at);
CREATE INDEX IF NOT EXISTS ix_deposit_mintable ON thb_deposit (confirmed_at) WHERE status = 'CONFIRMED';
CREATE UNIQUE INDEX IF NOT EXISTS uq_mint_inflight
  ON operation (subject_id)
  WHERE kind = 'THB_MINT' AND status NOT IN ('FINALIZED','CHAIN_REVERTED');
CREATE UNIQUE INDEX IF NOT EXISTS uq_mint_operation ON thb_mint (operation_id);
CREATE INDEX IF NOT EXISTS ix_ledger_issuance ON ledger_entry (created_at) WHERE account = 'ISSUANCE';
CREATE UNIQUE INDEX IF NOT EXISTS uq_reversal_deposit ON deposit_reversal (deposit_id);
CREATE INDEX IF NOT EXISTS ix_reversal_review ON deposit_reversal (created_at)
  WHERE outcome = 'MANUAL_REVIEW';
CREATE INDEX IF NOT EXISTS ix_operation_actor ON operation (actor_scope, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_opevent_seq ON operation_event (operation_id, seq);
CREATE INDEX IF NOT EXISTS ix_opevent_by_op_seq ON operation_event (operation_id, seq);
CREATE INDEX IF NOT EXISTS ix_endpoint_merchant ON webhook_endpoint (merchant_id)
  WHERE status = 'ACTIVE';
CREATE UNIQUE INDEX IF NOT EXISTS uq_delivery_event
  ON webhook_delivery (endpoint_id, operation_id, event_seq)
  WHERE replay_of IS NULL;
CREATE INDEX IF NOT EXISTS ix_delivery_replay ON webhook_delivery (replay_of)
  WHERE replay_of IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_delivery_seq
  ON webhook_delivery (endpoint_id, operation_id, delivery_seq);
CREATE INDEX IF NOT EXISTS ix_delivery_due ON webhook_delivery (next_attempt_at)
  WHERE status = 'PENDING';
CREATE INDEX IF NOT EXISTS ix_delivery_dead ON webhook_delivery (dead_at) WHERE status = 'DEAD';
CREATE UNIQUE INDEX IF NOT EXISTS uq_attempt_seq ON webhook_delivery_attempt (delivery_id, attempt);
CREATE UNIQUE INDEX IF NOT EXISTS uq_finality_effective ON finality_config (effective_from);
CREATE UNIQUE INDEX IF NOT EXISTS uq_block_live ON raw_block (block_number)
  WHERE orphaned_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_block_number ON raw_block (block_number);
CREATE INDEX IF NOT EXISTS ix_block_orphaned ON raw_block (orphaned_at)
  WHERE orphaned_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_event_unapplied ON chain_event (block_number, log_index)
  WHERE applied_at IS NULL AND orphaned_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_event_by_block ON chain_event (block_number);
CREATE INDEX IF NOT EXISTS ix_event_not_final ON chain_event (block_number)
  WHERE finalized_at IS NULL AND orphaned_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_reorg_review ON reorg_log (detected_at)
  WHERE outcome = 'MANUAL_REVIEW';

-- ------------------------------------------------------- 6 · trigger
DO $do$ BEGIN
CREATE TRIGGER trg_challenge_transition
  BEFORE UPDATE ON otp_challenge
  FOR EACH ROW EXECUTE FUNCTION f_challenge_transition();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE CONSTRAINT TRIGGER trg_attempt_count_matches
  AFTER INSERT ON otp_attempt
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION f_attempt_count_matches();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_throttle_check
  BEFORE INSERT ON otp_challenge
  FOR EACH ROW EXECUTE FUNCTION f_throttle_check();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_customer_phone_from_challenge
  BEFORE INSERT ON customer
  FOR EACH ROW EXECUTE FUNCTION f_customer_phone_from_challenge();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_customer_phone_immutable
  BEFORE UPDATE ON customer
  FOR EACH ROW EXECUTE FUNCTION f_customer_phone_immutable();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_point_chain_immutable
  BEFORE UPDATE ON point_type_chain
  FOR EACH ROW EXECUTE FUNCTION f_point_chain_immutable();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_point_type_merchant_active
  BEFORE INSERT ON point_type
  FOR EACH ROW EXECUTE FUNCTION f_merchant_must_be_active();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_point_reservation_balance
  BEFORE INSERT ON reservation
  FOR EACH ROW EXECUTE FUNCTION f_check_point_reservation();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_reservation_release_timing
  BEFORE UPDATE ON reservation
  FOR EACH ROW EXECUTE FUNCTION f_check_release_timing();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_watermark_monotonic
  BEFORE UPDATE ON projection_watermark
  FOR EACH ROW EXECUTE FUNCTION f_watermark_monotonic();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_notice_not_expired
  BEFORE INSERT ON expiry_notice
  FOR EACH ROW EXECUTE FUNCTION f_notice_not_expired();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_coupon_type_merchant_active
  BEFORE INSERT ON coupon_type
  FOR EACH ROW EXECUTE FUNCTION f_merchant_must_be_active();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_coupon_chain_immutable
  BEFORE UPDATE ON coupon_type_chain
  FOR EACH ROW EXECUTE FUNCTION f_coupon_chain_immutable();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_code_ref_derived
  BEFORE INSERT OR UPDATE ON coupon_code
  FOR EACH ROW EXECUTE FUNCTION f_code_ref_derived();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_issue_within_supply
  BEFORE INSERT ON coupon_code
  FOR EACH ROW EXECUTE FUNCTION f_issue_within_supply();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_listing_chain_immutable
  BEFORE UPDATE ON listing_chain
  FOR EACH ROW EXECUTE FUNCTION f_listing_chain_immutable();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_listing_item_valid
  BEFORE INSERT ON listing_item
  FOR EACH ROW EXECUTE FUNCTION f_listing_item_valid();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_listing_quantity_match
  AFTER INSERT ON listing_item
  FOR EACH ROW EXECUTE FUNCTION f_listing_quantity_match();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_listing_status_transition
  BEFORE UPDATE ON listing
  FOR EACH ROW EXECUTE FUNCTION f_listing_status_transition();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_delist_admissible
  BEFORE INSERT ON operation
  FOR EACH ROW EXECUTE FUNCTION f_delist_admissible();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_step_order_gate
  BEFORE UPDATE ON operation_step
  FOR EACH ROW EXECUTE FUNCTION f_step_order_gate();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_coupon_hold_until_closed
  BEFORE UPDATE ON reservation
  FOR EACH ROW EXECUTE FUNCTION f_coupon_hold_until_closed();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_listing_price_immutable
  BEFORE UPDATE ON listing
  FOR EACH ROW EXECUTE FUNCTION f_listing_price_immutable();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_purchase_math
  BEFORE INSERT OR UPDATE ON purchase
  FOR EACH ROW EXECUTE FUNCTION f_purchase_math();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_buy_admissible
  BEFORE INSERT ON operation
  FOR EACH ROW EXECUTE FUNCTION f_buy_admissible();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_thb_reservation_balance
  BEFORE INSERT ON reservation
  FOR EACH ROW EXECUTE FUNCTION f_thb_reservation_balance();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE CONSTRAINT TRIGGER trg_ledger_balanced
  AFTER INSERT OR UPDATE ON ledger_entry
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION f_ledger_balanced();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE CONSTRAINT TRIGGER trg_balance_matches_ledger
  AFTER INSERT OR UPDATE ON thb_balance
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION f_balance_matches_ledger();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_psp_webhook_immutable
  BEFORE UPDATE ON psp_webhook
  FOR EACH ROW EXECUTE FUNCTION f_psp_webhook_immutable();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_deposit_amount_exact
  BEFORE INSERT OR UPDATE ON thb_deposit
  FOR EACH ROW EXECUTE FUNCTION f_deposit_amount_exact();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_deposit_status_transition
  BEFORE UPDATE ON thb_deposit
  FOR EACH ROW EXECUTE FUNCTION f_deposit_status_transition();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_mint_requires_confirmed_deposit
  BEFORE INSERT ON thb_mint
  FOR EACH ROW EXECUTE FUNCTION f_mint_requires_confirmed_deposit();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_issuance_only_from_mint
  BEFORE INSERT OR UPDATE ON ledger_entry
  FOR EACH ROW EXECUTE FUNCTION f_issuance_only_from_mint();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_reversal_outcome_matches_balance
  BEFORE INSERT ON deposit_reversal
  FOR EACH ROW EXECUTE FUNCTION f_reversal_outcome_matches_balance();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_operation_status_transition
  BEFORE UPDATE ON operation
  FOR EACH ROW EXECUTE FUNCTION f_operation_status_transition();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_opevent_chain_intact
  BEFORE INSERT ON operation_event
  FOR EACH ROW EXECUTE FUNCTION f_opevent_chain_intact();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE CONSTRAINT TRIGGER trg_status_has_event
  AFTER INSERT OR UPDATE ON operation
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION f_status_has_event();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_endpoint_no_plain_secret
  BEFORE INSERT OR UPDATE ON webhook_endpoint
  FOR EACH ROW EXECUTE FUNCTION f_endpoint_no_plain_secret();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_delivery_terminal_immutable
  BEFORE UPDATE ON webhook_delivery
  FOR EACH ROW EXECUTE FUNCTION f_delivery_terminal_immutable();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_delivery_order_gate
  BEFORE UPDATE ON webhook_delivery
  FOR EACH ROW EXECUTE FUNCTION f_delivery_order_gate();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_delivery_event_exists
  BEFORE INSERT ON webhook_delivery
  FOR EACH ROW EXECUTE FUNCTION f_delivery_event_exists();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_replay_valid
  BEFORE INSERT ON webhook_delivery
  FOR EACH ROW EXECUTE FUNCTION f_replay_valid();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_cursor_needs_lease
  BEFORE UPDATE ON indexer_cursor
  FOR EACH ROW EXECUTE FUNCTION f_cursor_needs_lease();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_block_links_to_parent
  BEFORE INSERT ON raw_block
  FOR EACH ROW EXECUTE FUNCTION f_block_links_to_parent();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE CONSTRAINT TRIGGER trg_event_within_cursor
  AFTER INSERT OR UPDATE ON chain_event
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION f_event_within_cursor();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_final_not_orphanable
  BEFORE UPDATE ON chain_event
  FOR EACH ROW EXECUTE FUNCTION f_final_not_orphanable();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;
DO $do$ BEGIN
CREATE TRIGGER trg_reorg_outcome_matches_depth
  BEFORE INSERT ON reorg_log
  FOR EACH ROW EXECUTE FUNCTION f_reorg_outcome_matches_depth();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;

-- ----------------------------------------- 7 · event trigger ท้ายสุด
DO $do$ BEGIN
CREATE EVENT TRIGGER trg_no_secret_columns
  ON ddl_command_end
  WHEN TAG IN ('ALTER TABLE','CREATE TABLE')
  EXECUTE FUNCTION f_no_secret_columns();
EXCEPTION WHEN duplicate_object THEN NULL;
END $do$;

COMMIT;
