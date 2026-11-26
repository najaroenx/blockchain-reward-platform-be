-- =====================================================================
-- FEAT-07 · Create Coupon Type + Issue Codes · acceptance สำหรับ blockchain-reward-platform-be
--
-- ต้นฉบับ : blockchain-reward-platform/reference/feat-07/tests/acceptance.sql (23 ข้อ)
-- schema  : migrations 0002 → 0010_feat07_coupon_type
--
-- รัน (Postgres ใน docker):
--   docker exec -i blockchain-reward-platform-be-postgres-1 \
--     psql -U reward -d reward < tests/sql/feat07_acceptance.sql
--
-- ผล  PASS ฐานข้อมูลบังคับกฎนี้ได้จริง · FAIL ไม่ได้ หรือถูกปฏิเสธด้วยกฎตัวอื่น
--     ERROR test เองรันไม่ได้ (เช่น คอลัมน์ที่ต้องใช้ยังไม่มี) · ดูหมายเหตุ
--
-- ต่างจากต้นฉบับ · หลักฐานอยู่ใน docs/reference-findings.md
--   1. hash / address เป็นสตริง hex '0x…' และ face_value / chain_type_id เป็นสตริง uint256
--      (migration 0002) · code_ref = '0x' || hex(sha256(id))
--   2. contract จริง createCouponType(name, startDate, expireDate) · coupon_type มี starts_at
--      T5 ตรวจทั้ง expires_at > created_at และ expires_at > starts_at
--   3. ชื่อซ้ำใช้คอลัมน์ name_normalized + CHECK (แบบเดียวกับ point_type ใน 0006)
--   4. supply · ต้นฉบับนับใหม่ทุกแถว (O(n²) ตอนออก 1,000,000 ใบ) และไม่ดูการเปลี่ยน VOIDED กลับ
--      ที่นี่ coupon_type.issued_count ถูกนับโดย trigger ระดับ statement · CHECK issued_count <= total_supply
--      T7 เพิ่มกรณีออกหลายใบใน statement เดียว · T8b ยกเลิก VOIDED เมื่อเต็มแล้ว → ถูกปฏิเสธ
--      T21b ตัวนับตรงกับจำนวนจริง · T21c เขียนตัวนับเองไม่ได้ · T21d ลด supply ต่ำกว่าที่ออกไปแล้วไม่ได้
--   5. T13 ตรวจทุกคอลัมน์ที่มาจาก chain ไม่ใช่แค่ chain_type_id · T13b chain_type_id ต้องเป็น uint256
--   6. T19 ตรวจทั้งค่าสูงสุดและค่าที่ต้องถูกปฏิเสธ (2^256 · ติดลบ · 1e3)
--   7. T20 นอกจากตรวจข้อมูล ยังลองตั้ง LIVE โดยไม่มีแถว chain · ต้องถูกปฏิเสธตอน commit (deferred)
--   8. ข้อมูลทดสอบขึ้นต้นด้วย acc_ · ข้อ "ต้องถูกปฏิเสธ" ตรวจว่าชนกฎที่ตั้งใจจริง
--
-- ทั้งไฟล์อยู่ใน transaction เดียวแล้ว ROLLBACK · รันซ้ำกี่รอบก็ได้
-- =====================================================================
\set ON_ERROR_STOP on
\pset format aligned

BEGIN;

-- ควรได้ 0010_feat07_coupon_type ขึ้นไป
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

-- รัน p_sql แล้วคาดว่าต้องผ่าน · คืน NULL ถ้าผ่าน · 'FAIL: …' ถ้าถูกปฏิเสธ
CREATE FUNCTION pg_temp.try_ok(p_sql text) RETURNS text LANGUAGE plpgsql AS $$
DECLARE v_state text; v_con text; v_msg text;
BEGIN
  EXECUTE p_sql;
  RETURN NULL;
EXCEPTION WHEN OTHERS THEN
  GET STACKED DIAGNOSTICS v_state = RETURNED_SQLSTATE, v_con = CONSTRAINT_NAME, v_msg = MESSAGE_TEXT;
  RETURN format('%s: ถูกปฏิเสธ %s %s · %s',
                CASE WHEN v_state IN ('23505', '23514', '23502', '23503') THEN 'FAIL' ELSE 'ERROR' END,
                v_state, coalesce(nullif(v_con, ''), ''), v_msg);
END $$;

-- sha256 เป็น hex · รูปแบบเดียวกับที่ backend เก็บ hash · code_ref = h(id)
CREATE FUNCTION pg_temp.h(p text) RETURNS text LANGUAGE sql IMMUTABLE AS
$$ SELECT '0x' || encode(sha256(convert_to(p, 'UTF8')), 'hex') $$;

CREATE FUNCTION pg_temp.has_col(p_table text, p_col text) RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT EXISTS (SELECT 1 FROM information_schema.columns
                  WHERE table_schema = current_schema() AND table_name = p_table AND column_name = p_col)
$$;

-- สร้างประเภทคูปองแบบที่ service ต้องทำ · name_normalized = lower(trim(name)) เขียนใน SQL
-- คอลัมน์ที่ migration 0010 เพิ่ม ใส่เฉพาะเมื่อมีอยู่ · ไฟล์นี้จึงรันกับ 0009 ได้ (ใช้ทำ baseline)
CREATE FUNCTION pg_temp.mk_ct(p_id text, p_merchant text, p_name text,
                              p_supply int DEFAULT 10, p_status text DEFAULT 'REQUESTED',
                              p_face text DEFAULT '1',
                              p_expires timestamptz DEFAULT now() + interval '30 days',
                              p_starts timestamptz DEFAULT NULL)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE cols text := 'id, merchant_id, name, face_value, total_supply, status, expires_at, live_at';
        vals text;
BEGIN
  vals := format('%L, %L, %L, %L, %s, %L, %L, %s', p_id, p_merchant, p_name, p_face, p_supply,
                 p_status, p_expires, CASE WHEN p_status = 'LIVE' THEN 'now()' ELSE 'NULL' END);
  IF pg_temp.has_col('coupon_type', 'name_normalized') THEN
    cols := cols || ', name_normalized';
    vals := vals || format(', lower(trim(%L))', p_name);
  END IF;
  IF p_starts IS NOT NULL THEN
    cols := cols || ', starts_at';
    vals := vals || format(', %L', p_starts);
  END IF;
  EXECUTE format('INSERT INTO coupon_type (%s) VALUES (%s)', cols, vals);
END $$;

CREATE FUNCTION pg_temp.mk_code(p_id text, p_type text, p_code text, p_batch text DEFAULT NULL)
RETURNS void LANGUAGE sql AS $$
  INSERT INTO coupon_code (id, coupon_type_id, code_hash, code_ref, issue_batch_id)
  VALUES (p_id, p_type, pg_temp.h(p_code), pg_temp.h(p_id), p_batch)
$$;

-- ------------------------------------------------------------ fixture
INSERT INTO merchant (id, name, status, activated_at) VALUES ('acc_mch_ok', 'ร้านที่พร้อมแล้ว', 'ACTIVE', now());
INSERT INTO merchant (id, name) VALUES ('acc_mch_new', 'ร้านที่เพิ่งสมัคร');
INSERT INTO signer (address, key_ref) VALUES ('0x' || repeat('3a', 20), 'kms/acc-signer-1');

-- face_value 100 THB ในหน่วย 18 ทศนิยม · supply 3
SELECT pg_temp.mk_ct('acc_ct_1', 'acc_mch_ok', 'คูปองส่วนลด 100 บาท', 3, 'LIVE',
                     '100000000000000000000', now() + interval '180 days');
INSERT INTO coupon_type_chain (coupon_type_id, contract_address, chain_type_id, deployed_block, deployed_tx_hash)
VALUES ('acc_ct_1', '0x' || repeat('a7', 20), '7', 1001, '0x' || repeat('ab', 32));

INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
VALUES ('acc_op_1', 'merchant:acc_mch_ok', 'COUPON_ISSUE', 'acc_ct_1', '{}');
INSERT INTO outbox (id, operation_id, topic, payload)
VALUES ('acc_obx_1', 'acc_op_1', 'coupon.issue.v1', '{}');
INSERT INTO issue_batch (id, coupon_type_id, operation_id, quantity)
VALUES ('acc_ib_1', 'acc_ct_1', 'acc_op_1', 3);

-- =====================================================================
-- T1 · ส่ง Idempotency-Key เดิมซ้ำ → ไม่เกิดงานใหม่            RFD 3 R1
-- =====================================================================
DO $$
DECLARE n1 int; n2 int;
BEGIN
  INSERT INTO idempotency_key (id, scope, key, fingerprint, expires_at)
  VALUES ('acc_ik_1', 'merchant:acc_mch_ok', '11111111-2222-3333-4444-555555555555',
          pg_temp.h('b'), now() + interval '1 day')
  ON CONFLICT (scope, key) DO NOTHING;
  GET DIAGNOSTICS n1 = ROW_COUNT;
  INSERT INTO idempotency_key (id, scope, key, fingerprint, expires_at)
  VALUES ('acc_ik_2', 'merchant:acc_mch_ok', '11111111-2222-3333-4444-555555555555',
          pg_temp.h('b'), now() + interval '1 day')
  ON CONFLICT (scope, key) DO NOTHING;
  GET DIAGNOSTICS n2 = ROW_COUNT;
  PERFORM pg_temp.rec('T1', 'ออกคูปองซ้ำด้วย key เดิม → แถวที่สองได้ 0 rows',
                      CASE WHEN n1 = 1 AND n2 = 0 THEN 'PASS' ELSE 'FAIL' END,
                      format('ได้ %s · %s', n1, n2));
END $$;

-- =====================================================================
-- T2 · ร้านที่ยังไม่ ACTIVE สร้างประเภทคูปองไม่ได้ · บังคับด้วย trigger
-- =====================================================================
SELECT pg_temp.rec_checks('T2', 'ร้านที่ status ยังไม่ ACTIVE สร้างประเภทคูปองไม่ได้',
  pg_temp.try_reject($q$SELECT pg_temp.mk_ct('acc_ct_bad', 'acc_mch_new', 'คูปองร้านใหม่')$q$,
    '23514', 'coupon_type', NULL, ARRAY['trg_coupon_type_merchant_active']));

-- =====================================================================
-- T3 · ชื่อซ้ำในร้านเดียวไม่ได้ · ไม่สนตัวพิมพ์และช่องว่างหน้าหลัง
--      และ name_normalized ต้องคำนวณจากชื่อจริง · ใส่ค่าอื่นเพื่อหลบ unique ไม่ได้
-- =====================================================================
SELECT pg_temp.rec_checks('T3', 'ชื่อประเภทคูปองซ้ำในร้านเดียว (ต่างแค่ตัวพิมพ์/ช่องว่าง) → ถูกปฏิเสธ',
  pg_temp.try_reject($q$SELECT pg_temp.mk_ct('acc_ct_dup', 'acc_mch_ok', '  คูปองส่วนลด 100 บาท ')$q$,
    '23505', 'coupon_type', ARRAY['merchant_id', 'name_normalized'], ARRAY['uq_coupon_type_name']),
  pg_temp.try_reject($q$
    INSERT INTO coupon_type (id, merchant_id, name, name_normalized, face_value, total_supply, expires_at)
    VALUES ('acc_ct_dup2', 'acc_mch_ok', 'คูปองส่วนลด 100 บาท', 'ชื่ออื่น', '1', 10, now() + interval '30 days')
  $q$, '23514', 'coupon_type', NULL, ARRAY['ck_coupon_type_name_normalized']));

-- =====================================================================
-- T4 · deploy ล้มแล้วชื่อถูกปล่อยคืน · partial index ครอบเฉพาะที่ไม่ FAILED
-- =====================================================================
SELECT pg_temp.rec_checks('T4', 'deploy ล้มแล้วใช้ชื่อเดิมสร้างใหม่ได้',
  pg_temp.try_ok($q$SELECT pg_temp.mk_ct('acc_ct_fail', 'acc_mch_ok', 'คูปองที่ล้ม', 10, 'FAILED')$q$),
  pg_temp.try_ok($q$SELECT pg_temp.mk_ct('acc_ct_retry', 'acc_mch_ok', 'คูปองที่ล้ม')$q$));

-- =====================================================================
-- T5 · วันหมดอายุต้องอยู่หลังวันสร้างและวันเริ่ม · supply ต้องเป็นบวก
-- =====================================================================
SELECT pg_temp.rec_checks('T5', 'หมดอายุก่อนวันสร้าง · หมดอายุก่อนวันเริ่ม · supply 0 → ถูกปฏิเสธ',
  pg_temp.try_reject($q$SELECT pg_temp.mk_ct('acc_ct_x1', 'acc_mch_ok', 'ก', 10, 'REQUESTED', '1',
                                             now() - interval '1 day', now() - interval '2 days')$q$,
    '23514', 'coupon_type', NULL, ARRAY['ck_coupon_type_expiry']),
  pg_temp.try_reject($q$SELECT pg_temp.mk_ct('acc_ct_x2', 'acc_mch_ok', 'ข', 10, 'REQUESTED', '1',
                                             now() + interval '30 days', now() + interval '60 days')$q$,
    '23514', 'coupon_type', NULL, ARRAY['ck_coupon_type_window']),
  pg_temp.try_reject($q$SELECT pg_temp.mk_ct('acc_ct_x3', 'acc_mch_ok', 'ค', 0)$q$,
    '23514', 'coupon_type', NULL, ARRAY['ck_coupon_type_supply_range']));

-- =====================================================================
-- T6 · code_ref ต้องเท่ากับ sha256(id) เสมอ                 RFD 11 §3.3
--      projector คำนวณ code_ref จาก id เองได้ โดยไม่ต้องรู้ plaintext
-- =====================================================================
SELECT pg_temp.rec_checks('T6', 'code_ref ที่ไม่ใช่ sha256(id) → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO coupon_code (id, coupon_type_id, code_hash, code_ref, issue_batch_id)
    VALUES ('acc_cc_bad', 'acc_ct_1', pg_temp.h('CODE-BAD'), pg_temp.h('something-else'), 'acc_ib_1')
  $q$, '23514', 'coupon_code', NULL, ARRAY['ck_code_ref_derived']));

-- =====================================================================
-- T7 · ออกรหัสได้ตามจำนวนที่ประกาศ · เกินไม่ได้ ทั้งทีละใบและหลายใบใน statement เดียว
-- =====================================================================
DO $$
DECLARE r text[];
BEGIN
  r[1] := pg_temp.try_ok($q$
    INSERT INTO coupon_code (id, coupon_type_id, code_hash, code_ref, issue_batch_id) VALUES
      ('acc_cc_1', 'acc_ct_1', pg_temp.h('CODE-1'), pg_temp.h('acc_cc_1'), 'acc_ib_1'),
      ('acc_cc_2', 'acc_ct_1', pg_temp.h('CODE-2'), pg_temp.h('acc_cc_2'), 'acc_ib_1'),
      ('acc_cc_3', 'acc_ct_1', pg_temp.h('CODE-3'), pg_temp.h('acc_cc_3'), 'acc_ib_1')
  $q$);
  IF (SELECT count(*) FROM coupon_code WHERE coupon_type_id = 'acc_ct_1') <> 3 THEN
    r[2] := 'FAIL: ออกได้ไม่ครบ 3 ใบ';
  END IF;
  r[3] := pg_temp.try_reject($q$SELECT pg_temp.mk_code('acc_cc_4', 'acc_ct_1', 'CODE-4', 'acc_ib_1')$q$,
    '23514', 'coupon_type', NULL, ARRAY['ck_coupon_type_within_supply']);
  r[4] := pg_temp.try_ok($q$SELECT pg_temp.mk_ct('acc_ct_small', 'acc_mch_ok', 'คูปองจำนวนน้อย', 1)$q$);
  r[5] := pg_temp.try_reject($q$
    INSERT INTO coupon_code (id, coupon_type_id, code_hash, code_ref) VALUES
      ('acc_cc_s1', 'acc_ct_small', pg_temp.h('CODE-S1'), pg_temp.h('acc_cc_s1')),
      ('acc_cc_s2', 'acc_ct_small', pg_temp.h('CODE-S2'), pg_temp.h('acc_cc_s2'))
  $q$, '23514', 'coupon_type', NULL, ARRAY['ck_coupon_type_within_supply']);
  PERFORM pg_temp.rec_checks('T7',
    'ออกครบ 3 ใบตาม supply · ใบที่ 4 → ถูกปฏิเสธ · ออก 2 ใบในประเภทที่เหลือ 1 → ถูกปฏิเสธ', VARIADIC r);
END $$;

-- =====================================================================
-- T8  · ยกเลิกใบหนึ่งแล้วออกใหม่ได้ · VOIDED ปล่อยโควตาคืน
-- T8b · เมื่อออกเต็มแล้ว เปลี่ยนใบที่ VOIDED กลับมาใช้ไม่ได้ (ต้นฉบับตรวจแค่ตอน INSERT)
-- =====================================================================
SELECT pg_temp.rec_checks('T8', 'ยกเลิกใบหนึ่งแล้วออกใบใหม่ได้ · VOIDED ปล่อยโควตาคืน',
  pg_temp.try_ok($q$UPDATE coupon_code SET state = 'VOIDED' WHERE id = 'acc_cc_3'$q$),
  pg_temp.try_ok($q$SELECT pg_temp.mk_code('acc_cc_5', 'acc_ct_1', 'CODE-5', 'acc_ib_1')$q$));

SELECT pg_temp.rec_checks('T8b', 'ออกครบแล้ว เปลี่ยนใบที่ VOIDED กลับเป็น ISSUED → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE coupon_code SET state = 'ISSUED' WHERE id = 'acc_cc_3'$q$,
    '23514', 'coupon_type', NULL, ARRAY['ck_coupon_type_within_supply']));

-- =====================================================================
-- T9 · รหัสเดียวกันออกสองใบไม่ได้ · code_hash unique ทั้งระบบ
--      ใช้ประเภทอื่นที่ยังมีโควตา จะได้เห็นว่าชนที่ code_hash ไม่ใช่ที่ supply
-- =====================================================================
SELECT pg_temp.rec_checks('T9', 'รหัสชนกันข้ามประเภทคูปอง (code_hash ซ้ำทั้งระบบ) → ถูกปฏิเสธ',
  pg_temp.try_reject($q$SELECT pg_temp.mk_code('acc_cc_col', 'acc_ct_retry', 'CODE-1')$q$,
    '23505', 'coupon_code', ARRAY['code_hash']));

-- =====================================================================
-- T10 · coupon_code ไม่มีคอลัมน์เก็บรหัสแบบ plaintext · ตรวจโครงสร้าง ไม่ใช่ข้อมูล   RFD 11 §3.3
-- =====================================================================
DO $$
DECLARE bad text;
BEGIN
  SELECT string_agg(column_name, ',') INTO bad
    FROM information_schema.columns
   WHERE table_schema = current_schema() AND table_name = 'coupon_code'
     AND column_name ~* '(code|secret|plain)'
     AND column_name NOT IN ('code_hash', 'code_ref');
  PERFORM pg_temp.rec('T10', 'coupon_code ไม่มีคอลัมน์เก็บรหัสแบบ plaintext เลย',
                      CASE WHEN bad IS NULL THEN 'PASS' ELSE 'FAIL' END, bad);
END $$;

-- =====================================================================
-- T11 · HELD ต้องมีผู้ถือ · REDEEMED ต้องมีเวลาที่ใช้
-- T12 · ผู้ถือต้องระบุทั้งชนิดและรหัส
-- =====================================================================
SELECT pg_temp.rec_checks('T11', 'HELD โดยไม่มีผู้ถือ หรือ REDEEMED โดยไม่มีเวลา → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE coupon_code SET state = 'HELD' WHERE id = 'acc_cc_1'$q$,
    '23514', 'coupon_code', NULL, ARRAY['ck_code_held']),
  pg_temp.try_reject($q$UPDATE coupon_code SET state = 'REDEEMED' WHERE id = 'acc_cc_2'$q$,
    '23514', 'coupon_code', NULL, ARRAY['ck_code_redeemed']));

SELECT pg_temp.rec_checks('T12', 'ใส่ holder_kind โดยไม่ใส่ holder_id → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE coupon_code SET holder_kind = 'CUSTOMER' WHERE id = 'acc_cc_1'$q$,
    '23514', 'coupon_code', NULL, ARRAY['ck_code_holder']));

-- =====================================================================
-- T13  · ค่าที่มาจาก chain แก้ไม่ได้หลังถูกเขียน (ทุกคอลัมน์ ไม่ใช่แค่ chain_type_id)
-- T13b · chain_type_id เป็น uint256 · '07' · '-1' · 'abc' ไม่ได้
-- =====================================================================
SELECT pg_temp.rec_checks('T13', 'แก้ chain_type_id / contract / block / tx ของประเภทที่ deploy แล้ว → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE coupon_type_chain SET chain_type_id = '99' WHERE coupon_type_id = 'acc_ct_1'$q$,
    '23514', 'coupon_type_chain', NULL, ARRAY['trg_coupon_chain_immutable']),
  pg_temp.try_reject($q$UPDATE coupon_type_chain SET contract_address = '0x' || repeat('b7', 20)
                        WHERE coupon_type_id = 'acc_ct_1'$q$,
    '23514', 'coupon_type_chain', NULL, ARRAY['trg_coupon_chain_immutable']),
  pg_temp.try_reject($q$UPDATE coupon_type_chain SET deployed_block = 1000 WHERE coupon_type_id = 'acc_ct_1'$q$,
    '23514', 'coupon_type_chain', NULL, ARRAY['trg_coupon_chain_immutable']),
  pg_temp.try_reject($q$UPDATE coupon_type_chain SET deployed_tx_hash = '0x' || repeat('ac', 32)
                        WHERE coupon_type_id = 'acc_ct_1'$q$,
    '23514', 'coupon_type_chain', NULL, ARRAY['trg_coupon_chain_immutable']));

SELECT pg_temp.rec_checks('T13b', 'chain_type_id ที่ไม่ใช่ uint256 → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO coupon_type_chain (coupon_type_id, contract_address, chain_type_id, deployed_block, deployed_tx_hash)
    VALUES ('acc_ct_retry', '0x' || repeat('a7', 20), '07', 1002, '0x' || repeat('cd', 32))
  $q$, '23514', 'coupon_type_chain', NULL, ARRAY['ck_coupon_type_chain_type_id_uint256']),
  pg_temp.try_reject($q$
    INSERT INTO coupon_type_chain (coupon_type_id, contract_address, chain_type_id, deployed_block, deployed_tx_hash)
    VALUES ('acc_ct_retry', '0x' || repeat('a7', 20), '-1', 1002, '0x' || repeat('cd', 32))
  $q$, '23514', 'coupon_type_chain', NULL, ARRAY['ck_coupon_type_chain_type_id_uint256']));

-- =====================================================================
-- T14 · contract + type id เดียวกันผูกกับสองประเภทไม่ได้
-- =====================================================================
SELECT pg_temp.rec_checks('T14', 'contract address + chain_type_id ซ้ำ → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO coupon_type_chain (coupon_type_id, contract_address, chain_type_id, deployed_block, deployed_tx_hash)
    VALUES ('acc_ct_retry', '0x' || repeat('a7', 20), '7', 1002, '0x' || repeat('cd', 32))
  $q$, '23505', 'coupon_type_chain', ARRAY['chain_type_id', 'contract_address']));

-- =====================================================================
-- T15 · ประเภทคูปองอันเดียวสั่ง deploy ซ้อนไม่ได้
-- =====================================================================
INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
VALUES ('acc_op_d1', 'merchant:acc_mch_ok', 'COUPON_TYPE_CREATE', 'acc_ct_retry', '{}');
INSERT INTO outbox (id, operation_id, topic, payload)
VALUES ('acc_obx_d1', 'acc_op_d1', 'coupon.type.create.v1', '{}');

SELECT pg_temp.rec_checks('T15', 'สั่ง deploy ประเภทคูปองเดิมซ้อนกัน → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
    VALUES ('acc_op_d2', 'merchant:acc_mch_ok', 'COUPON_TYPE_CREATE', 'acc_ct_retry', '{}')
  $q$, '23505', 'operation', ARRAY['subject_id'], ARRAY['uq_coupon_deploy_inflight']));

-- =====================================================================
-- T16 · mint เป็นชุด · step ละไม่เกิน 500 ใบ
-- =====================================================================
SELECT pg_temp.rec_checks('T16', 'mint 1200 ใบ = 3 step · step ที่เกิน 500 → ถูกปฏิเสธ',
  pg_temp.try_ok($q$
    INSERT INTO operation_step (id, operation_id, step_index, name, mint_count) VALUES
      ('acc_st_1', 'acc_op_1', 1, 'mintCoupon-1', 500),
      ('acc_st_2', 'acc_op_1', 2, 'mintCoupon-2', 500),
      ('acc_st_3', 'acc_op_1', 3, 'mintCoupon-3', 200)
  $q$),
  pg_temp.try_reject($q$
    INSERT INTO operation_step (id, operation_id, step_index, name, mint_count)
    VALUES ('acc_st_bad', 'acc_op_d1', 1, 'mintCoupon-1', 501)
  $q$, '23514', 'operation_step', NULL, ARRAY['ck_operation_step_mint_count_range']));

-- =====================================================================
-- T17 · ห้าม broadcast ก่อน raw_tx ลงตาราง                   RFD 2 R2
-- T18 · nonce ของ signer ตัวหนึ่งใช้ซ้ำไม่ได้                  RFD 4
-- =====================================================================
SELECT pg_temp.rec_checks('T17', 'เปลี่ยน step เป็น SUBMITTED โดยไม่มี raw_tx → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE operation_step SET status = 'SUBMITTED' WHERE id = 'acc_st_1'$q$,
    '23514', 'operation_step', NULL, ARRAY['ck_rawtx_before_broadcast']));

UPDATE operation_step
   SET raw_tx = '\x02f8', signer_address = '0x' || repeat('3a', 20), nonce = 80,
       status = 'SUBMITTED', tx_hash = '0x' || repeat('a1', 32)
 WHERE id = 'acc_st_1';

SELECT pg_temp.rec_checks('T18', 'ใช้ nonce เดิมของ signer ตัวเดิมซ้ำ → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    UPDATE operation_step
       SET raw_tx = '\x02f8', signer_address = '0x' || repeat('3a', 20), nonce = 80,
           status = 'SUBMITTED', tx_hash = '0x' || repeat('a2', 32)
     WHERE id = 'acc_st_2'
  $q$, '23505', 'operation_step', ARRAY['nonce', 'signer_address']));

-- =====================================================================
-- T19 · มูลค่าหน้าคูปองเก็บ uint256 สูงสุดได้ · เกิน / ติดลบ / รูปแบบอื่น ไม่ได้   RFD 14 D1
-- =====================================================================
DO $$
DECLARE r text[];
BEGIN
  r[1] := pg_temp.try_ok(format($q$SELECT pg_temp.mk_ct('acc_ct_max', 'acc_mch_ok', 'คูปองมูลค่าสูงสุด', 1,
                                                       'REQUESTED', %L)$q$, (2::numeric ^ 256 - 1)::text));
  IF (SELECT face_value FROM coupon_type WHERE id = 'acc_ct_max') IS DISTINCT FROM (2::numeric ^ 256 - 1)::text THEN
    r[2] := 'FAIL: ค่าที่อ่านกลับไม่ตรง';
  END IF;
  r[3] := pg_temp.try_reject(format($q$SELECT pg_temp.mk_ct('acc_ct_over', 'acc_mch_ok', 'เกิน', 1,
                                                           'REQUESTED', %L)$q$, (2::numeric ^ 256)::text),
    '23514', 'coupon_type', NULL, ARRAY['ck_coupon_type_face_value_uint256']);
  r[4] := pg_temp.try_reject($q$SELECT pg_temp.mk_ct('acc_ct_neg', 'acc_mch_ok', 'ติดลบ', 1, 'REQUESTED', '-1')$q$,
    '23514', 'coupon_type', NULL, ARRAY['ck_coupon_type_face_value_uint256']);
  r[5] := pg_temp.try_reject($q$SELECT pg_temp.mk_ct('acc_ct_exp', 'acc_mch_ok', 'เลขยกกำลัง', 1, 'REQUESTED', '1e3')$q$,
    '23514', 'coupon_type', NULL, ARRAY['ck_coupon_type_face_value_uint256']);
  PERFORM pg_temp.rec_checks('T19',
    'face_value = 2^256-1 เก็บได้ครบทุกหลัก · 2^256 · -1 · 1e3 → ถูกปฏิเสธ', VARIADIC r);
END $$;

-- =====================================================================
-- T20 · ประเภทคูปองที่ LIVE ต้องมีแถวฝั่ง chain
--       ตรวจข้อมูลทั้งฐาน และลองตั้ง LIVE โดยไม่มีแถว chain · ต้องถูกปฏิเสธตอน commit
-- =====================================================================
DO $$
DECLARE bad text; r text;
BEGIN
  SELECT string_agg(t.id, ',') INTO bad
    FROM coupon_type t LEFT JOIN coupon_type_chain c ON c.coupon_type_id = t.id
   WHERE t.status = 'LIVE' AND c.coupon_type_id IS NULL;
  r := pg_temp.try_reject($q$
         UPDATE coupon_type SET status = 'LIVE', live_at = now() WHERE id = 'acc_ct_retry';
         SET CONSTRAINTS ALL IMMEDIATE;
       $q$, '23514', 'coupon_type', NULL, ARRAY['trg_coupon_live_has_chain']);
  SET CONSTRAINTS ALL DEFERRED;
  PERFORM pg_temp.rec_checks('T20', 'ไม่มีประเภทที่ LIVE แต่ไม่มี contract · ตั้ง LIVE โดยไม่มีแถว chain → ถูกปฏิเสธ',
    CASE WHEN bad IS NULL THEN NULL ELSE 'FAIL: ข้อมูลเดิมผิดกฎ ' || bad END, r);
END $$;

-- =====================================================================
-- T21  · ไม่มีรหัสที่ออกเกิน supply ของประเภทตัวเอง (ทั้งฐาน)
-- T21b · ตัวนับ issued_count ตรงกับจำนวนรหัสที่ไม่ VOIDED จริง (ทั้งฐาน)
-- T21c · แอปเขียน issued_count เองไม่ได้
-- T21d · ลด total_supply ให้ต่ำกว่าที่ออกไปแล้วไม่ได้
-- =====================================================================
DO $$
DECLARE bad text;
BEGIN
  SELECT string_agg(id, ',') INTO bad FROM (
    SELECT t.id, t.total_supply, count(c.id) FILTER (WHERE c.state <> 'VOIDED') AS issued
      FROM coupon_type t LEFT JOIN coupon_code c ON c.coupon_type_id = t.id
     GROUP BY t.id, t.total_supply) x
   WHERE x.issued > x.total_supply;
  PERFORM pg_temp.rec('T21', 'ไม่มีประเภทไหนที่ออกรหัสเกิน supply ที่ประกาศไว้',
                      CASE WHEN bad IS NULL THEN 'PASS' ELSE 'FAIL' END, bad);
END $$;

DO $$
DECLARE bad text;
BEGIN
  EXECUTE $q$
    SELECT string_agg(t.id || ' ' || t.issued_count || '≠' || coalesce(x.n, 0), ', ')
      FROM coupon_type t
      LEFT JOIN (SELECT coupon_type_id, count(*) AS n FROM coupon_code
                  WHERE state <> 'VOIDED' GROUP BY coupon_type_id) x ON x.coupon_type_id = t.id
     WHERE t.issued_count <> coalesce(x.n, 0)
  $q$ INTO bad;
  PERFORM pg_temp.rec('T21b', 'issued_count ตรงกับจำนวนรหัสที่ไม่ VOIDED ทุกประเภท',
                      CASE WHEN bad IS NULL THEN 'PASS' ELSE 'FAIL' END, bad);
EXCEPTION WHEN OTHERS THEN
  PERFORM pg_temp.rec('T21b', 'issued_count ตรงกับจำนวนรหัสที่ไม่ VOIDED ทุกประเภท', 'ERROR',
                      SQLSTATE || ' · ' || SQLERRM);
END $$;

SELECT pg_temp.rec_checks('T21c', 'เขียน issued_count ตรงๆ (ตอนสร้างหรือแก้) → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE coupon_type SET issued_count = 0 WHERE id = 'acc_ct_1'$q$,
    '23514', 'coupon_type', NULL, ARRAY['trg_coupon_issued_count_guard']),
  pg_temp.try_reject($q$
    INSERT INTO coupon_type (id, merchant_id, name, name_normalized, face_value, total_supply,
                             expires_at, issued_count)
    VALUES ('acc_ct_cnt', 'acc_mch_ok', 'นับเอง', 'นับเอง', '1', 10, now() + interval '30 days', 5)
  $q$, '23514', 'coupon_type', NULL, ARRAY['trg_coupon_issued_count_guard']));

SELECT pg_temp.rec_checks('T21d', 'ลด total_supply ให้ต่ำกว่าจำนวนที่ออกไปแล้ว → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE coupon_type SET total_supply = 2 WHERE id = 'acc_ct_1'$q$,
    '23514', 'coupon_type', NULL, ARRAY['ck_coupon_type_within_supply']));

-- =====================================================================
-- T22 · operation ทุกแถวต้องมี outbox คู่กัน (ทั้งฐาน)        RFD 2 D1
-- =====================================================================
DO $$
DECLARE bad text;
BEGIN
  SELECT string_agg(o.id, ',') INTO bad
    FROM operation o LEFT JOIN outbox b ON b.operation_id = o.id WHERE b.id IS NULL;
  PERFORM pg_temp.rec('T22', 'ไม่มี operation ที่ไม่มีใครมาทำ (orphan = 0)',
                      CASE WHEN bad IS NULL THEN 'PASS' ELSE 'FAIL' END, bad);
END $$;

-- =====================================================================
-- T23 · อ่าน log ช่วงเดิมซ้ำได้ · chain_event ไม่เกิดแถวซ้ำ        RFD 5 D2
-- =====================================================================
INSERT INTO raw_block (block_hash, block_number, parent_hash, "timestamp")
VALUES ('0x' || repeat('b7', 32), 900000001, '0x' || repeat('b6', 32), now());
-- FEAT-14 · บล็อกที่อ่านแล้วต้องอยู่ใต้ cursor ตอน commit (0017)
INSERT INTO indexer_cursor (id, last_block, last_block_hash)
VALUES ('main', 900000001, '0x' || repeat('b7', 32));

DO $$
DECLARE n1 int; n2 int;
BEGIN
  INSERT INTO chain_event (id, block_number, block_hash, tx_hash, log_index, address, topic0, topics,
                           data, block_timestamp)
  VALUES ('acc_ce_1', 900000001, '0x' || repeat('b7', 32), '0x' || repeat('e7', 32), 0,
          '0x' || repeat('a7', 20), '0x' || repeat('00', 32), '[]', '\x00', now())
  ON CONFLICT (tx_hash, log_index) DO NOTHING;
  GET DIAGNOSTICS n1 = ROW_COUNT;
  INSERT INTO chain_event (id, block_number, block_hash, tx_hash, log_index, address, topic0, topics,
                           data, block_timestamp)
  VALUES ('acc_ce_2', 900000001, '0x' || repeat('b7', 32), '0x' || repeat('e7', 32), 0,
          '0x' || repeat('a7', 20), '0x' || repeat('00', 32), '[]', '\x00', now())
  ON CONFLICT (tx_hash, log_index) DO NOTHING;
  GET DIAGNOSTICS n2 = ROW_COUNT;
  PERFORM pg_temp.rec('T23', 'อ่าน log ช่วงเดิมซ้ำ → chain_event ไม่เกิดแถวซ้ำ',
                      CASE WHEN n1 = 1 AND n2 = 0 THEN 'PASS' ELSE 'FAIL' END,
                      format('ได้ %s · %s', n1, n2));
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
