-- =====================================================================
-- FEAT-02 · Customer Register + OTP · acceptance สำหรับ blockchain-reward-platform-be
--
-- ต้นฉบับ : blockchain-reward-platform/reference/feat-02/tests/acceptance.sql (39 ข้อ)
-- schema  : migrations 0002 → 0004_feat02_customer_otp (แผน C)
--
-- รัน (Postgres ใน docker):
--   docker exec -i blockchain-reward-platform-be-postgres-1 \
--     psql -U reward -d reward < tests/sql/feat02_acceptance.sql
--
-- แผน C แบ่งกฎตามชนิด · ผลจึงมีห้าแบบ
--   PASS   ฐานข้อมูลบังคับกฎนี้ได้จริง
--   FAIL   ฐานข้อมูลยอมให้ทำสิ่งที่ RFD ห้าม หรือถูกปฏิเสธด้วยกฎตัวอื่น
--   ERROR  test เองรันไม่ได้ · ดูหมายเหตุ
--   APP    กฎนี้อยู่ในโค้ด Python ไม่ใช่ฐานข้อมูล · พิสูจน์ด้วย pytest (รอบสอง)
--   DEFER  ยังไม่ได้ตัดสินใจว่าจะบังคับ (event trigger กันคอลัมน์ความลับ)
--
-- ต่างจากต้นฉบับ
--   1. hash/address เป็น hex string ('0x…') ตาม 0002 · ไม่ต้องใช้ pgcrypto
--   2. F7 บังคับด้วย customer.verified_challenge_id + FK คู่ (id, phone_e164)
--      แทน current_setting('app.verified_challenge') ของต้นฉบับ
--   3. trigger ไม่เติม verified_at / consumed_at ให้ · โค้ดต้องใส่เอง
--      เพราะ SQLite (ที่ pytest ใช้) ไม่มี trigger ชุดนี้ และ CHECK บังคับอยู่แล้ว
--   4. ข้อ "ต้องถูกปฏิเสธ" ตรวจว่าชนกฎที่ตั้งใจทดสอบจริง และย้อนแถวทิ้งถ้าไม่ถูกห้าม
--   5. ข้อมูลทดสอบขึ้นต้นด้วย acc_ / เบอร์ +6681999xxxx เพื่อไม่ชนข้อมูลจริง
--
-- ทั้งไฟล์อยู่ใน transaction เดียวแล้ว ROLLBACK · รันซ้ำกี่รอบก็ได้
-- =====================================================================
\set ON_ERROR_STOP on
\pset format aligned

BEGIN;

-- ควรได้ 0004_feat02_customer_otp ขึ้นไป
SELECT version_num AS "alembic revision" FROM alembic_version;

\o /dev/null

CREATE TEMP TABLE result (id text, what text, verdict text, note text);

CREATE FUNCTION pg_temp.rec(p_id text, p_what text, p_verdict text, p_note text DEFAULT NULL)
RETURNS void LANGUAGE sql AS
$$ INSERT INTO result VALUES (p_id, p_what, p_verdict, p_note) $$;

-- คอลัมน์ที่ constraint หรือ unique index ชื่อนี้ครอบอยู่
CREATE FUNCTION pg_temp.cols_of(p_table text, p_name text)
RETURNS text[] LANGUAGE sql STABLE AS $$
  SELECT coalesce(
    (SELECT array_agg(a.attname::text ORDER BY a.attname)
       FROM pg_constraint c
       JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
      WHERE c.conrelid = to_regclass(p_table) AND c.conname = p_name),
    (SELECT array_agg(a.attname::text ORDER BY a.attname)
       FROM pg_index i
       JOIN pg_class ci    ON ci.oid = i.indexrelid
       JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey)
      WHERE i.indrelid = to_regclass(p_table) AND ci.relname = p_name),
    '{}'::text[])
$$;

-- รัน p_sql แล้วคาดว่าต้องถูกปฏิเสธ · คืน NULL ถ้าผ่าน · 'FAIL: …' / 'ERROR: …' ถ้าไม่
--   p_state : 23505 unique · 23514 check · 23503 foreign key · 23502 not null
--   p_cons  : ถ้าให้มา ชื่อ constraint/trigger ที่ปฏิเสธต้องเป็นหนึ่งในนี้
--             (trigger แจ้งชื่อตัวเองผ่าน RAISE … CONSTRAINT = '…')
--   ไม่งั้น  : ต้องอยู่บน p_table และครอบคอลัมน์ p_cols
--             (23502 ไม่มีชื่อ constraint · ใช้ชื่อคอลัมน์แทน)
CREATE FUNCTION pg_temp.try_reject(p_sql text, p_state text, p_table text,
                                   p_cols text[], p_cons text[] DEFAULT NULL)
RETURNS text LANGUAGE plpgsql AS $$
DECLARE
  v_state text; v_con text; v_tab text; v_col text; v_msg text; v_cols text[];
BEGIN
  BEGIN
    EXECUTE p_sql;
    RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'ACC_NOT_REJECTED';
  EXCEPTION WHEN OTHERS THEN
    GET STACKED DIAGNOSTICS v_state = RETURNED_SQLSTATE,
                            v_con   = CONSTRAINT_NAME,
                            v_tab   = TABLE_NAME,
                            v_col   = COLUMN_NAME,
                            v_msg   = MESSAGE_TEXT;
  END;

  IF v_msg = 'ACC_NOT_REJECTED' THEN
    RETURN 'FAIL: ฐานข้อมูลยอมให้ทำ · ไม่มีกฎที่ห้าม';
  END IF;
  IF v_state <> p_state THEN
    RETURN format('ERROR: คาด SQLSTATE %s แต่ได้ %s · %s', p_state, v_state, v_msg);
  END IF;

  IF p_cons IS NOT NULL THEN
    IF v_con = ANY (p_cons) THEN RETURN NULL; END IF;
    RETURN format('FAIL: ถูกปฏิเสธด้วย %s ไม่ใช่ %s · %s',
                  coalesce(nullif(v_con, ''), '?'), array_to_string(p_cons, '/'), v_msg);
  END IF;

  IF p_state = '23502' THEN
    IF v_tab = p_table AND v_col = p_cols[1] THEN RETURN NULL; END IF;
    RETURN format('FAIL: NOT NULL ของ %s.%s ไม่ใช่ %s.%s', v_tab, v_col, p_table, p_cols[1]);
  END IF;

  v_cols := pg_temp.cols_of(v_tab, v_con);
  IF v_tab = p_table AND p_cols <@ v_cols THEN RETURN NULL; END IF;
  RETURN format('FAIL: ถูกปฏิเสธด้วย %s.%s (%s) ซึ่งไม่ใช่กฎที่ทดสอบ',
                v_tab, v_con, array_to_string(v_cols, ','));
END $$;

-- รวมผลย่อย · ERROR ชนะ FAIL ชนะ PASS
CREATE FUNCTION pg_temp.rec_checks(p_id text, p_what text, VARIADIC p_results text[])
RETURNS void LANGUAGE plpgsql AS $$
DECLARE v_bad text[]; v_err boolean;
BEGIN
  SELECT array_agg(r), bool_or(r LIKE 'ERROR:%') INTO v_bad, v_err
    FROM unnest(p_results) r WHERE r IS NOT NULL;
  IF v_bad IS NULL THEN
    PERFORM pg_temp.rec(p_id, p_what, 'PASS');
  ELSE
    PERFORM pg_temp.rec(p_id, p_what, CASE WHEN v_err THEN 'ERROR' ELSE 'FAIL' END,
      (SELECT string_agg(regexp_replace(b, '^(FAIL|ERROR): ', ''), ' | ') FROM unnest(v_bad) b));
  END IF;
END $$;

-- hash ของ OTP · ของจริงคือ HMAC-SHA256(pepper จาก KMS, challenge_id || ':' || code)
-- ที่นี่ต้องการแค่รูปแบบ (32 byte เป็น hex) · การเทียบรหัสอยู่ในโค้ด (APP)
CREATE FUNCTION pg_temp.code_hash(p_ch text, p_code text) RETURNS text
LANGUAGE sql IMMUTABLE AS
$$ SELECT '0x' || encode(sha256(convert_to(p_ch || ':' || p_code, 'UTF8')), 'hex') $$;

-- สร้าง challenge PENDING
CREATE FUNCTION pg_temp.mk_challenge(p_id text, p_phone text, p_code text)
RETURNS void LANGUAGE sql AS $$
  INSERT INTO otp_challenge (id, phone_e164, code_hash, pepper_ref, expires_at)
  VALUES (p_id, p_phone, pg_temp.code_hash(p_id, p_code), 'kms://otp-pepper/v1',
          now() + interval '5 minutes')
$$;

-- ยืนยัน challenge แบบที่โค้ดจะทำ · status กับ verified_at ต้องไปด้วยกัน (ck_ch_verified)
CREATE FUNCTION pg_temp.verify(p_id text) RETURNS void LANGUAGE sql AS $$
  UPDATE otp_challenge SET status = 'VERIFIED', verified_at = now(), attempts = attempts + 1
   WHERE id = p_id
$$;

-- กฎที่อยู่ในโค้ด
CREATE FUNCTION pg_temp.app(p_id text, p_what text, p_where text) RETURNS void
LANGUAGE sql AS $$ SELECT pg_temp.rec(p_id, p_what, 'APP', p_where) $$;

-- =====================================================================
-- T1 · เบอร์สามรูปที่คนพิมพ์จริง ได้ค่าเดียวกัน                     APP
-- =====================================================================
SELECT pg_temp.app('T1', 'เบอร์สามรูป (0xx · +66xx · 66xx) และมีขีด → ค่าเดียวกัน',
  'normalize_phone() ใน Python · ฐานข้อมูลรับเฉพาะ E.164 (T2 T3)');

-- =====================================================================
-- T2 · เบอร์รูปที่ไม่รู้จัก → CHECK ปฏิเสธ
--      ต้นฉบับส่ง f_normalize_phone('12345') ซึ่งคืน '12345' · ที่นี่ส่งค่านั้นตรงๆ
-- =====================================================================
SELECT pg_temp.rec_checks('T2', 'เบอร์รูปที่ไม่รู้จัก → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO otp_challenge (id, phone_e164, code_hash, pepper_ref, expires_at)
    VALUES ('acc_ch_bad', '12345', pg_temp.code_hash('acc_ch_bad', '1'),
            'kms://otp-pepper/v1', now() + interval '5 minutes')
  $q$, '23514', 'otp_challenge', ARRAY['phone_e164']));

-- =====================================================================
-- T3 · เบอร์ที่ยังไม่ normalize → ถูกปฏิเสธ · เก็บรูปเดียวเท่านั้น
-- =====================================================================
SELECT pg_temp.rec_checks('T3', 'เบอร์ที่ยังไม่ normalize → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO otp_challenge (id, phone_e164, code_hash, pepper_ref, expires_at)
    VALUES ('acc_ch_raw', '0819990001', pg_temp.code_hash('acc_ch_raw', '1'),
            'kms://otp-pepper/v1', now() + interval '5 minutes')
  $q$, '23514', 'otp_challenge', ARRAY['phone_e164']),
  pg_temp.try_reject($q$
    INSERT INTO phone_throttle (phone_e164) VALUES ('0819990001')
  $q$, '23514', 'phone_throttle', ARRAY['phone_e164']));

-- =====================================================================
-- T4 · F6 · ฐานข้อมูลไม่มีคอลัมน์ไหนที่เก็บ OTP ได้
-- =====================================================================
DO $$
DECLARE v_bad text; v_has boolean;
BEGIN
  SELECT string_agg(column_name::text, ',') INTO v_bad FROM information_schema.columns
   WHERE table_schema = 'public' AND table_name = 'otp_challenge'
     AND column_name ~* '(^code$|otp|plain|clear)' AND column_name <> 'code_hash';
  SELECT count(*) = 1 INTO v_has FROM information_schema.columns
   WHERE table_schema = 'public' AND table_name = 'otp_challenge' AND column_name = 'code_hash';
  PERFORM pg_temp.rec('T4', 'F6 · otp_challenge ไม่มีคอลัมน์ที่เก็บ OTP ได้ · มีแต่ code_hash',
    CASE WHEN v_bad IS NULL AND v_has THEN 'PASS' ELSE 'FAIL' END,
    CASE WHEN v_bad IS NOT NULL THEN 'คอลัมน์ต้องสงสัย: ' || v_bad END);
END $$;

-- =====================================================================
-- T5 · code_hash ต้องเป็น 32 byte · pepper_ref ต้องเป็น kms:// ไม่ใช่ค่าดิบ
-- =====================================================================
SELECT pg_temp.rec_checks('T5', 'code_hash ต้อง 32 byte · pepper_ref ต้องเป็น kms:// ไม่ใช่ค่าดิบ',
  pg_temp.try_reject($q$
    INSERT INTO otp_challenge (id, phone_e164, code_hash, pepper_ref, expires_at)
    VALUES ('acc_ch_short', '+66819990009', '0xdeadbeef', 'kms://otp-pepper/v1',
            now() + interval '5 minutes')
  $q$, '23514', 'otp_challenge', ARRAY['code_hash']),
  pg_temp.try_reject($q$
    INSERT INTO otp_challenge (id, phone_e164, code_hash, pepper_ref, expires_at)
    VALUES ('acc_ch_nopep', '+66819990009', pg_temp.code_hash('acc_ch_nopep', '1'),
            'my-secret-pepper-value', now() + interval '5 minutes')
  $q$, '23514', 'otp_challenge', ARRAY['pepper_ref']));

-- =====================================================================
-- T6 · L6 · ตาราง wallet ไม่มีคอลัมน์ที่เก็บ key ได้ · มีแต่ kms_key_ref
-- =====================================================================
DO $$
DECLARE v_bad text; v_has boolean;
BEGIN
  SELECT string_agg(column_name::text, ',') INTO v_bad FROM information_schema.columns
   WHERE table_schema = 'public' AND table_name = 'wallet'
     AND column_name ~* '(private|secret|mnemonic|seed|keystore|encrypted)';
  SELECT count(*) = 1 INTO v_has FROM information_schema.columns
   WHERE table_schema = 'public' AND table_name = 'wallet' AND column_name = 'kms_key_ref';
  PERFORM pg_temp.rec('T6', 'L6 · wallet ไม่มีคอลัมน์เก็บ key · มีแต่ kms_key_ref',
    CASE WHEN v_bad IS NULL AND v_has THEN 'PASS' ELSE 'FAIL' END,
    CASE WHEN v_bad IS NOT NULL THEN 'คอลัมน์ต้องสงสัย: ' || v_bad END);
END $$;

-- =====================================================================
-- T7–T10 · T12 · event trigger กันคอลัมน์ชื่อเข้าข่ายความลับที่ชั้น DDL    DEFER
--      ต้องสร้างด้วยสิทธิ์ superuser ทุก environment · ยังไม่ได้ตัดสินใจ
-- =====================================================================
INSERT INTO result VALUES
  ('T7',  'L6 · ALTER TABLE เพิ่ม private_key → ถูกปฏิเสธที่ชั้น DDL', 'DEFER', 'event trigger · รอตัดสินใจ'),
  ('T8',  'L6 · encrypted_key · mnemonic · sk · seed_phrase ถูกปฏิเสธทั้งหมด', 'DEFER', 'event trigger · รอตัดสินใจ'),
  ('T9',  'L6 · CREATE TABLE ที่มี privkey → ถูกปฏิเสธ', 'DEFER', 'event trigger · รอตัดสินใจ'),
  ('T10', 'คอลัมน์ชื่อธรรมดา (label) เพิ่มได้ปกติ · กฎไม่กว้างเกิน', 'DEFER', 'ทดสอบ event trigger · ไม่มีความหมายถ้ายังไม่มี'),
  ('T12', 'event trigger มีอยู่จริงและเปิดใช้งาน', 'DEFER', 'event trigger · รอตัดสินใจ');

-- =====================================================================
-- T11 · kms_key_ref ที่เป็นค่าความลับเอง → ถูกปฏิเสธ
-- =====================================================================
SELECT pg_temp.rec_checks('T11', 'kms_key_ref ที่เป็นค่าดิบ → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO wallet (id, address, key_ref, owner_kind, owner_id, kms_key_ref)
    VALUES ('acc_wal_raw', '0x' || repeat('11', 20), 'kms/acc', 'CUSTOMER', 'acc_cus_x',
            '0x4c0883a69102937d6231471b5dbb6204fe512961708279f2f3b1d3a4b9c7e8d1')
  $q$, '23514', 'wallet', ARRAY['kms_key_ref']));

-- =====================================================================
-- T13 · T15 · T16 · จำกัดการขอ OTP (cooldown · โควตาต่อชั่วโมง · เบอร์ที่ล็อก)   APP
-- =====================================================================
SELECT pg_temp.app('T13', 'ขอ OTP ซ้ำภายใน cooldown → ถูกปฏิเสธ', 'request_otp() · phone_throttle');
SELECT pg_temp.app('T15', 'ขอ OTP ครบโควตาในหนึ่งชั่วโมง → ถูกปฏิเสธ', 'request_otp() · phone_throttle');
SELECT pg_temp.app('T16', 'เบอร์ที่ถูกล็อก → ขอ OTP ไม่ได้', 'request_otp() · phone_throttle.locked_until');

-- =====================================================================
-- T14 · หนึ่งเบอร์มี challenge PENDING ได้อันเดียว
--       ต้นฉบับนับแถว (พึ่ง T13 กันไว้ก่อน) · ที่นี่ชน uq_challenge_live ตรงๆ
-- =====================================================================
SELECT pg_temp.mk_challenge('acc_ch_1', '+66819990001', '123456');

SELECT pg_temp.rec_checks('T14', 'หนึ่งเบอร์มี challenge PENDING ได้อันเดียว',
  pg_temp.try_reject($q$
    SELECT pg_temp.mk_challenge('acc_ch_1b', '+66819990001', '999999')
  $q$, '23505', 'otp_challenge', ARRAY['phone_e164']));

-- =====================================================================
-- T17–T21 · T21b · T23 · ยืนยัน OTP (เทียบ hash · นับครั้ง · ล็อก · หมดอายุ)   APP
--       ต้องเกิดใน transaction เดียวด้วย SELECT … FOR UPDATE
-- =====================================================================
SELECT pg_temp.app('T17', 'ใส่รหัสผิด → WRONG_CODE · attempts เป็น 1', 'verify_otp()');
SELECT pg_temp.app('T18', 'ใส่รหัสถูก → OK · VERIFIED · verified_at ถูกเติม', 'verify_otp()');
SELECT pg_temp.app('T19', 'ยืนยันซ้ำหลัง VERIFIED → ALREADY_USED', 'verify_otp()');
SELECT pg_temp.app('T20', 'ใส่ผิดครบ 5 ครั้ง → LOCKED · เบอร์ถูกล็อกด้วย', 'verify_otp()');
SELECT pg_temp.app('T21', 'LOCKED แล้วใส่รหัสถูกก็ไม่ผ่าน → LOCKED', 'verify_otp()');
SELECT pg_temp.app('T21b', 'ยิงรัวตอนล็อกแล้ว · บันทึกครบแต่ attempts ยังเป็น 5', 'verify_otp()');
SELECT pg_temp.app('T23', 'challenge ที่หมดอายุ → EXPIRED · ใส่รหัสถูกก็ไม่ผ่าน', 'verify_otp()');

-- =====================================================================
-- T22 · attempts เกิน max_attempts → CHECK ปฏิเสธ
-- =====================================================================
SELECT pg_temp.mk_challenge('acc_ch_2', '+66819990002', '654321');

SELECT pg_temp.rec_checks('T22', 'attempts เกิน max_attempts → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    UPDATE otp_challenge SET attempts = 99 WHERE id = 'acc_ch_2'
  $q$, '23514', 'otp_challenge', ARRAY['attempts', 'max_attempts']));

-- =====================================================================
-- T24 · F7 · แก้เบอร์ใน challenge → ถูกปฏิเสธ
-- T25 · แก้ code_hash หรือ expires_at → ถูกปฏิเสธ · ยืดอายุ OTP ย้อนหลังไม่ได้
-- =====================================================================
SELECT pg_temp.verify('acc_ch_1');

SELECT pg_temp.rec_checks('T24', 'F7 · แก้เบอร์ใน challenge → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    UPDATE otp_challenge SET phone_e164 = '+66819990099' WHERE id = 'acc_ch_1'
  $q$, '23514', 'otp_challenge', NULL, ARRAY['trg_otp_challenge_guard']));

SELECT pg_temp.rec_checks('T25', 'แก้ code_hash หรือ expires_at → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    UPDATE otp_challenge SET code_hash = pg_temp.code_hash('acc_ch_1', 'new') WHERE id = 'acc_ch_1'
  $q$, '23514', 'otp_challenge', NULL, ARRAY['trg_otp_challenge_guard']),
  pg_temp.try_reject($q$
    UPDATE otp_challenge SET expires_at = now() + interval '1 day' WHERE id = 'acc_ch_1'
  $q$, '23514', 'otp_challenge', NULL, ARRAY['trg_otp_challenge_guard']));

-- =====================================================================
-- T26 · F7 · สร้างลูกค้าโดยไม่อ้าง challenge → ถูกปฏิเสธ
--       บน Postgres trigger (BEFORE INSERT) ปฏิเสธก่อนถึง NOT NULL
--       NOT NULL ของ verified_challenge_id คือตัวที่กันบน SQLite (pytest)
-- =====================================================================
SELECT pg_temp.rec_checks('T26', 'F7 · สร้างลูกค้าโดยไม่อ้าง challenge → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO customer (id, phone_e164, phone_verified_at)
    VALUES ('acc_cus_noch', '+66819990001', now())
  $q$, '23514', 'customer', NULL, ARRAY['trg_customer_from_verified_challenge']));

-- =====================================================================
-- T27 · F7 · อ้าง challenge ที่ถูก แต่ใส่เบอร์อื่น → ถูกปฏิเสธ
--       FK คู่ (verified_challenge_id, phone_e164) → otp_challenge (id, phone_e164)
-- =====================================================================
SELECT pg_temp.rec_checks('T27', 'F7 · เบอร์ไม่ตรงกับ challenge → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO customer (id, phone_e164, phone_verified_at, verified_challenge_id)
    VALUES ('acc_cus_wrong', '+66819990099', now(), 'acc_ch_1')
  $q$, '23503', 'customer', ARRAY['phone_e164', 'verified_challenge_id']));

-- =====================================================================
-- T28 · F7 · อ้าง challenge ที่ยังไม่ VERIFIED → ถูกปฏิเสธ
-- =====================================================================
SELECT pg_temp.mk_challenge('acc_ch_4', '+66819990004', '888888');

SELECT pg_temp.rec_checks('T28', 'F7 · challenge ที่ยังไม่ VERIFIED → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO customer (id, phone_e164, phone_verified_at, verified_challenge_id)
    VALUES ('acc_cus_unver', '+66819990004', now(), 'acc_ch_4')
  $q$, '23514', 'customer', NULL, ARRAY['trg_customer_from_verified_challenge']));

-- =====================================================================
-- T29 · สมัครด้วย challenge ที่ยืนยันแล้วและเบอร์ตรง → ผ่าน
--       แล้วปิด challenge เป็น CONSUMED (โค้ดใส่ consumed_at เอง)
-- =====================================================================
DO $$
DECLARE v text;
BEGIN
  INSERT INTO customer (id, phone_e164, phone_verified_at, verified_challenge_id)
  VALUES ('acc_cus_1', '+66819990001',
          (SELECT verified_at FROM otp_challenge WHERE id = 'acc_ch_1'), 'acc_ch_1');
  UPDATE otp_challenge SET status = 'CONSUMED', consumed_at = now() WHERE id = 'acc_ch_1';
  SELECT phone_e164 INTO v FROM customer WHERE id = 'acc_cus_1';
  PERFORM pg_temp.rec('T29', 'สมัครด้วย challenge ที่ถูก → ผ่าน · เบอร์มาจาก challenge',
                      CASE WHEN v = '+66819990001' THEN 'PASS' ELSE 'FAIL' END);
EXCEPTION WHEN OTHERS THEN
  PERFORM pg_temp.rec('T29', 'สมัครด้วย challenge ที่ถูก → ผ่าน · เบอร์มาจาก challenge',
                      'ERROR', SQLSTATE || ' · ' || SQLERRM);
END $$;

-- =====================================================================
-- T30 · challenge ที่ CONSUMED แล้ว ใช้สมัครอีกไม่ได้
--       ต้นฉบับยอมรับทั้ง check และ unique · ที่นี่ต้องชนกฎของ challenge
--       ไม่ใช่แค่ unique ของเบอร์ (ซึ่งจะ PASS ปลอม)
-- =====================================================================
SELECT pg_temp.rec_checks('T30', 'challenge ที่ CONSUMED แล้ว ใช้สมัครอีกไม่ได้',
  pg_temp.try_reject($q$
    INSERT INTO customer (id, phone_e164, phone_verified_at, verified_challenge_id)
    VALUES ('acc_cus_dup', '+66819990001', now(), 'acc_ch_1')
  $q$, '23514', 'customer', NULL,
       ARRAY['trg_customer_from_verified_challenge', 'uq_customer_challenge']));

-- =====================================================================
-- T31 · เปลี่ยนเบอร์ของลูกค้าตรงๆ → ถูกปฏิเสธ · ต้องยืนยันเบอร์ใหม่
-- =====================================================================
SELECT pg_temp.rec_checks('T31', 'เปลี่ยนเบอร์ลูกค้าตรงๆ → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    UPDATE customer SET phone_e164 = '+66819990099' WHERE id = 'acc_cus_1'
  $q$, '23514', 'customer', NULL, ARRAY['trg_customer_guard']));

-- =====================================================================
-- T32 · F12 · ตาราง customer ไม่มีคอลัมน์รหัสผ่าน
--       ยกเว้น tel_hash · มาจาก FEAT-03/05/06 (ค้นลูกค้าด้วย hash ของเบอร์)
--       ไม่ใช่รหัสผ่าน · regex ของต้นฉบับ (…|hash) กว้างเกินเมื่อรวม schema ทุก pack
-- =====================================================================
DO $$
DECLARE v_bad text;
BEGIN
  SELECT string_agg(column_name::text, ',') INTO v_bad FROM information_schema.columns
   WHERE table_schema = 'public' AND table_name = 'customer'
     AND column_name ~* '(password|passwd|pwd|bcrypt|hash)'
     AND column_name <> 'tel_hash';
  PERFORM pg_temp.rec('T32', 'F12 · customer ไม่มีคอลัมน์รหัสผ่าน',
    CASE WHEN v_bad IS NULL THEN 'PASS' ELSE 'FAIL' END,
    CASE WHEN v_bad IS NULL THEN 'ยกเว้น tel_hash (FEAT-03/05/06)'
         ELSE 'คอลัมน์ต้องสงสัย: ' || v_bad END);
END $$;

-- =====================================================================
-- T33 · F12 · ยืนยันแล้วได้ session ทันที
--       ที่ฐานข้อมูลพิสูจน์ได้แค่ว่าออก session ให้ลูกค้าได้ · การออกจริงอยู่ในโค้ด
-- =====================================================================
DO $$
DECLARE v_n int;
BEGIN
  INSERT INTO customer_session (id, customer_id, token_hash, expires_at)
  VALUES ('acc_ses_1', 'acc_cus_1', '0x' || encode(sha256('the-token'::bytea), 'hex'),
          now() + interval '30 days');
  SELECT count(*)::int INTO v_n FROM customer_session
   WHERE customer_id = 'acc_cus_1' AND revoked_at IS NULL;
  PERFORM pg_temp.rec('T33', 'F12 · ยืนยันแล้วได้ session ทันที',
                      CASE WHEN v_n = 1 THEN 'PASS' ELSE 'FAIL' END,
                      'ตาราง · การออก session หลังยืนยันอยู่ใน verify_and_register() (APP)');
EXCEPTION WHEN OTHERS THEN
  PERFORM pg_temp.rec('T33', 'F12 · ยืนยันแล้วได้ session ทันที', 'ERROR', SQLSTATE || ' · ' || SQLERRM);
END $$;

-- =====================================================================
-- T34 · session เก็บแต่ hash ของ token · ไม่เก็บ token
-- =====================================================================
DO $$
DECLARE v_has boolean; v_bad text;
BEGIN
  SELECT count(*) = 1 INTO v_has FROM information_schema.columns
   WHERE table_schema = 'public' AND table_name = 'customer_session' AND column_name = 'token_hash';
  SELECT string_agg(column_name::text, ',') INTO v_bad FROM information_schema.columns
   WHERE table_schema = 'public' AND table_name = 'customer_session'
     AND column_name ~* '(^token$|refresh_token|access_token|jwt)';
  PERFORM pg_temp.rec('T34', 'session เก็บแต่ token_hash · ไม่มีคอลัมน์เก็บ token',
    CASE WHEN v_has AND v_bad IS NULL THEN 'PASS' ELSE 'FAIL' END,
    CASE WHEN v_bad IS NOT NULL THEN 'คอลัมน์ต้องสงสัย: ' || v_bad END);
END $$;

-- =====================================================================
-- T35 · wallet หนึ่งใบต่อเจ้าของหนึ่งคน
-- =====================================================================
INSERT INTO wallet (id, address, key_ref, owner_kind, owner_id, kms_key_ref)
VALUES ('acc_wal_1', '0x' || repeat('22', 20), 'kms/acc-c1', 'CUSTOMER', 'acc_cus_1',
        'kms://wallet/acc_cus_1/v1');

SELECT pg_temp.rec_checks('T35', 'wallet ซ้ำต่อเจ้าของเดียว → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO wallet (id, address, key_ref, owner_kind, owner_id, kms_key_ref)
    VALUES ('acc_wal_2', '0x' || repeat('33', 20), 'kms/acc-c1b', 'CUSTOMER', 'acc_cus_1',
            'kms://wallet/acc_cus_1/v2')
  $q$, '23505', 'wallet', ARRAY['owner_id', 'owner_kind']));

-- =====================================================================
-- T36 · งาน provision ค้างได้ทีละงานเดียวต่อลูกค้า
-- =====================================================================
INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
VALUES ('acc_op_p1', 'system:provision', 'CUSTOMER_PROVISION', 'acc_cus_1', '{}');

SELECT pg_temp.rec_checks('T36', 'สองงาน provision บนลูกค้าเดียว → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
    VALUES ('acc_op_p2', 'system:provision', 'CUSTOMER_PROVISION', 'acc_cus_1', '{}')
  $q$, '23505', 'operation', ARRAY['kind', 'subject_id']));

-- =====================================================================
-- T37 · attempts นับไม่ตรงกับแถวจริง → ไม่ผ่าน                          APP
-- =====================================================================
SELECT pg_temp.app('T37', 'attempts นับไม่ตรงกับแถวจริง → ไม่ผ่าน',
  'verify_otp() เพิ่ม attempts กับ otp_attempt ใน transaction เดียว');

-- =====================================================================
-- T38 · ปิดท้าย · ไม่มีคอลัมน์ไหนในฐานข้อมูลที่ชื่อเข้าข่ายความลับ
-- =====================================================================
DO $$
DECLARE v_bad text;
BEGIN
  SELECT string_agg(table_name || '.' || column_name, ', ') INTO v_bad
    FROM information_schema.columns
   WHERE table_schema = 'public'
     AND (column_name ~* '(private|privkey|priv_key|mnemonic|seed_phrase|keystore|encrypted_key|enc_key)'
          OR column_name ~* '^(sk|xprv)$');
  PERFORM pg_temp.rec('T38', 'ปิดท้าย · ไม่มีคอลัมน์ชื่อเข้าข่ายความลับทั้งฐานข้อมูล',
    CASE WHEN v_bad IS NULL THEN 'PASS' ELSE 'FAIL' END,
    CASE WHEN v_bad IS NOT NULL THEN 'คอลัมน์ต้องสงสัย: ' || v_bad END);
END $$;

-- ------------------------------------------------------------- สรุป
\o
SELECT id AS "#", what AS "ข้อรับประกัน", verdict AS "ผล", coalesce(note, '') AS "หมายเหตุ"
FROM result
ORDER BY lpad(substring(id FROM '[0-9]+'), 3, '0'), id;

SELECT count(*) FILTER (WHERE verdict = 'PASS')  AS "ผ่าน",
       count(*) FILTER (WHERE verdict = 'FAIL')  AS "ไม่ผ่าน",
       count(*) FILTER (WHERE verdict = 'ERROR') AS "test รันไม่ได้",
       count(*) FILTER (WHERE verdict = 'APP')   AS "อยู่ในโค้ด",
       count(*) FILTER (WHERE verdict = 'DEFER') AS "รอตัดสินใจ",
       count(*)                                  AS "รวม"
FROM result;

-- ไม่ commit · ฐานข้อมูลกลับไปเหมือนก่อนรัน
ROLLBACK;
