-- =====================================================================
-- FEAT-06 · Point Balance · bucket expiry · acceptance สำหรับ blockchain-reward-platform-be
--
-- ต้นฉบับ : blockchain-reward-platform/reference/feat-06/tests/acceptance.sql (20 ข้อ)
-- schema  : migrations 0002 → 0009_feat06_balance_reads
--
-- รัน (Postgres ใน docker):
--   docker exec -i blockchain-reward-platform-be-postgres-1 \
--     psql -U reward -d reward < tests/sql/feat06_acceptance.sql
--
-- ผล  PASS ฐานข้อมูลบังคับกฎนี้ได้จริง · FAIL ไม่ได้ หรือถูกปฏิเสธด้วยกฎตัวอื่น
--     ERROR test เองรันไม่ได้ (เช่น ฟังก์ชันที่ต้องใช้ยังไม่มี) · ดูหมายเหตุ
--
-- ต่างจากต้นฉบับ · หลักฐานอยู่ใน docs/reference-findings.md
--   1. ยอดเป็นล็อตของ NewPointToken v3 (migration 0008) ไม่ใช่ bucket ตาม epoch
--      bucket ห้าก้อนของต้นฉบับกลายเป็นห้าล็อตที่วันเดียวกัน · ตัวเลขทุกข้อเท่าเดิม
--   2. T5 ต้นฉบับไม่ได้ทดสอบกรณีติดลบจริง (ได้ 50) · ที่นี่ทดสอบทั้ง 50 และกรณีที่ต้องได้ 0
--   2b. T0 เป็นต้นวันของวันที่รัน ไม่ใช่วันตายตัว · trigger จองยอดตรวจกับเวลาจริง
--   3. T9b เพิ่ม · ที่ expireDate พอดี ล็อต D นับว่าหมดอายุแล้ว ล็อต B ยัง (ตาม contract)
--   4. expiry_notice อ้างยอดของผู้ถือในล็อต (point_type_id, holder, lot_key) แทน bucket_id
--   5. T16 · indexer_cursor มีแถวเดียว id = 'main' (FEAT-14) ไม่ใช่แถวต่อ contract
--   6. T18 ยกเว้น deposit_reversal.available_at_reversal (FEAT-12) · เป็นค่าที่บันทึก
--      ณ ตอนตีกลับ ไม่ใช่ยอดคงเหลือที่ cache ไว้ · regex ต้นฉบับกว้างเกินเมื่อรวมทุก pack
--   7. T19 ตรวจฟังก์ชันอ่านทุกตัวของล็อต · T20 ตรวจว่าอ่าน point_lot_balance ผ่าน index (ไม่ผูกชื่อ)
--   8. watermark และ cursor ตั้งค่าสัมพัทธ์กับค่าที่มีอยู่ · ไม่ถอยค่าจริงของฐานข้อมูล
--   9. operation ของ fixture เป็น POINT_TRANSFER (เดิม LISTING_BUY · 0013 ตรวจว่า LISTING_BUY อ้าง listing จริง)
--  10. ข้อมูลทดสอบขึ้นต้นด้วย acc_ · ข้อ "ต้องถูกปฏิเสธ" ตรวจว่าชนกฎที่ตั้งใจจริง
--
-- ทั้งไฟล์อยู่ใน transaction เดียวแล้ว ROLLBACK · รันซ้ำกี่รอบก็ได้
-- =====================================================================
\set ON_ERROR_STOP on
\pset format aligned

BEGIN;

-- ควรได้ 0009_feat06_balance_reads ขึ้นไป
SELECT version_num AS "alembic revision" FROM alembic_version;

\o /dev/null

CREATE TEMP TABLE result (id text, what text, verdict text, note text);

CREATE FUNCTION pg_temp.rec(p_id text, p_what text, p_verdict text, p_note text DEFAULT NULL)
RETURNS void LANGUAGE sql AS
$$ INSERT INTO result VALUES (p_id, p_what, p_verdict, p_note) $$;

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
--   p_cons ถ้าให้มา: ชื่อ constraint ที่ปฏิเสธต้องเป็นหนึ่งในนี้
--   ไม่งั้น: ต้องอยู่บน p_table และครอบคอลัมน์ p_cols
CREATE FUNCTION pg_temp.try_reject(p_sql text, p_state text, p_table text,
                                   p_cols text[], p_cons text[] DEFAULT NULL)
RETURNS text LANGUAGE plpgsql AS $$
DECLARE
  v_state text; v_con text; v_tab text; v_msg text; v_cols text[];
BEGIN
  BEGIN
    EXECUTE p_sql;
    RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'ACC_NOT_REJECTED';
  EXCEPTION WHEN OTHERS THEN
    GET STACKED DIAGNOSTICS v_state = RETURNED_SQLSTATE,
                            v_con   = CONSTRAINT_NAME,
                            v_tab   = TABLE_NAME,
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
    RETURN format('FAIL: ถูกปฏิเสธด้วย %s ไม่ใช่ %s',
                  coalesce(nullif(v_con, ''), '?'), array_to_string(p_cons, '/'));
  END IF;

  v_cols := pg_temp.cols_of(v_tab, v_con);
  IF v_tab = p_table AND p_cols <@ v_cols THEN RETURN NULL; END IF;
  RETURN format('FAIL: ถูกปฏิเสธด้วย %s.%s (%s) ซึ่งไม่ใช่กฎที่ทดสอบ',
                v_tab, v_con, array_to_string(v_cols, ','));
END $$;

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

-- sha256 เป็น hex · รูปแบบเดียวกับที่ backend เก็บ hash
CREATE FUNCTION pg_temp.h(p text) RETURNS text LANGUAGE sql IMMUTABLE AS
$$ SELECT '0x' || encode(sha256(convert_to(p, 'UTF8')), 'hex') $$;


-- เวลาอ้างอิง · ต้นวันของวันที่รัน (ต้นฉบับใช้วันตายตัว 2026-10-01)
-- ฟังก์ชันอ่านรับ as_of จึงได้ผลเดิมทุกครั้ง แต่ trigger จองยอดตรวจกับ now() จริง
-- ถ้าใช้วันตายตัว fixture จะเริ่มผิดเองเมื่อเวลาจริงเดินเลยวันหมดอายุของล็อตทดสอบ
CREATE FUNCTION pg_temp.t0() RETURNS timestamptz LANGUAGE sql STABLE AS
$$ SELECT date_trunc('day', now()) $$;

-- เรียกฟังก์ชันอ่านแบบ dynamic · ฟังก์ชันที่ยังไม่มีให้กลายเป็น ERROR ของข้อนั้น ไม่ใช่หยุดทั้งไฟล์
CREATE FUNCTION pg_temp.q(p_sql text) RETURNS numeric LANGUAGE plpgsql AS $$
DECLARE v numeric;
BEGIN
  EXECUTE p_sql INTO v;
  RETURN v;
END $$;

CREATE FUNCTION pg_temp.check_num(p_id text, p_what text, p_sql text, p_expect numeric[])
RETURNS void LANGUAGE plpgsql AS $$
DECLARE v numeric[]; s text;
BEGIN
  v := ARRAY(SELECT pg_temp.q(x) FROM unnest(string_to_array(p_sql, ';;')) x);
  PERFORM pg_temp.rec(p_id, p_what, CASE WHEN v = p_expect THEN 'PASS' ELSE 'FAIL' END,
                      'ได้ ' || array_to_string(v, ' · ', 'NULL'));
EXCEPTION WHEN OTHERS THEN
  PERFORM pg_temp.rec(p_id, p_what, 'ERROR', SQLSTATE || ' · ' || SQLERRM);
END $$;

-- ------------------------------------------------------------ fixture
INSERT INTO merchant (id, name, status, activated_at) VALUES ('acc_mch', 'ร้านทดสอบ', 'ACTIVE', now());
INSERT INTO point_type (id, merchant_id, name, name_normalized, symbol, business_model, lot_policy) VALUES
  ('acc_pt', 'acc_mch', 'แต้มสะสมสุขใจ', 'แต้มสะสมสุขใจ', 'SUKJAI', 2, 'FEFO_ONLY'),
  ('acc_pt_d', 'acc_mch', 'แต้ม D', 'แต้ม d', 'SUKJAID', 4, 'FEFO_ONLY');
INSERT INTO point_type_chain (point_type_id, contract_address, deployed_block, deployed_tx_hash,
                              business_model, lot_policy, configured_block, configured_tx_hash) VALUES
  ('acc_pt',   '0x' || repeat('a6', 20), 100, '0x' || repeat('a1', 32), 2, 'FEFO_ONLY', 101, '0x' || repeat('a2', 32)),
  ('acc_pt_d', '0x' || repeat('d6', 20), 100, '0x' || repeat('d1', 32), 4, 'FEFO_ONLY', 101, '0x' || repeat('d2', 32));

-- ห้าล็อต · ห้าวันหมดอายุ · ตรงกับห้า bucket ของต้นฉบับ
INSERT INTO point_lot (point_type_id, lot_key, start_at, expire_at) VALUES
  ('acc_pt', '1', pg_temp.t0() - interval '200 days', pg_temp.t0() - interval '10 days'),
  ('acc_pt', '2', pg_temp.t0() - interval '100 days', pg_temp.t0() + interval '5 days'),
  ('acc_pt', '3', pg_temp.t0() - interval '60 days',  pg_temp.t0() + interval '20 days'),
  ('acc_pt', '4', pg_temp.t0() - interval '30 days',  pg_temp.t0() + interval '90 days'),
  ('acc_pt', '5', pg_temp.t0() - interval '10 days',  pg_temp.t0() + interval '60 days');
INSERT INTO point_lot_balance (point_type_id, holder_kind, holder_id, lot_key, amount) VALUES
  ('acc_pt', 'CUSTOMER', 'acc_cus_1', '1', '500'),   -- หมดอายุแล้ว
  ('acc_pt', 'CUSTOMER', 'acc_cus_1', '2', '100'),   -- อีก 5 วัน
  ('acc_pt', 'CUSTOMER', 'acc_cus_1', '3', '200'),   -- อีก 20 วัน
  ('acc_pt', 'CUSTOMER', 'acc_cus_1', '4', '300'),   -- อีก 90 วัน
  ('acc_pt', 'CUSTOMER', 'acc_cus_1', '5', '0');     -- ยอดศูนย์

INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
VALUES ('acc_op_1', 'customer:acc_cus_1', 'POINT_TRANSFER', 'acc_pt', '{}');
INSERT INTO outbox (id, operation_id, topic, payload)
VALUES ('acc_obx_1', 'acc_op_1', 'point.transfer.v1', '{}');

-- watermark และ cursor สัมพัทธ์กับค่าที่มีอยู่ · w = ค่าปัจจุบัน (หรือ 0)
CREATE TEMP TABLE acc_w AS
  SELECT coalesce((SELECT last_block FROM projection_watermark), 0) AS w;
INSERT INTO projection_watermark (id, last_block, last_block_at)
SELECT true, w + 1000, pg_temp.t0() - interval '30 seconds' FROM acc_w
ON CONFLICT (id) DO UPDATE SET last_block = excluded.last_block, last_block_at = excluded.last_block_at;
-- FEAT-14 · cursor ต้องชี้บล็อกที่มีอยู่จริงบนสายหลัก (0017)
INSERT INTO raw_block (block_hash, block_number, parent_hash, "timestamp")
SELECT '0x' || repeat('c5', 32), w + 1005, '0x' || repeat('c4', 32), pg_temp.t0() FROM acc_w;
INSERT INTO indexer_cursor (id, last_block, last_block_hash)
SELECT 'main', w + 1005, '0x' || repeat('c5', 32) FROM acc_w
ON CONFLICT (id) DO UPDATE SET last_block = excluded.last_block, last_block_hash = excluded.last_block_hash;

-- =====================================================================
-- T1 · ยอดที่ใช้ได้ = ผลรวมล็อตที่ยังไม่หมดอายุ                       RFD 10
-- T2 · as_of ย้อนหลัง ต้องได้คำตอบของตอนนั้น
-- T3 · as_of ในอนาคต ต้องตัดล็อตที่จะหมดอายุออก · ไม่ต้องมี job ไปลดยอด
-- =====================================================================
SELECT pg_temp.check_num('T1', 'ยอดที่ใช้ได้ = 100+200+300 = 600 (ไม่นับล็อตที่หมดอายุและล็อตศูนย์)',
  $q$SELECT f_available_points('acc_pt', 'CUSTOMER', 'acc_cus_1', pg_temp.t0())$q$, ARRAY[600]);

SELECT pg_temp.check_num('T2', 'ถาม ณ 30 วันก่อน → ได้ 1100 เพราะล็อต 500 ยังไม่หมดอายุ',
  $q$SELECT f_available_points('acc_pt', 'CUSTOMER', 'acc_cus_1', pg_temp.t0() - interval '30 days')$q$,
  ARRAY[1100]);

SELECT pg_temp.check_num('T3', 'ถามล่วงหน้า 10 วัน → 500 · ล่วงหน้า 30 วัน → 300',
  $q$SELECT f_available_points('acc_pt', 'CUSTOMER', 'acc_cus_1', pg_temp.t0() + interval '10 days');;
     SELECT f_available_points('acc_pt', 'CUSTOMER', 'acc_cus_1', pg_temp.t0() + interval '30 days')$q$,
  ARRAY[500, 300]);

-- =====================================================================
-- T4 · ยอดที่ถูกกันไว้ถูกหักออกจากยอดที่ใช้ได้
-- T5 · ยอดที่ใช้ได้ติดลบไม่ได้ แม้ reservation ค้างมากกว่ายอด
--      เกิดได้จริงเมื่อล็อตหมดอายุระหว่างที่ยังจองค้างอยู่
--      ต้นฉบับได้ 50 (ไม่ใช่กรณีติดลบ) · ที่นี่ถามเพิ่มที่ +100 วัน ซึ่งทุกล็อตหมดแล้ว
--      ยอด 0 − จอง 250 = −250 → ต้องได้ 0
-- T6 · ปล่อยจองแล้วยอดกลับมา
-- =====================================================================
INSERT INTO reservation (id, operation_id, subject_type, subject_id, amount)
VALUES ('acc_rsv_1', 'acc_op_1', 'POINT_BALANCE', 'acc_pt|CUSTOMER|acc_cus_1', '250');

SELECT pg_temp.check_num('T4', 'จองไว้ 250 → ยอดที่ใช้ได้เหลือ 350',
  $q$SELECT f_available_points('acc_pt', 'CUSTOMER', 'acc_cus_1', pg_temp.t0())$q$, ARRAY[350]);

SELECT pg_temp.check_num('T5', 'ยอดที่ใช้ได้ไม่ติดลบ · +30 วันได้ 50 · +100 วันได้ 0 ไม่ใช่ −250',
  $q$SELECT f_available_points('acc_pt', 'CUSTOMER', 'acc_cus_1', pg_temp.t0() + interval '30 days');;
     SELECT f_available_points('acc_pt', 'CUSTOMER', 'acc_cus_1', pg_temp.t0() + interval '100 days')$q$,
  ARRAY[50, 0]);

DO $$
BEGIN
  UPDATE operation SET status = 'SUBMITTED' WHERE id = 'acc_op_1';
  UPDATE operation SET status = 'CHAIN_REVERTED' WHERE id = 'acc_op_1';
  UPDATE reservation SET released_at = now() WHERE id = 'acc_rsv_1';
  PERFORM pg_temp.check_num('T6', 'ปล่อยจอง → ยอดกลับเป็น 600',
    $q$SELECT f_available_points('acc_pt', 'CUSTOMER', 'acc_cus_1', pg_temp.t0())$q$, ARRAY[600]);
EXCEPTION WHEN OTHERS THEN
  PERFORM pg_temp.rec('T6', 'ปล่อยจอง → ยอดกลับเป็น 600', 'ERROR', SQLSTATE || ' · ' || SQLERRM);
END $$;

-- =====================================================================
-- T7 · พยากรณ์การหมดอายุ · รวมตามวันและเรียงจากใกล้ไปไกล
-- T8 · พยากรณ์ไม่รวมล็อตที่หมดอายุไปแล้วและล็อตที่ยอดเป็นศูนย์
-- =====================================================================
SELECT pg_temp.check_num('T7', 'พยากรณ์ 30 วันข้างหน้า → 2 วัน รวม 300 (100 + 200) เรียงใกล้ไปไกล',
  $q$SELECT count(*) FROM f_expiry_forecast('acc_pt', 'CUSTOMER', 'acc_cus_1', pg_temp.t0(), 30);;
     SELECT sum(amount) FROM f_expiry_forecast('acc_pt', 'CUSTOMER', 'acc_cus_1', pg_temp.t0(), 30);;
     SELECT (array_agg(amount ORDER BY ord))[1]
       FROM (SELECT row_number() OVER () AS ord, amount
               FROM f_expiry_forecast('acc_pt', 'CUSTOMER', 'acc_cus_1', pg_temp.t0(), 30)) x$q$,
  ARRAY[2, 300, 100]);

SELECT pg_temp.check_num('T8', 'พยากรณ์ 365 วัน → 600 · ไม่นับล็อตหมดอายุ (500) และล็อตศูนย์',
  $q$SELECT coalesce(sum(amount), 0)
       FROM f_expiry_forecast('acc_pt', 'CUSTOMER', 'acc_cus_1', pg_temp.t0(), 365)$q$, ARRAY[600]);

-- =====================================================================
-- T9 · รายงานยอดที่หมดอายุสะสม · เป็น query ไม่ใช่ตาราง
-- T9b · ที่ expireDate พอดี ล็อต D หมดอายุแล้ว ล็อต B ยังไม่หมด      (เพิ่ม · contract v3)
-- =====================================================================
SELECT pg_temp.check_num('T9', 'หมดอายุสะสม ณ วันนี้ 500 · อีก 30 วัน 800',
  $q$SELECT f_expired_total('acc_pt', pg_temp.t0());;
     SELECT f_expired_total('acc_pt', pg_temp.t0() + interval '30 days')$q$, ARRAY[500, 800]);

INSERT INTO point_lot (point_type_id, lot_key, start_at, expire_at)
VALUES ('acc_pt_d', '1', pg_temp.t0() - interval '10 days', pg_temp.t0() + interval '5 days');
INSERT INTO point_lot_balance (point_type_id, holder_kind, holder_id, lot_key, amount)
VALUES ('acc_pt_d', 'CUSTOMER', 'acc_cus_1', '1', '40');

SELECT pg_temp.check_num('T9b', 'ที่ expireDate พอดี · D นับว่าหมดอายุ (40) · B ยังไม่หมด (ล็อต 2 ไม่ถูกนับ)',
  $q$SELECT f_expired_total('acc_pt_d', pg_temp.t0() + interval '5 days');;
     SELECT f_expired_total('acc_pt', pg_temp.t0() + interval '5 days')$q$, ARRAY[40, 500]);

-- =====================================================================
-- T10 · แจ้งเตือนซ้ำที่ระดับเดียวกันของล็อตเดิมไม่ได้
-- T11 · แจ้งคนละระดับได้ · 30 วัน 7 วัน 1 วัน เป็นคนละใบ
-- T12 · ระดับการแจ้งเตือนนอกนโยบายเข้าไม่ได้
-- T13 · แจ้งเตือนล็อตที่หมดอายุไปแล้วไม่ได้
-- =====================================================================
INSERT INTO expiry_notice (id, point_type_id, holder_kind, holder_id, lot_key, threshold_days, channel, notified_at)
VALUES ('acc_en_1', 'acc_pt', 'CUSTOMER', 'acc_cus_1', '2', 7, 'PUSH', pg_temp.t0());

SELECT pg_temp.rec_checks('T10', 'แจ้งเตือนล็อตเดิมที่ระดับ 7 วันซ้ำ → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO expiry_notice (id, point_type_id, holder_kind, holder_id, lot_key, threshold_days, channel, notified_at)
    VALUES ('acc_en_dup', 'acc_pt', 'CUSTOMER', 'acc_cus_1', '2', 7, 'EMAIL', pg_temp.t0())
  $q$, '23505', 'expiry_notice', ARRAY['holder_id', 'lot_key', 'threshold_days']));

DO $$
DECLARE n int;
BEGIN
  INSERT INTO expiry_notice (id, point_type_id, holder_kind, holder_id, lot_key, threshold_days, channel, notified_at)
  VALUES ('acc_en_2', 'acc_pt', 'CUSTOMER', 'acc_cus_1', '2', 1, 'SMS', pg_temp.t0());
  SELECT count(*) INTO n FROM expiry_notice WHERE holder_id = 'acc_cus_1' AND lot_key = '2';
  PERFORM pg_temp.rec('T11', 'แจ้งล็อตเดิมที่ระดับ 7 และ 1 วันได้ · ได้ 2 ใบ',
                      CASE WHEN n = 2 THEN 'PASS' ELSE 'FAIL' END, format('ได้ %s ใบ', n));
EXCEPTION WHEN OTHERS THEN
  PERFORM pg_temp.rec('T11', 'แจ้งล็อตเดิมที่ระดับ 7 และ 1 วันได้ · ได้ 2 ใบ', 'FAIL', SQLSTATE || ' · ' || SQLERRM);
END $$;

SELECT pg_temp.rec_checks('T12', 'threshold_days ที่ไม่ใช่ 30/7/1 → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO expiry_notice (id, point_type_id, holder_kind, holder_id, lot_key, threshold_days, channel, notified_at)
    VALUES ('acc_en_bad', 'acc_pt', 'CUSTOMER', 'acc_cus_1', '3', 14, 'PUSH', pg_temp.t0())
  $q$, '23514', 'expiry_notice', ARRAY['threshold_days']));

SELECT pg_temp.rec_checks('T13', 'แจ้งเตือนล่วงหน้าของล็อตที่หมดอายุแล้ว → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO expiry_notice (id, point_type_id, holder_kind, holder_id, lot_key, threshold_days, channel, notified_at)
    VALUES ('acc_en_late', 'acc_pt', 'CUSTOMER', 'acc_cus_1', '1', 7, 'PUSH', pg_temp.t0())
  $q$, '23514', 'expiry_notice', NULL, ARRAY['trg_notice_not_expired']));

-- =====================================================================
-- T14 · watermark ถอยหลังไม่ได้ · projection ย้อนกลับไม่ได้
-- T15 · watermark มีได้แถวเดียว
-- T16 · บอกได้ว่า projection ตามหลัง indexer กี่ block (3)
-- =====================================================================
UPDATE projection_watermark SET last_block = (SELECT w + 1002 FROM acc_w), last_block_at = pg_temp.t0();

SELECT pg_temp.rec_checks('T14', 'เลื่อน watermark ถอยหลัง → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    UPDATE projection_watermark SET last_block = (SELECT w + 999 FROM acc_w)
  $q$, '23514', 'projection_watermark', NULL, ARRAY['trg_watermark_monotonic']));

SELECT pg_temp.rec_checks('T15', 'เพิ่มแถวที่สองใน projection_watermark → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO projection_watermark (id, last_block, last_block_at) VALUES (false, 1, pg_temp.t0())
  $q$, '23514', 'projection_watermark', NULL, ARRAY['ck_watermark_single_row']));

SELECT pg_temp.check_num('T16', 'คำนวณระยะที่ projection ตามหลัง indexer ได้ (3 block)',
  $q$SELECT c.last_block - w.last_block FROM indexer_cursor c, projection_watermark w
      WHERE c.id = 'main'$q$, ARRAY[3]);

-- =====================================================================
-- T17 · เก็บ uint256 สูงสุด (2^256−1) ได้ครบทุกหลัก · และอ่านกลับเป็นยอดได้   RFD 14 D1
-- =====================================================================
DO $$
DECLARE max text := '115792089237316195423570985008687907853269984665640564039457584007913129639935';
BEGIN
  INSERT INTO point_lot_balance (point_type_id, holder_kind, holder_id, lot_key, amount)
  VALUES ('acc_pt', 'CUSTOMER', 'acc_cus_max', '4', max);
  PERFORM pg_temp.check_num('T17', 'เก็บ uint256 สูงสุดได้ครบ และยอดที่ใช้ได้อ่านออกมาเท่ากัน',
    format($q$SELECT f_available_points('acc_pt', 'CUSTOMER', 'acc_cus_max', pg_temp.t0()) - %s$q$, max),
    ARRAY[0]);
EXCEPTION WHEN OTHERS THEN
  PERFORM pg_temp.rec('T17', 'เก็บ uint256 สูงสุดได้ครบ และยอดที่ใช้ได้อ่านออกมาเท่ากัน', 'ERROR',
                      SQLSTATE || ' · ' || SQLERRM);
END $$;

-- =====================================================================
-- T18 · ไม่มีคอลัมน์ยอดคงเหลือสำเร็จรูปในตารางไหนเลย              RFD 10
--       ยกเว้น deposit_reversal.available_at_reversal (FEAT-12) · ค่าที่บันทึก ณ ตอนตีกลับ
-- =====================================================================
DO $$
DECLARE bad text;
BEGIN
  SELECT string_agg(table_name || '.' || column_name, ',') INTO bad FROM information_schema.columns
   WHERE table_schema = 'public'
     AND column_name ~* '(available|cached_balance|current_balance|remaining_balance)'
     AND (table_name, column_name) <> ('deposit_reversal', 'available_at_reversal');
  PERFORM pg_temp.rec('T18', 'ไม่มีคอลัมน์ยอดคงเหลือสำเร็จรูปในฐานข้อมูลเลย',
    CASE WHEN bad IS NULL THEN 'PASS' ELSE 'FAIL' END,
    CASE WHEN bad IS NULL THEN 'ยกเว้น deposit_reversal.available_at_reversal (FEAT-12)'
         ELSE 'คอลัมน์: ' || bad END);
END $$;

-- =====================================================================
-- T19 · ฟังก์ชันอ่านทุกตัวต้องไม่เรียก now() ข้างใน และไม่เป็น VOLATILE
--       ถ้าเรียก คำตอบจะขึ้นกับเวลาที่รัน ถามอดีตหรืออนาคตไม่ได้ ทดสอบซ้ำไม่ได้
-- =====================================================================
DO $$
DECLARE want text[] := ARRAY['f_available_points', 'f_points_fefo', 'f_expiry_forecast',
                             'f_expired_total', 'f_point_lot_active', 'f_point_lot_order'];
        missing text; bad text;
BEGIN
  SELECT string_agg(w, ',') INTO missing FROM unnest(want) w
   WHERE NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                      WHERE n.nspname = 'public' AND p.proname = w);
  SELECT string_agg(p.proname, ',') INTO bad
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
   WHERE n.nspname = 'public' AND p.proname = ANY (want)
     AND (pg_get_functiondef(p.oid) ~* '\m(now|current_timestamp|clock_timestamp|transaction_timestamp)\M'
          OR p.provolatile = 'v');
  PERFORM pg_temp.rec('T19', 'ฟังก์ชันอ่านไม่เรียก now() ข้างใน และไม่เป็น VOLATILE',
    CASE WHEN missing IS NOT NULL THEN 'ERROR' WHEN bad IS NULL THEN 'PASS' ELSE 'FAIL' END,
    concat_ws(' · ', CASE WHEN missing IS NOT NULL THEN 'ยังไม่มี: ' || missing END,
                     CASE WHEN bad IS NOT NULL THEN 'เรียกเวลาปัจจุบันหรือ VOLATILE: ' || bad END));
END $$;

-- =====================================================================
-- T20 · query ยอดต้องใช้ index ไม่ใช่สแกนทั้งตาราง
--       ใส่ 5000 แถว · ANALYZE · อ่าน plan จริงจาก EXPLAIN ของ query เดียวกับใน f_available_points
--       ต้นฉบับตรวจชื่อ ix_bucket_live · ที่นี่ point_lot_balance มี PK ขึ้นต้นด้วย
--       (point_type_id, holder_kind, holder_id) อยู่แล้ว จึงตรวจว่าอ่านตารางนี้ผ่าน index
--       และไม่มี Seq Scan บนตารางนี้ · ไม่ผูกกับชื่อ index
-- =====================================================================
DO $$
DECLARE line text; plan text := '';
BEGIN
  INSERT INTO point_lot_balance (point_type_id, holder_kind, holder_id, lot_key, amount)
  SELECT 'acc_pt', 'CUSTOMER', 'acc_bulk_' || g, (2 + g % 3)::text, '10'
    FROM generate_series(1, 5000) g;
  ANALYZE point_lot_balance;
  ANALYZE point_lot;
  FOR line IN EXECUTE format($q$
    EXPLAIN (COSTS OFF)
    SELECT coalesce(sum(b.amount::numeric), 0)
      FROM point_lot_balance b
      JOIN point_lot l USING (point_type_id, lot_key)
      LEFT JOIN point_type_chain c ON c.point_type_id = b.point_type_id
     WHERE b.point_type_id = %L AND b.holder_kind = 'CUSTOMER' AND b.holder_id = %L
       AND f_point_lot_active(c.business_model, l.start_at, l.expire_at, %L)$q$,
    'acc_pt', 'acc_cus_1', pg_temp.t0())
  LOOP
    plan := plan || line || E'\n';
  END LOOP;
  PERFORM pg_temp.rec('T20', 'EXPLAIN ยืนยันว่า query ยอดอ่าน point_lot_balance ผ่าน index ไม่สแกนทั้งตาราง',
    CASE WHEN plan ~ 'Index (Only )?Scan using \S+ on point_lot_balance'
              AND plan !~ 'Seq Scan on point_lot_balance' THEN 'PASS' ELSE 'FAIL' END,
    coalesce(substring(plan FROM '(?:Index (?:Only )?Scan using|Seq Scan on|Bitmap Heap Scan on) \S+(?: on point_lot_balance)?'),
             left(regexp_replace(plan, '\s+', ' ', 'g'), 200)));
EXCEPTION WHEN OTHERS THEN
  PERFORM pg_temp.rec('T20', 'EXPLAIN ยืนยันว่า query ยอดอ่าน point_lot_balance ผ่าน index ไม่สแกนทั้งตาราง', 'ERROR',
                      SQLSTATE || ' · ' || SQLERRM);
END $$;

-- ------------------------------------------------------------- สรุป
\o
SELECT id AS "#", what AS "ข้อรับประกัน", verdict AS "ผล", coalesce(note, '') AS "หมายเหตุ"
FROM result
ORDER BY lpad(substring(id FROM '[0-9]+'), 3, '0'), id;

SELECT count(*) FILTER (WHERE verdict = 'PASS')  AS "ผ่าน",
       count(*) FILTER (WHERE verdict = 'FAIL')  AS "ไม่ผ่าน",
       count(*) FILTER (WHERE verdict = 'ERROR') AS "test รันไม่ได้",
       count(*)                                  AS "รวม"
FROM result;

-- ไม่ commit · ฐานข้อมูลกลับไปเหมือนก่อนรัน
ROLLBACK;
