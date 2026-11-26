-- =====================================================================
-- FEAT-04 · Create Point Type · acceptance สำหรับ blockchain-reward-platform-be
--
-- ต้นฉบับ : blockchain-reward-platform/reference/feat-04/tests/acceptance.sql (17 ข้อ)
-- schema  : migrations 0002 → 0006_feat04_point_type
--
-- รัน (Postgres ใน docker):
--   docker exec -i blockchain-reward-platform-be-postgres-1 \
--     psql -U reward -d reward < tests/sql/feat04_acceptance.sql
--
-- ผล  PASS ฐานข้อมูลบังคับกฎนี้ได้จริง · FAIL ไม่ได้ หรือถูกปฏิเสธด้วยกฎตัวอื่น
--     ERROR test เองรันไม่ได้ · ดูหมายเหตุ
--
-- ต่างจากต้นฉบับ · หลักฐานอยู่ใน docs/reference-findings.md
--   1. contract ที่ส่งมอบคือ NewPointToken v3 ไม่ใช่ token แบบ epoch ที่ pack สมมติ
--      · T4   decimals ต้องเป็น 18 (deployPointToken ไม่รับ decimals) แทนช่วง 0–30
--      · T11  การตั้งค่า business A/B/C/D ต้องครบและสอดคล้อง แทน epoch_seconds > 0
--      · T15  LIVE ต้องมีแถวฝั่ง chain ที่ configure แล้ว (Draft ใช้งานไม่ได้)
--      · T15b เพิ่ม · การตั้งค่าบน chain ต้องตรงกับที่ร้านขอ
--   2. trigger ของต้นฉบับลืม validity_epochs · ที่นี่ทุกคอลัมน์ที่มาจาก chain แก้ไม่ได้
--      · T9b เพิ่ม · deployed_block และค่าที่ configure แล้วก็แก้ไม่ได้
--   3. T6 ใช้คอลัมน์ name_normalized + CHECK แทน expression index · T6 ตรวจทั้งคู่
--   4. hash/address เป็น hex string ('0x…') · ข้อมูลทดสอบขึ้นต้นด้วย acc_
--   5. ข้อ "ต้องถูกปฏิเสธ" ตรวจว่าชนกฎที่ตั้งใจทดสอบจริง และย้อนแถวทิ้งถ้าไม่ถูกห้าม
--   6. T15 T15b T16 ตรวจทั้งตาราง รวมข้อมูลจริง · FAIL อาจมาจากข้อมูลเก่า ดูหมายเหตุ
--
-- ทั้งไฟล์อยู่ใน transaction เดียวแล้ว ROLLBACK · รันซ้ำกี่รอบก็ได้
-- =====================================================================
\set ON_ERROR_STOP on
\pset format aligned

BEGIN;

-- ควรได้ 0006_feat04_point_type ขึ้นไป
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


-- point type แบบ B (ล็อต · FEFO) · name_normalized เขียนด้วย SQL แบบเดียวกับที่โค้ดต้องทำ
CREATE FUNCTION pg_temp.mk_pt(p_id text, p_merchant text, p_name text, p_symbol text,
                              p_status text DEFAULT 'REQUESTED')
RETURNS void LANGUAGE sql AS $$
  INSERT INTO point_type (id, merchant_id, name, name_normalized, symbol, status,
                          business_model, lot_policy)
  VALUES (p_id, p_merchant, p_name, lower(trim(p_name)), p_symbol, p_status, 2, 'FEFO_ONLY')
$$;

-- ------------------------------------------------------------ fixture
INSERT INTO merchant (id, name, status, activated_at) VALUES ('acc_mch_ok', 'ร้านพร้อม', 'ACTIVE', now());
INSERT INTO merchant (id, name) VALUES ('acc_mch_pending', 'ร้านรอ provision');

SELECT pg_temp.mk_pt('acc_pt_1', 'acc_mch_ok', 'Happy Points', 'HAPPY');

-- =====================================================================
-- T1 · ส่ง Idempotency-Key เดิมซ้ำ → ไม่เกิดงานใหม่            RFD 3 R1
-- =====================================================================
DO $$
DECLARE n1 int; n2 int;
BEGIN
  INSERT INTO idempotency_key (id, scope, key, fingerprint, expires_at)
  VALUES ('acc_ik_1', 'merchant:acc_mch_ok', 'acc-11111111-2222-3333', pg_temp.h('body'),
          now() + interval '72 hours')
  ON CONFLICT (scope, key) DO NOTHING;
  GET DIAGNOSTICS n1 = ROW_COUNT;
  INSERT INTO idempotency_key (id, scope, key, fingerprint, expires_at)
  VALUES ('acc_ik_2', 'merchant:acc_mch_ok', 'acc-11111111-2222-3333', pg_temp.h('body'),
          now() + interval '72 hours')
  ON CONFLICT (scope, key) DO NOTHING;
  GET DIAGNOSTICS n2 = ROW_COUNT;
  PERFORM pg_temp.rec('T1', 'สร้าง point type ซ้ำด้วย key เดิม → แถวที่สองได้ 0 rows',
                      CASE WHEN n1 = 1 AND n2 = 0 THEN 'PASS' ELSE 'FAIL' END);
EXCEPTION WHEN OTHERS THEN
  PERFORM pg_temp.rec('T1', 'สร้าง point type ซ้ำด้วย key เดิม → แถวที่สองได้ 0 rows',
                      'ERROR', SQLSTATE || ' · ' || SQLERRM);
END $$;

-- =====================================================================
-- T2 · ร้านที่ยังไม่ ACTIVE สร้าง point type ไม่ได้               FEAT-01 §3.4
-- =====================================================================
SELECT pg_temp.rec_checks('T2', 'ร้านที่ status ยังไม่ ACTIVE สร้าง point type ไม่ได้',
  pg_temp.try_reject($q$ SELECT pg_temp.mk_pt('acc_pt_t2', 'acc_mch_pending', 'แต้มรอ', 'WAIT') $q$,
    '23514', 'point_type', NULL, ARRAY['trg_point_type_merchant_active']));

-- =====================================================================
-- T3 · symbol ต้องเป็นตัวใหญ่ล้วน 2–11 ตัว · ตัวเล็ก · สั้นไป · มีช่องว่าง
-- =====================================================================
SELECT pg_temp.rec_checks('T3', 'symbol ที่เป็นตัวเล็ก สั้นไป หรือมีช่องว่าง → ถูกปฏิเสธ',
  pg_temp.try_reject($q$ SELECT pg_temp.mk_pt('acc_pt_t3a', 'acc_mch_ok', 'แต้ม a', 'abc') $q$,
    '23514', 'point_type', ARRAY['symbol']),
  pg_temp.try_reject($q$ SELECT pg_temp.mk_pt('acc_pt_t3b', 'acc_mch_ok', 'แต้ม b', 'A') $q$,
    '23514', 'point_type', ARRAY['symbol']),
  pg_temp.try_reject($q$ SELECT pg_temp.mk_pt('acc_pt_t3c', 'acc_mch_ok', 'แต้ม c', 'AB C') $q$,
    '23514', 'point_type', ARRAY['symbol']));

-- =====================================================================
-- T4 · decimals ต้องเป็น 18                                    contract v3 · RFD 14
--      ต้นฉบับยอม 0–30 · deployPointToken(name, symbol) ไม่รับ decimals และ token มี 18 เสมอ
-- =====================================================================
SELECT pg_temp.rec_checks('T4', 'decimals ต้องเป็น 18 (contract v3) · 31 · -1 · 2 ถูกปฏิเสธ',
  pg_temp.try_reject($q$ UPDATE point_type SET decimals = 31 WHERE id = 'acc_pt_1' $q$,
    '23514', 'point_type', ARRAY['decimals']),
  pg_temp.try_reject($q$ UPDATE point_type SET decimals = -1 WHERE id = 'acc_pt_1' $q$,
    '23514', 'point_type', ARRAY['decimals']),
  pg_temp.try_reject($q$ UPDATE point_type SET decimals = 2 WHERE id = 'acc_pt_1' $q$,
    '23514', 'point_type', ARRAY['decimals']));

-- =====================================================================
-- T5 · ร้านเดียวกันใช้ symbol ซ้ำไม่ได้
-- =====================================================================
SELECT pg_temp.rec_checks('T5', 'ร้านเดียวกันใช้ symbol ซ้ำ → ถูกปฏิเสธ',
  pg_temp.try_reject($q$ SELECT pg_temp.mk_pt('acc_pt_t5', 'acc_mch_ok', 'อีกชื่อ', 'HAPPY') $q$,
    '23505', 'point_type', ARRAY['merchant_id', 'symbol']));

-- =====================================================================
-- T6 · ชื่อซ้ำแบบไม่สนตัวพิมพ์และช่องว่างหน้าหลัง ก็ไม่ได้
--      และ name_normalized ต้องคำนวณจากชื่อจริง · ใส่ค่าอื่นเพื่อหลบ unique ไม่ได้
-- =====================================================================
SELECT pg_temp.rec_checks('T6', 'ชื่อเดียวกันที่มีช่องว่างหน้าหลัง/ต่างตัวพิมพ์ → ถูกปฏิเสธ',
  pg_temp.try_reject($q$ SELECT pg_temp.mk_pt('acc_pt_t6', 'acc_mch_ok', '  HAPPY points ', 'HAPPY2') $q$,
    '23505', 'point_type', ARRAY['merchant_id', 'name_normalized']),
  pg_temp.try_reject($q$
    INSERT INTO point_type (id, merchant_id, name, name_normalized, symbol, business_model, lot_policy)
    VALUES ('acc_pt_t6b', 'acc_mch_ok', 'Happy Points', 'happy points (2)', 'HAPPY3', 2, 'FEFO_ONLY')
  $q$, '23514', 'point_type', ARRAY['name', 'name_normalized']));

-- =====================================================================
-- T7 · deploy ล้มแล้ว ชื่อและ symbol ถูกปล่อยคืนให้ใช้ใหม่ได้
-- =====================================================================
DO $$
BEGIN
  PERFORM pg_temp.mk_pt('acc_pt_fail', 'acc_mch_ok', 'แต้มที่ล้ม', 'FAILP', 'FAILED');
  PERFORM pg_temp.mk_pt('acc_pt_retry', 'acc_mch_ok', 'แต้มที่ล้ม', 'FAILP');
  PERFORM pg_temp.rec('T7', 'deploy ล้มแล้วใช้ชื่อและ symbol เดิมสร้างใหม่ได้', 'PASS');
EXCEPTION WHEN OTHERS THEN
  PERFORM pg_temp.rec('T7', 'deploy ล้มแล้วใช้ชื่อและ symbol เดิมสร้างใหม่ได้', 'FAIL',
                      SQLSTATE || ' · ' || SQLERRM);
END $$;

-- =====================================================================
-- T8 · point type หนึ่งอันมีงาน deploy ค้างทีละงานเดียว
-- =====================================================================
INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
VALUES ('acc_op_1', 'merchant:acc_mch_ok', 'POINT_TYPE_CREATE', 'acc_pt_1', '{}');
INSERT INTO outbox (id, operation_id, topic, payload)
VALUES ('acc_obx_1', 'acc_op_1', 'point.type.create.v1', '{}');

SELECT pg_temp.rec_checks('T8', 'สั่ง deploy point type เดิมซ้อนกัน → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
    VALUES ('acc_op_2', 'merchant:acc_mch_ok', 'POINT_TYPE_CREATE', 'acc_pt_1', '{}')
  $q$, '23505', 'operation', ARRAY['subject_id']));

-- =====================================================================
-- T9 · ค่าที่มาจาก chain แก้ไม่ได้หลังถูกเขียน
--      deploy ใหม่ต้องเป็น point_type แถวใหม่ ไม่ใช่แก้แถวเดิม
-- =====================================================================
INSERT INTO point_type_chain (point_type_id, contract_address, deployed_block, deployed_tx_hash)
VALUES ('acc_pt_1', '0x' || repeat('aa', 20), 1001, '0x' || repeat('ab', 32));

SELECT pg_temp.rec_checks('T9', 'แก้ contract_address ของ point type ที่ deploy แล้ว → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    UPDATE point_type_chain SET contract_address = '0x' || repeat('bb', 20)
     WHERE point_type_id = 'acc_pt_1'
  $q$, '23514', 'point_type_chain', NULL, ARRAY['trg_point_chain_immutable']));

-- =====================================================================
-- T9b · ทุกคอลัมน์ที่มาจาก chain แก้ไม่ได้ · ไม่ใช่แค่ address          (เพิ่ม)
--       ต้นฉบับลืม validity_epochs · ค่าที่ทำให้วันหมดอายุทุกก้อนเปลี่ยน
--       configure เป็นอีก transaction · จาก NULL เป็นค่าได้ครั้งเดียว แล้วแก้ไม่ได้
-- =====================================================================
DO $$
DECLARE r1 text; r2 text;
BEGIN
  r1 := pg_temp.try_reject($q$
    UPDATE point_type_chain SET deployed_block = 1002 WHERE point_type_id = 'acc_pt_1'
  $q$, '23514', 'point_type_chain', NULL, ARRAY['trg_point_chain_immutable']);

  -- BusinessConfigured(2, abi.encode(uint8 0)) · B · FEFO_ONLY
  UPDATE point_type_chain
     SET business_model = 2, lot_policy = 'FEFO_ONLY',
         configured_block = 1003, configured_tx_hash = '0x' || repeat('ac', 32)
   WHERE point_type_id = 'acc_pt_1';

  r2 := pg_temp.try_reject($q$
    UPDATE point_type_chain SET lot_policy = 'USER_SELECT', business_model = 3
     WHERE point_type_id = 'acc_pt_1'
  $q$, '23514', 'point_type_chain', NULL, ARRAY['trg_point_chain_immutable']);

  PERFORM pg_temp.rec_checks('T9b', 'ค่าจาก chain ทุกคอลัมน์ (รวมค่าที่ configure แล้ว) แก้ไม่ได้', r1, r2);
EXCEPTION WHEN OTHERS THEN
  PERFORM pg_temp.rec('T9b', 'ค่าจาก chain ทุกคอลัมน์ (รวมค่าที่ configure แล้ว) แก้ไม่ได้',
                      'ERROR', 'configure ครั้งแรกไม่ผ่าน · ' || SQLSTATE || ' · ' || SQLERRM);
END $$;

-- =====================================================================
-- T10 · contract address เดียวกันผูกกับ point type สองอันไม่ได้
-- =====================================================================
SELECT pg_temp.rec_checks('T10', 'contract address เดียวกันผูกกับสอง point type → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO point_type_chain (point_type_id, contract_address, deployed_block, deployed_tx_hash)
    VALUES ('acc_pt_retry', '0x' || repeat('aa', 20), 1010, '0x' || repeat('cd', 32))
  $q$, '23505', 'point_type_chain', ARRAY['contract_address']));

-- =====================================================================
-- T11 · การตั้งค่า business ต้องครบและสอดคล้องกับแบบที่เลือก        contract v3
--       แทน "epoch_seconds > 0" ของต้นฉบับ
--       A ต้องมีวันเริ่มและวันหมดอายุ (หมดหลังเริ่ม) · B = FEFO_ONLY · C = USER_SELECT
--       D ต้องมี policy · ฝั่ง chain: configure แล้วต้องมี block และ tx ครบ
-- =====================================================================
SELECT pg_temp.rec_checks('T11', 'การตั้งค่า A/B/C/D ไม่ครบหรือขัดกัน → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    UPDATE point_type SET business_model = 1, lot_policy = NULL WHERE id = 'acc_pt_1'
  $q$, '23514', 'point_type', ARRAY['business_model', 'program_expire_at', 'program_start_at']),
  pg_temp.try_reject($q$
    UPDATE point_type SET business_model = 1, lot_policy = NULL,
           program_start_at = now(), program_expire_at = now() - interval '1 day'
     WHERE id = 'acc_pt_1'
  $q$, '23514', 'point_type', ARRAY['business_model', 'program_expire_at', 'program_start_at']),
  pg_temp.try_reject($q$
    UPDATE point_type SET lot_policy = 'USER_SELECT' WHERE id = 'acc_pt_1'
  $q$, '23514', 'point_type', ARRAY['business_model', 'lot_policy']),
  pg_temp.try_reject($q$
    UPDATE point_type SET business_model = 4, lot_policy = NULL WHERE id = 'acc_pt_1'
  $q$, '23514', 'point_type', ARRAY['business_model', 'lot_policy']),
  pg_temp.try_reject($q$
    INSERT INTO point_type_chain (point_type_id, contract_address, deployed_block, deployed_tx_hash,
                                  business_model, lot_policy)
    VALUES ('acc_pt_retry', '0x' || repeat('a2', 20), 1010, '0x' || repeat('ce', 32), 2, 'FEFO_ONLY')
  $q$, '23514', 'point_type_chain', ARRAY['business_model', 'configured_block']));

-- =====================================================================
-- T12 · ห้าม broadcast ก่อน raw_tx ลงตาราง                   RFD 2 R2
-- =====================================================================
INSERT INTO operation_step (id, operation_id, step_index, name)
VALUES ('acc_st_1', 'acc_op_1', 1, 'deployPointToken');

SELECT pg_temp.rec_checks('T12', 'เปลี่ยน step เป็น SUBMITTED โดยไม่มี raw_tx → ถูกปฏิเสธ',
  pg_temp.try_reject($q$ UPDATE operation_step SET status = 'SUBMITTED' WHERE id = 'acc_st_1' $q$,
    '23514', 'operation_step', ARRAY['raw_tx', 'status']));

-- =====================================================================
-- T13 · topic ของ outbox ต้องมีเวอร์ชันในชื่อ                RFD 7 D9
-- =====================================================================
SELECT pg_temp.rec_checks('T13', 'topic ที่ไม่มีเวอร์ชันต่อท้าย → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO outbox (id, operation_id, topic, payload)
    VALUES ('acc_obx_bad', 'acc_op_1', 'point.type.create', '{}')
  $q$, '23514', 'outbox', ARRAY['topic']));

-- =====================================================================
-- T14 · อ่าน log ช่วงเดิมซ้ำได้                              RFD 5 D2
-- =====================================================================
DO $$
DECLARE n1 int; n2 int;
BEGIN
  INSERT INTO raw_block (block_hash, block_number, parent_hash, "timestamp")
  VALUES ('0x' || repeat('b4', 32), 1001, '0x' || repeat('b3', 32), now());
  INSERT INTO chain_event (id, block_number, block_hash, tx_hash, log_index, address,
                           topic0, topics, data, block_timestamp)
  VALUES ('acc_ce_1', 1001, '0x' || repeat('b4', 32), '0x' || repeat('ab', 32), 0,
          '0x' || repeat('aa', 20), '0x' || repeat('00', 32), '[]', '\x00', now())
  ON CONFLICT (tx_hash, log_index) DO NOTHING;
  GET DIAGNOSTICS n1 = ROW_COUNT;
  INSERT INTO chain_event (id, block_number, block_hash, tx_hash, log_index, address,
                           topic0, topics, data, block_timestamp)
  VALUES ('acc_ce_2', 1001, '0x' || repeat('b4', 32), '0x' || repeat('ab', 32), 0,
          '0x' || repeat('aa', 20), '0x' || repeat('00', 32), '[]', '\x00', now())
  ON CONFLICT (tx_hash, log_index) DO NOTHING;
  GET DIAGNOSTICS n2 = ROW_COUNT;
  PERFORM pg_temp.rec('T14', 'อ่าน log ช่วงเดิมซ้ำ → chain_event ไม่เกิดแถวซ้ำ',
                      CASE WHEN n1 = 1 AND n2 = 0 THEN 'PASS' ELSE 'FAIL' END);
EXCEPTION WHEN OTHERS THEN
  PERFORM pg_temp.rec('T14', 'อ่าน log ช่วงเดิมซ้ำ → chain_event ไม่เกิดแถวซ้ำ',
                      'ERROR', SQLSTATE || ' · ' || SQLERRM);
END $$;

-- =====================================================================
-- T15 · invariant · point type ที่ LIVE ต้องมี contract ที่ configure แล้ว
--       token ที่ยัง Draft (ยังไม่ configureBusiness) ออกแต้มไม่ได้
-- T15b · invariant · การตั้งค่าบน chain ต้องตรงกับที่ร้านขอ           (เพิ่ม)
-- =====================================================================
UPDATE point_type SET status = 'LIVE', live_at = now() WHERE id = 'acc_pt_1';

DO $$
DECLARE bad int; sample text;
BEGIN
  SELECT count(*), string_agg(p.id, ',' ORDER BY p.id) INTO bad, sample
    FROM point_type p LEFT JOIN point_type_chain c ON c.point_type_id = p.id
   WHERE p.status = 'LIVE' AND (c.point_type_id IS NULL OR c.business_model IS NULL);
  PERFORM pg_temp.rec('T15', 'ไม่มี point type ที่ LIVE แต่ยังไม่มี contract ที่ configure แล้ว',
    CASE WHEN bad = 0 THEN 'PASS' ELSE 'FAIL' END,
    CASE WHEN bad > 0 THEN format('%s แถว: %s', bad, left(sample, 200)) END);

  SELECT count(*), string_agg(p.id, ',' ORDER BY p.id) INTO bad, sample
    FROM point_type p JOIN point_type_chain c ON c.point_type_id = p.id
   WHERE c.business_model IS NOT NULL
     AND (c.business_model, c.program_start_at, c.program_expire_at, c.lot_policy)
         IS DISTINCT FROM (p.business_model, p.program_start_at, p.program_expire_at, p.lot_policy);
  PERFORM pg_temp.rec('T15b', 'การตั้งค่าบน chain ตรงกับที่ร้านขอ',
    CASE WHEN bad = 0 THEN 'PASS' ELSE 'FAIL' END,
    CASE WHEN bad > 0 THEN format('%s แถว: %s', bad, left(sample, 200)) END);
END $$;

-- =====================================================================
-- T16 · invariant · operation ทุกแถวต้องมี outbox คู่กัน      RFD 2 D1
-- =====================================================================
DO $$
DECLARE orphan int; sample text;
BEGIN
  SELECT count(*), string_agg(o.id, ',' ORDER BY o.id) INTO orphan, sample
    FROM operation o LEFT JOIN outbox b ON b.operation_id = o.id
   WHERE b.id IS NULL;
  PERFORM pg_temp.rec('T16', 'ไม่มี operation ที่ไม่มีใครมาทำ (orphan = 0)',
    CASE WHEN orphan = 0 THEN 'PASS' ELSE 'FAIL' END,
    CASE WHEN orphan > 0 THEN format('orphan %s แถว: %s', orphan, left(sample, 200)) END);
END $$;

-- =====================================================================
-- T17 · point_type_chain rebuild ได้ · ไม่มีคอลัมน์ที่มาจากร้าน     RFD 6 §3.2
-- =====================================================================
DO $$
DECLARE bad text;
BEGIN
  SELECT string_agg(column_name::text, ',') INTO bad FROM information_schema.columns
   WHERE table_schema = 'public' AND table_name = 'point_type_chain'
     AND column_name IN ('name', 'name_normalized', 'symbol', 'decimals', 'status', 'merchant_id');
  PERFORM pg_temp.rec('T17', 'point_type_chain ไม่มีคอลัมน์ที่มาจากร้าน · replay ได้ทั้งตาราง',
    CASE WHEN bad IS NULL THEN 'PASS' ELSE 'FAIL' END,
    CASE WHEN bad IS NOT NULL THEN 'คอลัมน์ที่มาจากร้าน: ' || bad END);
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
