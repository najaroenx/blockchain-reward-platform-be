-- =====================================================================
-- FEAT-05 · Transfer Point · acceptance สำหรับ blockchain-reward-platform-be
--
-- ต้นฉบับ : blockchain-reward-platform/reference/feat-05/tests/acceptance.sql (20 ข้อ)
-- schema  : migrations 0002 → 0008_feat05_lots_inventory
--
-- รัน (Postgres ใน docker):
--   docker exec -i blockchain-reward-platform-be-postgres-1 \
--     psql -U reward -d reward < tests/sql/feat05_acceptance.sql
--
-- ผล  PASS ฐานข้อมูลบังคับกฎนี้ได้จริง · FAIL ไม่ได้ หรือถูกปฏิเสธด้วยกฎตัวอื่น
--     ERROR test เองรันไม่ได้ (เช่น ตารางหรือฟังก์ชันที่ต้องใช้ยังไม่มี) · ดูหมายเหตุ
--
-- ต่างจากต้นฉบับ · หลักฐานอยู่ใน docs/reference-findings.md
--   1. ยอดแต้มเป็นล็อตของ NewPointToken v3 ไม่ใช่ bucket ตาม epoch
--      · ล็อตมาจาก LotCreated · แบบ A ไม่มีล็อต ใช้แถว lot_key = 'A' ที่มีช่วงวันของโปรแกรม
--      · T2  ไม่นับล็อตที่หมดอายุ และล็อตที่ยังไม่ถึงวันเริ่ม (Pending)
--      · T2b เพิ่ม · ที่ expireDate พอดี A/B/C ยังใช้ได้ แต่ D หมดแล้ว (ตาม contract)
--      · T4b เพิ่ม · แบบ D ร้านแจกได้ไม่เกินคลัง (inventory) · แบบ A/B/C ร้าน mint ใหม่ ไม่ต้องจอง
--      · T9  ล็อตหมดอายุก่อนวันเริ่มไม่ได้ · T10 หนึ่งแถวต่อ (ผู้ถือ, ล็อต)
--      · T17 FEFO เรียง (expire_at, lotId) แบบตัวเลข · lotId 9 มาก่อน 10
--      · T20 ตรวจตารางล็อตแทน point_balance_bucket ที่เลิกใช้
--   2. บัญชีแต้มแยกเป็น point_ledger_entry · ledger_entry เป็นของ THB (FEAT-10/12)
--      · T16 DEBIT = CREDIT ต่อ operation บังคับตอน COMMIT (ต้นฉบับแค่ query ตรวจ)
--      · T16b เพิ่ม · แถวบัญชีที่อ้างล็อต ต้องเป็นล็อตที่มีอยู่จริง
--   3. จำนวนเก็บเป็นตัวเลขฐานสิบใน text (0002) · T11 T12 ตรวจรูปแบบและช่วง uint256
--   4. T7b เพิ่ม · จองแต้มของ point type ที่ไม่มีอยู่ไม่ได้
--   5. hash/address เป็น hex string · ข้อมูลทดสอบขึ้นต้นด้วย acc_
--   6. ข้อ "ต้องถูกปฏิเสธ" ตรวจว่าชนกฎที่ตั้งใจทดสอบจริง และย้อนแถวทิ้งถ้าไม่ถูกห้าม
--   7. T18 T19 ตรวจทั้งตาราง รวมข้อมูลจริง · FAIL อาจมาจากข้อมูลเก่า ดูหมายเหตุ
--
-- ทั้งไฟล์อยู่ใน transaction เดียวแล้ว ROLLBACK · รันซ้ำกี่รอบก็ได้
-- =====================================================================
\set ON_ERROR_STOP on
\pset format aligned

BEGIN;

-- ควรได้ 0008_feat05_lots_inventory ขึ้นไป
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


-- เวลาอ้างอิงคงที่ · now() แข็งตัวใน transaction อยู่แล้ว แต่ T2b ต้องการวินาทีที่ระบุได้
CREATE FUNCTION pg_temp.t0() RETURNS timestamptz LANGUAGE sql IMMUTABLE AS
$$ SELECT timestamptz '2026-11-18 12:00:00+07' $$;

-- รันชุดคำสั่งเตรียมข้อมูล · ถ้าพังให้บันทึกเป็น ERROR ของข้อนั้นแทนที่จะหยุดทั้งไฟล์
CREATE FUNCTION pg_temp.setup(p_id text, p_what text, p_sql text) RETURNS boolean
LANGUAGE plpgsql AS $$
BEGIN
  EXECUTE p_sql;
  RETURN true;
EXCEPTION WHEN OTHERS THEN
  PERFORM pg_temp.rec(p_id, p_what, 'ERROR', 'เตรียมข้อมูลไม่ได้ · ' || SQLSTATE || ' · ' || SQLERRM);
  RETURN false;
END $$;

-- ยอดที่ใช้ได้ · คืน NULL ถ้าฟังก์ชันยังไม่มี
CREATE FUNCTION pg_temp.avail(p_pt text, p_kind text, p_holder text, p_at timestamptz)
RETURNS numeric LANGUAGE plpgsql AS $$
DECLARE v numeric;
BEGIN
  EXECUTE 'SELECT f_available_points($1, $2, $3, $4)' INTO v USING p_pt, p_kind, p_holder, p_at;
  RETURN v;
EXCEPTION WHEN undefined_function THEN RETURN NULL;
END $$;

-- ------------------------------------------------------------ fixture
INSERT INTO merchant (id, name, status, activated_at)
VALUES ('acc_mch', 'ร้านทดสอบ', 'ACTIVE', now());

-- B · ล็อต · FEFO   และ   D · อายุนับจากวันแจก · มีคลัง
INSERT INTO point_type (id, merchant_id, name, name_normalized, symbol, business_model, lot_policy)
VALUES ('acc_pt_b', 'acc_mch', 'Lots B', 'lots b', 'LOTB', 2, 'FEFO_ONLY'),
       ('acc_pt_d', 'acc_mch', 'Lots D', 'lots d', 'LOTD', 4, 'FEFO_ONLY');
INSERT INTO point_type_chain (point_type_id, contract_address, deployed_block, deployed_tx_hash,
                              business_model, lot_policy, configured_block, configured_tx_hash)
VALUES ('acc_pt_b', '0x' || repeat('b0', 20), 100, '0x' || repeat('b1', 32), 2, 'FEFO_ONLY', 101, '0x' || repeat('b2', 32)),
       ('acc_pt_d', '0x' || repeat('d0', 20), 100, '0x' || repeat('d1', 32), 4, 'FEFO_ONLY', 101, '0x' || repeat('d2', 32));

INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
VALUES ('acc_op_1', 'merchant:acc_mch', 'POINT_TRANSFER', 'acc_pt_b', '{}');
INSERT INTO outbox (id, operation_id, topic, payload)
VALUES ('acc_obx_1', 'acc_op_1', 'point.transfer.v1', '{}');

-- ล็อตของลูกค้า acc_cus_1 ใน B · หมดแล้ว 500 · ใช้ได้ 1000 · ยังไม่เริ่ม 200
SELECT pg_temp.setup('T2', 'ล็อตที่หมดอายุและที่ยังไม่เริ่มไม่นับ (รวม 1700 · ใช้ได้ 1000)', $q$
  INSERT INTO point_lot (point_type_id, lot_key, start_at, expire_at) VALUES
    ('acc_pt_b', '1', pg_temp.t0() - interval '60 days', pg_temp.t0() - interval '1 day'),
    ('acc_pt_b', '2', pg_temp.t0() - interval '10 days', pg_temp.t0() + interval '30 days'),
    ('acc_pt_b', '3', pg_temp.t0() + interval '5 days',  pg_temp.t0() + interval '60 days');
  INSERT INTO point_lot_balance (point_type_id, holder_kind, holder_id, lot_key, amount) VALUES
    ('acc_pt_b', 'CUSTOMER', 'acc_cus_1', '1', '500'),
    ('acc_pt_b', 'CUSTOMER', 'acc_cus_1', '2', '1000'),
    ('acc_pt_b', 'CUSTOMER', 'acc_cus_1', '3', '200');
$q$);

-- =====================================================================
-- T1 · โอน point ซ้ำด้วย key เดิม → แถวที่สองได้ 0 rows            RFD 3 R1
-- =====================================================================
DO $$
DECLARE n1 int; n2 int;
BEGIN
  INSERT INTO idempotency_key (id, scope, key, fingerprint, expires_at)
  VALUES ('acc_ik_1', 'merchant:acc_mch', 'acc-transfer-0001', pg_temp.h('body'), now() + interval '72 hours')
  ON CONFLICT (scope, key) DO NOTHING;
  GET DIAGNOSTICS n1 = ROW_COUNT;
  INSERT INTO idempotency_key (id, scope, key, fingerprint, expires_at)
  VALUES ('acc_ik_2', 'merchant:acc_mch', 'acc-transfer-0001', pg_temp.h('body'), now() + interval '72 hours')
  ON CONFLICT (scope, key) DO NOTHING;
  GET DIAGNOSTICS n2 = ROW_COUNT;
  PERFORM pg_temp.rec('T1', 'โอน point ซ้ำด้วย key เดิม → แถวที่สองได้ 0 rows',
                      CASE WHEN n1 = 1 AND n2 = 0 THEN 'PASS' ELSE 'FAIL' END);
END $$;

-- =====================================================================
-- T2 · ยอดที่ใช้ได้ไม่นับล็อตที่หมดอายุ และล็อตที่ยังไม่ถึงวันเริ่ม     RFD 10
-- =====================================================================
DO $$
DECLARE v numeric; total numeric;
BEGIN
  IF EXISTS (SELECT 1 FROM result WHERE id = 'T2') THEN RETURN; END IF;
  v := pg_temp.avail('acc_pt_b', 'CUSTOMER', 'acc_cus_1', pg_temp.t0());
  SELECT sum(amount::numeric) INTO total FROM point_lot_balance WHERE holder_id = 'acc_cus_1';
  PERFORM pg_temp.rec('T2', 'ล็อตที่หมดอายุและที่ยังไม่เริ่มไม่นับ (รวม 1700 · ใช้ได้ 1000)',
    CASE WHEN v IS NULL THEN 'ERROR' WHEN v = 1000 AND total = 1700 THEN 'PASS' ELSE 'FAIL' END,
    CASE WHEN v IS NULL THEN 'ไม่มี f_available_points' ELSE format('รวม %s · ใช้ได้ %s', total, v) END);
END $$;

-- =====================================================================
-- T2b · วินาทีที่ expireDate พอดี · A/B/C ยังใช้ได้ · D หมดแล้ว      (เพิ่ม · contract v3)
-- =====================================================================
DO $$
DECLARE vb numeric; vd numeric;
BEGIN
  IF NOT pg_temp.setup('T2b', 'ที่ expireDate พอดี B ยังใช้ได้ · D หมดแล้ว', $q$
    INSERT INTO point_lot (point_type_id, lot_key, start_at, expire_at) VALUES
      ('acc_pt_b', '50', pg_temp.t0() - interval '1 day', pg_temp.t0() + interval '7 days'),
      ('acc_pt_d', '50', pg_temp.t0() - interval '1 day', pg_temp.t0() + interval '7 days');
    INSERT INTO point_lot_balance (point_type_id, holder_kind, holder_id, lot_key, amount) VALUES
      ('acc_pt_b', 'CUSTOMER', 'acc_cus_edge', '50', '10'),
      ('acc_pt_d', 'CUSTOMER', 'acc_cus_edge', '50', '10');
  $q$) THEN RETURN; END IF;
  vb := pg_temp.avail('acc_pt_b', 'CUSTOMER', 'acc_cus_edge', pg_temp.t0() + interval '7 days');
  vd := pg_temp.avail('acc_pt_d', 'CUSTOMER', 'acc_cus_edge', pg_temp.t0() + interval '7 days');
  PERFORM pg_temp.rec('T2b', 'ที่ expireDate พอดี B ยังใช้ได้ · D หมดแล้ว',
    CASE WHEN vb IS NULL THEN 'ERROR' WHEN vb = 10 AND vd = 0 THEN 'PASS' ELSE 'FAIL' END,
    format('B %s · D %s', vb, vd));
END $$;

-- =====================================================================
-- T3 T4 T5 · จองกันยอดของลูกค้า · จองพร้อมกันได้หลายรายการแต่ไม่เกินยอด
-- =====================================================================
SELECT pg_temp.rec_checks('T3', 'จอง 1001 ทั้งที่ใช้ได้ 1000 → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO reservation (id, operation_id, subject_type, subject_id, amount)
    VALUES ('acc_rs_x', 'acc_op_1', 'POINT_BALANCE', 'acc_pt_b|CUSTOMER|acc_cus_1', '1001')
  $q$, '23514', 'reservation', NULL, ARRAY['trg_point_reservation_balance']));

DO $$
DECLARE v numeric;
BEGIN
  INSERT INTO reservation (id, operation_id, subject_type, subject_id, amount) VALUES
    ('acc_rs_1', 'acc_op_1', 'POINT_BALANCE', 'acc_pt_b|CUSTOMER|acc_cus_1', '400'),
    ('acc_rs_2', 'acc_op_1', 'POINT_BALANCE', 'acc_pt_b|CUSTOMER|acc_cus_1', '300');
  v := pg_temp.avail('acc_pt_b', 'CUSTOMER', 'acc_cus_1', now());
  PERFORM pg_temp.rec('T4', 'จองหลายรายการพร้อมกันได้ · จอง 400+300 เหลือ 300',
    CASE WHEN v = 300 THEN 'PASS' WHEN v IS NULL THEN 'ERROR' ELSE 'FAIL' END,
    format('เหลือ %s', v));
EXCEPTION WHEN OTHERS THEN
  PERFORM pg_temp.rec('T4', 'จองหลายรายการพร้อมกันได้ · จอง 400+300 เหลือ 300', 'FAIL',
                      SQLSTATE || ' · ' || SQLERRM);
END $$;

SELECT pg_temp.rec_checks('T5', 'จองเพิ่ม 301 ทั้งที่เหลือ 300 → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO reservation (id, operation_id, subject_type, subject_id, amount)
    VALUES ('acc_rs_y', 'acc_op_1', 'POINT_BALANCE', 'acc_pt_b|CUSTOMER|acc_cus_1', '301')
  $q$, '23514', 'reservation', NULL, ARRAY['trg_point_reservation_balance']));

-- =====================================================================
-- T4b · แบบ D ร้านแจกจากคลัง · จองได้ไม่เกินยอดคลัง              (เพิ่ม · contract v3)
--       แบบ A/B/C ร้าน mint ใหม่ทุกครั้ง ไม่มียอดให้จอง
-- =====================================================================
DO $$
DECLARE r text;
BEGIN
  IF NOT pg_temp.setup('T4b', 'แบบ D จองแจกได้ไม่เกินคลัง (1000 · จอง 600 แล้ว 401 ไม่ได้)', $q$
    INSERT INTO point_inventory (point_type_id, holder_kind, holder_id, amount)
    VALUES ('acc_pt_d', 'MERCHANT', 'acc_mch', '1000');
    INSERT INTO reservation (id, operation_id, subject_type, subject_id, amount)
    VALUES ('acc_rs_d1', 'acc_op_1', 'POINT_BALANCE', 'acc_pt_d|MERCHANT|acc_mch', '600');
  $q$) THEN RETURN; END IF;
  r := pg_temp.try_reject($q$
    INSERT INTO reservation (id, operation_id, subject_type, subject_id, amount)
    VALUES ('acc_rs_d2', 'acc_op_1', 'POINT_BALANCE', 'acc_pt_d|MERCHANT|acc_mch', '401')
  $q$, '23514', 'reservation', NULL, ARRAY['trg_point_reservation_balance']);
  PERFORM pg_temp.rec_checks('T4b', 'แบบ D จองแจกได้ไม่เกินคลัง (1000 · จอง 600 แล้ว 401 ไม่ได้)', r);
END $$;

-- =====================================================================
-- T6 · ปล่อยจองก่อนงานจบไม่ได้ · พอ CHAIN_REVERTED แล้วคืน 400 → เหลือ 700
-- =====================================================================
DO $$
DECLARE r text; v numeric;
BEGIN
  r := pg_temp.try_reject($q$
    UPDATE reservation SET released_at = now() WHERE id = 'acc_rs_1'
  $q$, '23514', 'reservation', NULL, ARRAY['trg_reservation_release_timing']);
  UPDATE operation SET status = 'SUBMITTED' WHERE id = 'acc_op_1';
  UPDATE operation SET status = 'CHAIN_REVERTED' WHERE id = 'acc_op_1';
  UPDATE reservation SET released_at = now() WHERE id = 'acc_rs_1';
  v := pg_temp.avail('acc_pt_b', 'CUSTOMER', 'acc_cus_1', now());
  IF v IS DISTINCT FROM 700 THEN
    r := coalesce(r || ' | ', '') || format('FAIL: หลังคืนเหลือ %s ไม่ใช่ 700', coalesce(v::text, 'ไม่มี f_available_points'));
  END IF;
  PERFORM pg_temp.rec_checks('T6', 'ปล่อยจองก่อนงานจบไม่ได้ · CHAIN_REVERTED แล้วคืน 400 → เหลือ 700', r);
EXCEPTION WHEN OTHERS THEN
  PERFORM pg_temp.rec('T6', 'ปล่อยจองก่อนงานจบไม่ได้ · CHAIN_REVERTED แล้วคืน 400 → เหลือ 700',
                      'ERROR', SQLSTATE || ' · ' || SQLERRM);
END $$;

-- งานใหม่สำหรับข้อถัดไป · งานเดิมจบแล้ว
INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
VALUES ('acc_op_2', 'merchant:acc_mch', 'POINT_TRANSFER', 'acc_pt_b', '{}');
INSERT INTO outbox (id, operation_id, topic, payload)
VALUES ('acc_obx_2', 'acc_op_2', 'point.transfer.v1', '{}');

-- =====================================================================
-- T7 · subject_id ของ POINT_BALANCE ต้องเป็น <point_type_id>|<MERCHANT|CUSTOMER>|<holder_id>
-- T7b · และต้องเป็น point type ที่มีอยู่จริง                         (เพิ่ม)
-- =====================================================================
SELECT pg_temp.rec_checks('T7', 'subject_id ที่ไม่ใช่รูปแบบ pt|kind|holder → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO reservation (id, operation_id, subject_type, subject_id, amount)
    VALUES ('acc_rs_bad', 'acc_op_2', 'POINT_BALANCE', 'acc_pt_b:acc_cus_1', '1')
  $q$, '23514', 'reservation', ARRAY['subject_id', 'subject_type']),
  pg_temp.try_reject($q$
    INSERT INTO reservation (id, operation_id, subject_type, subject_id, amount)
    VALUES ('acc_rs_bad2', 'acc_op_2', 'POINT_BALANCE', 'acc_pt_b|PLATFORM|x', '1')
  $q$, '23514', 'reservation', ARRAY['subject_id', 'subject_type']));

SELECT pg_temp.rec_checks('T7b', 'จองแต้มของ point type ที่ไม่มีอยู่ → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO reservation (id, operation_id, subject_type, subject_id, amount)
    VALUES ('acc_rs_ghost', 'acc_op_2', 'POINT_BALANCE', 'acc_pt_ghost|CUSTOMER|acc_cus_1', '1')
  $q$, '23514', 'reservation', NULL, ARRAY['trg_point_reservation_balance']));

-- =====================================================================
-- T8 · ของชิ้นเดียว (coupon) จองซ้อนไม่ได้เหมือนเดิม                 RFD 9 Q4
--      index แคบเหลือ COUPON_UNIT LISTING ESCROW · POINT_BALANCE จองซ้อนได้ (T4)
-- =====================================================================
INSERT INTO reservation (id, operation_id, subject_type, subject_id, amount)
VALUES ('acc_rs_c1', 'acc_op_2', 'COUPON_UNIT', 'acc_coupon_1', '1');

SELECT pg_temp.rec_checks('T8', 'จอง coupon ใบเดียวกันซ้อน → ยังถูกปฏิเสธเหมือนเดิม',
  pg_temp.try_reject($q$
    INSERT INTO reservation (id, operation_id, subject_type, subject_id, amount)
    VALUES ('acc_rs_c2', 'acc_op_2', 'COUPON_UNIT', 'acc_coupon_1', '1')
  $q$, '23505', 'reservation', ARRAY['subject_id', 'subject_type']));

-- =====================================================================
-- T9 · ล็อตที่หมดอายุก่อน (หรือพร้อม) วันเริ่ม → ถูกปฏิเสธ
-- T10 · ผู้ถือหนึ่งคนมีได้แถวเดียวต่อล็อต
-- =====================================================================
SELECT pg_temp.rec_checks('T9', 'ล็อตที่ expire_at ไม่อยู่หลัง start_at → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO point_lot (point_type_id, lot_key, start_at, expire_at)
    VALUES ('acc_pt_b', '99', pg_temp.t0(), pg_temp.t0())
  $q$, '23514', 'point_lot', ARRAY['expire_at', 'start_at']));

SELECT pg_temp.rec_checks('T10', 'แถวยอดซ้ำของผู้ถือเดิมในล็อตเดิม → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO point_lot_balance (point_type_id, holder_kind, holder_id, lot_key, amount)
    VALUES ('acc_pt_b', 'CUSTOMER', 'acc_cus_1', '2', '1')
  $q$, '23505', 'point_lot_balance', ARRAY['holder_id', 'holder_kind', 'lot_key', 'point_type_id']));

-- =====================================================================
-- T11 · จำนวนติดลบ ศูนย์ หรือไม่ใช่ตัวเลขฐานสิบ เข้าไม่ได้           RFD 14
-- =====================================================================
SELECT pg_temp.rec_checks('T11', 'ยอดติดลบ ศูนย์ หรือไม่ใช่ตัวเลข → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO reservation (id, operation_id, subject_type, subject_id, amount)
    VALUES ('acc_rs_z', 'acc_op_2', 'COUPON_UNIT', 'acc_coupon_z', '0')
  $q$, '23514', 'reservation', ARRAY['amount']),
  pg_temp.try_reject($q$
    INSERT INTO reservation (id, operation_id, subject_type, subject_id, amount)
    VALUES ('acc_rs_n', 'acc_op_2', 'COUPON_UNIT', 'acc_coupon_n', '-5')
  $q$, '23514', 'reservation', ARRAY['amount']),
  pg_temp.try_reject($q$
    INSERT INTO reservation (id, operation_id, subject_type, subject_id, amount)
    VALUES ('acc_rs_e', 'acc_op_2', 'COUPON_UNIT', 'acc_coupon_e', '1e3')
  $q$, '23514', 'reservation', ARRAY['amount']),
  pg_temp.try_reject($q$
    INSERT INTO point_ledger_entry (id, operation_id, entry_seq, point_type_id, holder_kind,
                                    holder_id, direction, amount)
    VALUES ('acc_le_z', 'acc_op_2', 1, 'acc_pt_b', 'CUSTOMER', 'acc_cus_1', 'CREDIT', '0')
  $q$, '23514', 'point_ledger_entry', ARRAY['amount']));

-- =====================================================================
-- T12 · เก็บ uint256 สูงสุด (2^256−1) ได้ครบทุกหลัก · เกินกว่านั้นไม่ได้
-- =====================================================================
DO $$
DECLARE max text := '115792089237316195423570985008687907853269984665640564039457584007913129639935';
        over text := '115792089237316195423570985008687907853269984665640564039457584007913129639936';
        back text; r text;
BEGIN
  IF NOT pg_temp.setup('T12', 'เก็บ uint256 สูงสุดได้ครบ · 2^256 → ถูกปฏิเสธ', format($q$
    INSERT INTO point_lot_balance (point_type_id, holder_kind, holder_id, lot_key, amount)
    VALUES ('acc_pt_b', 'CUSTOMER', 'acc_cus_max', '2', %L)
  $q$, max)) THEN RETURN; END IF;
  SELECT amount INTO back FROM point_lot_balance WHERE holder_id = 'acc_cus_max';
  r := pg_temp.try_reject(format($q$
    INSERT INTO point_lot_balance (point_type_id, holder_kind, holder_id, lot_key, amount)
    VALUES ('acc_pt_b', 'CUSTOMER', 'acc_cus_over', '2', %L)
  $q$, over), '23514', 'point_lot_balance', ARRAY['amount']);
  IF back IS DISTINCT FROM max THEN r := 'FAIL: อ่านกลับมาได้ ' || back; END IF;
  PERFORM pg_temp.rec_checks('T12', 'เก็บ uint256 สูงสุดได้ครบ · 2^256 → ถูกปฏิเสธ', r);
END $$;

-- =====================================================================
-- T13 · โอน 243 คน = 3 step (100 + 100 + 43) · step ที่เกิน 100 คน → ถูกปฏิเสธ
-- =====================================================================
DO $$
DECLARE r text;
BEGIN
  INSERT INTO operation_step (id, operation_id, step_index, name, recipient_count) VALUES
    ('acc_st_1', 'acc_op_2', 1, 'batch-1', 100),
    ('acc_st_2', 'acc_op_2', 2, 'batch-2', 100),
    ('acc_st_3', 'acc_op_2', 3, 'batch-3', 43);
  r := pg_temp.try_reject($q$
    INSERT INTO operation_step (id, operation_id, step_index, name, recipient_count)
    VALUES ('acc_st_big', 'acc_op_2', 4, 'batch-big', 101)
  $q$, '23514', 'operation_step', ARRAY['recipient_count']);
  IF (SELECT sum(recipient_count) FROM operation_step WHERE operation_id = 'acc_op_2') <> 243 THEN
    r := 'FAIL: รวมผู้รับไม่ได้ 243';
  END IF;
  PERFORM pg_temp.rec_checks('T13', 'โอน 243 คน = 3 step · step ที่เกิน 100 คน → ถูกปฏิเสธ', r);
END $$;

-- =====================================================================
-- T14 · ห้าม broadcast ก่อน raw_tx ลงตาราง                   RFD 2 R2
-- T15 · nonce ของ signer ตัวหนึ่งถูกใช้ซ้ำไม่ได้               RFD 4 §3.6
-- =====================================================================
SELECT pg_temp.rec_checks('T14', 'เปลี่ยน step เป็น SUBMITTED โดยไม่มี raw_tx → ถูกปฏิเสธ',
  pg_temp.try_reject($q$ UPDATE operation_step SET status = 'SUBMITTED' WHERE id = 'acc_st_1' $q$,
    '23514', 'operation_step', ARRAY['raw_tx', 'status']));

INSERT INTO signer (address, key_ref) VALUES ('0x' || repeat('5e', 20), 'kms/acc-signer');
UPDATE operation_step SET raw_tx = '\x02f8', signer_address = '0x' || repeat('5e', 20), nonce = 7,
       status = 'SUBMITTED', tx_hash = '0x' || repeat('51', 32)
 WHERE id = 'acc_st_1';

SELECT pg_temp.rec_checks('T15', 'ใช้ nonce เดิมของ signer ตัวเดิมซ้ำ → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    UPDATE operation_step SET raw_tx = '\x02f8', signer_address = '0x' || repeat('5e', 20),
           nonce = 7, status = 'SUBMITTED', tx_hash = '0x' || repeat('52', 32)
     WHERE id = 'acc_st_2'
  $q$, '23505', 'operation_step', ARRAY['nonce', 'signer_address']));

-- =====================================================================
-- T16 · บัญชีคู่ · DEBIT = CREDIT ต่อ operation · ตรวจตอน COMMIT
--       ต้นฉบับแค่ query ตรวจ · ที่นี่ constraint trigger แบบ deferred ปฏิเสธจริง
-- T16b · แถวบัญชีที่อ้างล็อต ต้องเป็นล็อตที่มีอยู่จริง                 (เพิ่ม)
-- =====================================================================
DO $$
DECLARE r text;
BEGIN
  -- สมดุล · ร้านแจกจากคลัง 300 ให้ลูกค้าสองคน
  INSERT INTO point_ledger_entry (id, operation_id, entry_seq, point_type_id, holder_kind,
                                  holder_id, lot_key, direction, amount) VALUES
    ('acc_le_1', 'acc_op_2', 1, 'acc_pt_b', 'INVENTORY', 'acc_mch', NULL, 'DEBIT', '300'),
    ('acc_le_2', 'acc_op_2', 2, 'acc_pt_b', 'CUSTOMER', 'acc_cus_1', '2', 'CREDIT', '200'),
    ('acc_le_3', 'acc_op_2', 3, 'acc_pt_b', 'CUSTOMER', 'acc_cus_2', '2', 'CREDIT', '100');
  SET CONSTRAINTS ALL IMMEDIATE;
  SET CONSTRAINTS ALL DEFERRED;
  -- ไม่สมดุล · ขาดไป 1
  r := pg_temp.try_reject($q$
    INSERT INTO point_ledger_entry (id, operation_id, entry_seq, point_type_id, holder_kind,
                                    holder_id, lot_key, direction, amount)
    VALUES ('acc_le_4', 'acc_op_2', 4, 'acc_pt_b', 'CUSTOMER', 'acc_cus_3', '2', 'CREDIT', '1');
    SET CONSTRAINTS ALL IMMEDIATE
  $q$, '23514', 'point_ledger_entry', NULL, ARRAY['trg_point_ledger_balanced']);
  SET CONSTRAINTS ALL DEFERRED;
  PERFORM pg_temp.rec_checks('T16', 'บัญชีแต้ม DEBIT = CREDIT ต่อ operation · ไม่สมดุลถูกปฏิเสธตอน commit', r);
EXCEPTION WHEN OTHERS THEN
  PERFORM pg_temp.rec('T16', 'บัญชีแต้ม DEBIT = CREDIT ต่อ operation · ไม่สมดุลถูกปฏิเสธตอน commit',
                      'ERROR', SQLSTATE || ' · ' || SQLERRM);
END $$;

SELECT pg_temp.rec_checks('T16b', 'แถวบัญชีอ้างล็อตที่ไม่มีอยู่ → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO point_ledger_entry (id, operation_id, entry_seq, point_type_id, holder_kind,
                                    holder_id, lot_key, direction, amount)
    VALUES ('acc_le_ghost', 'acc_op_2', 9, 'acc_pt_b', 'CUSTOMER', 'acc_cus_1', '777', 'CREDIT', '1')
  $q$, '23503', 'point_ledger_entry', ARRAY['lot_key', 'point_type_id']));

-- =====================================================================
-- T17 · FEFO · ล็อตที่หมดอายุก่อนมาก่อน · วันเท่ากันใช้ lotId ที่น้อยกว่า (แบบตัวเลข)
--       contract v3: First Expiring, First Out · tie → lotId น้อยกว่า
-- =====================================================================
DO $$
DECLARE got text;
BEGIN
  IF NOT pg_temp.setup('T17', 'FEFO · หมดก่อนมาก่อน · วันเท่ากัน lotId 9 ก่อน 10', $q$
    INSERT INTO point_lot (point_type_id, lot_key, start_at, expire_at) VALUES
      ('acc_pt_b', '10', pg_temp.t0() - interval '1 day', pg_temp.t0() + interval '20 days'),
      ('acc_pt_b', '9',  pg_temp.t0() - interval '1 day', pg_temp.t0() + interval '20 days'),
      ('acc_pt_b', '11', pg_temp.t0() - interval '1 day', pg_temp.t0() + interval '3 days');
    INSERT INTO point_lot_balance (point_type_id, holder_kind, holder_id, lot_key, amount) VALUES
      ('acc_pt_b', 'CUSTOMER', 'acc_cus_fefo', '10', '1'),
      ('acc_pt_b', 'CUSTOMER', 'acc_cus_fefo', '9',  '1'),
      ('acc_pt_b', 'CUSTOMER', 'acc_cus_fefo', '11', '1');
  $q$) THEN RETURN; END IF;
  EXECUTE $q$ SELECT string_agg(lot_key, ',' ORDER BY ord)
                FROM f_points_fefo('acc_pt_b', 'CUSTOMER', 'acc_cus_fefo', $1) $q$
     INTO got USING pg_temp.t0();
  PERFORM pg_temp.rec('T17', 'FEFO · หมดก่อนมาก่อน · วันเท่ากัน lotId 9 ก่อน 10',
    CASE WHEN got = '11,9,10' THEN 'PASS' ELSE 'FAIL' END, 'ลำดับ ' || coalesce(got, '-'));
EXCEPTION WHEN undefined_function THEN
  PERFORM pg_temp.rec('T17', 'FEFO · หมดก่อนมาก่อน · วันเท่ากัน lotId 9 ก่อน 10', 'ERROR', 'ไม่มี f_points_fefo');
END $$;

-- =====================================================================
-- T18 · invariant · ไม่มีการจองที่ถูกปล่อยทั้งที่งานยังไม่จบ
-- T19 · invariant · operation ทุกแถวต้องมี outbox คู่กัน      RFD 2 D1
-- =====================================================================
DO $$
DECLARE bad int; sample text;
BEGIN
  SELECT count(*), string_agg(r.id, ',' ORDER BY r.id) INTO bad, sample
    FROM reservation r JOIN operation o ON o.id = r.operation_id
   WHERE r.released_at IS NOT NULL AND o.status NOT IN ('FINALIZED', 'CHAIN_REVERTED');
  PERFORM pg_temp.rec('T18', 'ไม่มีการปล่อยจองของงานที่ยังไม่จบ',
    CASE WHEN bad = 0 THEN 'PASS' ELSE 'FAIL' END,
    CASE WHEN bad > 0 THEN format('%s แถว: %s', bad, left(sample, 200)) END);

  SELECT count(*), string_agg(o.id, ',' ORDER BY o.id) INTO bad, sample
    FROM operation o LEFT JOIN outbox b ON b.operation_id = o.id WHERE b.id IS NULL;
  PERFORM pg_temp.rec('T19', 'ไม่มี operation ที่ไม่มีใครมาทำ (orphan = 0)',
    CASE WHEN bad = 0 THEN 'PASS' ELSE 'FAIL' END,
    CASE WHEN bad > 0 THEN format('orphan %s แถว: %s', bad, left(sample, 200)) END);
END $$;

-- =====================================================================
-- T20 · ไม่มีคอลัมน์ยอดคงเหลือสำเร็จรูป · ยอดที่ใช้ได้คิดตอน query      RFD 10
--       ตรวจตารางล็อต · และ point_balance_bucket แบบ epoch ต้องไม่อยู่แล้ว
-- =====================================================================
DO $$
DECLARE bad text; has_bucket boolean; has_lot boolean;
BEGIN
  SELECT string_agg(table_name || '.' || column_name, ',') INTO bad FROM information_schema.columns
   WHERE table_schema = 'public' AND table_name IN ('point_lot', 'point_lot_balance')
     AND column_name ~* '(available|balance|total|remaining)';
  has_bucket := to_regclass('public.point_balance_bucket') IS NOT NULL;
  has_lot := to_regclass('public.point_lot_balance') IS NOT NULL;
  PERFORM pg_temp.rec('T20', 'ไม่มีคอลัมน์ยอดคงเหลือสำเร็จรูป · คิดตอน query เท่านั้น',
    CASE WHEN bad IS NULL AND has_lot AND NOT has_bucket THEN 'PASS' ELSE 'FAIL' END,
    concat_ws(' · ', CASE WHEN bad IS NOT NULL THEN 'คอลัมน์: ' || bad END,
                     CASE WHEN NOT has_lot THEN 'ยังไม่มี point_lot_balance' END,
                     CASE WHEN has_bucket THEN 'point_balance_bucket (epoch) ยังอยู่' END));
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
