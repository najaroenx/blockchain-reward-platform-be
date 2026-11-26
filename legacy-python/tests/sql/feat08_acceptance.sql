-- =====================================================================
-- FEAT-08 · List on Marketplace · acceptance สำหรับ blockchain-reward-platform-be
--
-- ต้นฉบับ : blockchain-reward-platform/reference/feat-08/tests/acceptance.sql (25 ข้อ)
-- schema  : migrations 0002 → 0011_feat08_listing
--
-- รัน (Postgres ใน docker):
--   docker exec -i blockchain-reward-platform-be-postgres-1 \
--     psql -U reward -d reward < tests/sql/feat08_acceptance.sql
--
-- ผล  PASS ฐานข้อมูลบังคับกฎนี้ได้จริง · FAIL ไม่ได้ หรือถูกปฏิเสธด้วยกฎตัวอื่น
--     ERROR test เองรันไม่ได้ · ดูหมายเหตุ
--
-- ต่างจากต้นฉบับ · หลักฐานอยู่ใน docs/reference-findings.md
--   1. hash / address เป็นสตริง hex '0x…' · ราคาและ chain_listing_id เป็นสตริง uint256 (0002)
--   2. Marketplace จริงคือ listCoupon(typeId, amount, pricePerUnit, paymentToken) หลัง
--      setApprovalForAll ของ Coupon · ไม่มี approveCoupon / createListing / lockInVault
--      T11 จึงเป็นสอง step ตาม ABI · Vault ล็อกเงินตอนซื้อ (FEAT-10) ไม่ใช่คูปอง
--   3. บน chain ลงขายเป็นจำนวน · listing_item เป็นบัญชีภายในว่าใบไหนถูกกันไว้ขาย
--   4. T3b เพิ่ม · ใบน้อยกว่า quantity ก็ไม่ได้ (ต้นฉบับตรวจแค่เกิน) · ตรวจตอน commit
--   5. T9b เพิ่ม · ใบเดียวกันอยู่สองรายการขายที่ยังเปิดไม่ได้ แม้ service ลืมจอง reservation
--      T16b · รายการที่ FAILED ปล่อยใบคืน
--   6. T2b เพิ่ม · ใส่ใบได้เฉพาะตอนรายการยัง PENDING
--   7. T17 ตรวจทุกคอลัมน์จาก chain · การปิดรายการ (closed_*) เขียนได้ครั้งเดียว
--      T17b chain_listing_id ต้องเป็น uint256
--   8. T8 / T20 ตรวจรูปแบบ uint256 ด้วย (2^256 · 1e3)
--   9. T23 นอกจากตรวจข้อมูล ยังลองตั้ง ACTIVE โดยไม่มีแถว chain · ต้องถูกปฏิเสธตอน commit
--  10. T24 ตรวจเฉพาะรายการที่ยังเปิด (PENDING / ACTIVE) · ใบที่ขายแล้วเปลี่ยนมือได้ (FEAT-10)
--  11. T9 T15 T16 จองใบที่ไม่อยู่ในรายการขาย · ใบที่อยู่ในรายการที่ยังเปิด ปล่อยจองไม่ได้ (0012)
--      T14 / T19 ปรับหลัง 0012 (FEAT-09) · nonce ซ้ำใช้ step 1 ของอีกงาน (กฎลำดับ step)
--      งานซ้อนใช้คำขอสร้างซ้ำ (ถอดได้เฉพาะรายการ ACTIVE)
--  12. ข้อมูลทดสอบขึ้นต้นด้วย acc_ · ข้อ "ต้องถูกปฏิเสธ" ตรวจว่าชนกฎที่ตั้งใจจริง
--
-- ทั้งไฟล์อยู่ใน transaction เดียวแล้ว ROLLBACK · รันซ้ำกี่รอบก็ได้
-- =====================================================================
\set ON_ERROR_STOP on
\pset format aligned

BEGIN;

-- ควรได้ 0011_feat08_listing ขึ้นไป
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


-- ------------------------------------------------------------ fixture
INSERT INTO merchant (id, name, status, activated_at) VALUES
  ('acc_mch_a', 'ร้านผู้ขาย', 'ACTIVE', now()),
  ('acc_mch_b', 'ร้านอื่น', 'ACTIVE', now());
INSERT INTO signer (address, key_ref) VALUES ('0x' || repeat('3b', 20), 'kms/acc-signer-1');

INSERT INTO coupon_type (id, merchant_id, name, name_normalized, face_value, total_supply, status,
                         expires_at, live_at, created_at, starts_at) VALUES
  ('acc_ct_1', 'acc_mch_a', 'คูปองส่วนลด 100', 'คูปองส่วนลด 100', '100000000000000000000', 100, 'LIVE',
   now() + interval '180 days', now(), now(), now()),
  ('acc_ct_2', 'acc_mch_a', 'คูปองคนละประเภท', 'คูปองคนละประเภท', '50000000000000000000', 100, 'LIVE',
   now() + interval '180 days', now(), now(), now()),
  ('acc_ct_old', 'acc_mch_a', 'คูปองหมดอายุ', 'คูปองหมดอายุ', '10000000000000000000', 100, 'LIVE',
   now() - interval '1 day', now() - interval '20 days', now() - interval '30 days', now() - interval '30 days');
INSERT INTO coupon_type_chain (coupon_type_id, contract_address, chain_type_id, deployed_block, deployed_tx_hash) VALUES
  ('acc_ct_1',   '0x' || repeat('c8', 20), '1', 1001, '0x' || repeat('c1', 32)),
  ('acc_ct_2',   '0x' || repeat('c8', 20), '2', 1001, '0x' || repeat('c2', 32)),
  ('acc_ct_old', '0x' || repeat('c8', 20), '3', 1001, '0x' || repeat('c3', 32));

-- ร้าน A ถือคูปอง ct_1 อยู่ 10 ใบ
INSERT INTO coupon_code (id, coupon_type_id, code_hash, code_ref, state, holder_kind, holder_id, held_at)
SELECT 'acc_cc_' || g, 'acc_ct_1', pg_temp.h('CODE-' || g), pg_temp.h('acc_cc_' || g),
       'HELD', 'MERCHANT', 'acc_mch_a', now()
  FROM generate_series(1, 10) g;
-- ใบที่ยังไม่มีผู้ถือ · ใบของร้านอื่น · ใบคนละประเภท · ใบที่หมดอายุ
INSERT INTO coupon_code (id, coupon_type_id, code_hash, code_ref, state, holder_kind, holder_id, held_at) VALUES
  ('acc_cc_unheld', 'acc_ct_1',   pg_temp.h('U'), pg_temp.h('acc_cc_unheld'), 'ISSUED', NULL, NULL, NULL),
  ('acc_cc_other',  'acc_ct_1',   pg_temp.h('O'), pg_temp.h('acc_cc_other'),  'HELD', 'MERCHANT', 'acc_mch_b', now()),
  ('acc_cc_type2',  'acc_ct_2',   pg_temp.h('T'), pg_temp.h('acc_cc_type2'),  'HELD', 'MERCHANT', 'acc_mch_a', now()),
  ('acc_cc_exp',    'acc_ct_old', pg_temp.h('E'), pg_temp.h('acc_cc_exp'),    'HELD', 'MERCHANT', 'acc_mch_a', now());

INSERT INTO operation (id, actor_scope, kind, subject_id, payload) VALUES
  ('acc_op_1', 'merchant:acc_mch_a', 'LISTING_CREATE', 'acc_lst_1', '{}'),
  ('acc_op_2', 'merchant:acc_mch_a', 'LISTING_CREATE', 'acc_lst_2', '{}');
INSERT INTO outbox (id, operation_id, topic, payload) VALUES
  ('acc_obx_1', 'acc_op_1', 'listing.create.v1', '{}'),
  ('acc_obx_2', 'acc_op_2', 'listing.create.v1', '{}');

-- รายการขาย 1 ใบ ใส่ใบเดียว · ใช้เป็น SQL ของข้อที่ "ต้องถูกปฏิเสธ"
-- ทั้งรายการและใบอยู่ใน statement ชุดเดียวกัน จึงถูกยกเลิกทั้งคู่เมื่อถูกปฏิเสธ
CREATE FUNCTION pg_temp.list_one(p_listing text, p_type text, p_code text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT format($q$
    INSERT INTO listing (id, seller_kind, seller_id, coupon_type_id, unit_price, quantity)
    VALUES (%L, 'MERCHANT', 'acc_mch_a', %L, '80000000000000000000', 1);
    INSERT INTO listing_item (id, listing_id, coupon_code_id) VALUES (%L, %L, %L);
  $q$, p_listing, p_type, p_listing || '_i', p_listing, p_code)
$$;

-- =====================================================================
-- T1 · ส่ง Idempotency-Key เดิมซ้ำ → ไม่เกิดงานใหม่            RFD 3 R1
-- =====================================================================
DO $$
DECLARE n1 int; n2 int;
BEGIN
  INSERT INTO idempotency_key (id, scope, key, fingerprint, expires_at)
  VALUES ('acc_ik_1', 'merchant:acc_mch_a', '11111111-2222-3333-4444-555555555555',
          pg_temp.h('b'), now() + interval '1 day')
  ON CONFLICT (scope, key) DO NOTHING;
  GET DIAGNOSTICS n1 = ROW_COUNT;
  INSERT INTO idempotency_key (id, scope, key, fingerprint, expires_at)
  VALUES ('acc_ik_2', 'merchant:acc_mch_a', '11111111-2222-3333-4444-555555555555',
          pg_temp.h('b'), now() + interval '1 day')
  ON CONFLICT (scope, key) DO NOTHING;
  GET DIAGNOSTICS n2 = ROW_COUNT;
  PERFORM pg_temp.rec('T1', 'ลงขายซ้ำด้วย key เดิม → แถวที่สองได้ 0 rows',
                      CASE WHEN n1 = 1 AND n2 = 0 THEN 'PASS' ELSE 'FAIL' END, format('ได้ %s · %s', n1, n2));
END $$;

-- =====================================================================
-- T2 · ขายบางส่วนได้ · 5 จาก 10 ใบ · ระบุได้ว่าใบไหนบ้าง
-- =====================================================================
DO $$
DECLARE r text[];
BEGIN
  r[1] := pg_temp.try_ok($q$
    INSERT INTO listing (id, seller_kind, seller_id, coupon_type_id, unit_price, quantity)
    VALUES ('acc_lst_1', 'MERCHANT', 'acc_mch_a', 'acc_ct_1', '80000000000000000000', 5);
    INSERT INTO listing_item (id, listing_id, coupon_code_id)
    SELECT 'acc_li_' || g, 'acc_lst_1', 'acc_cc_' || g FROM generate_series(1, 5) g;
  $q$);
  IF (SELECT count(*) FROM listing_item WHERE listing_id = 'acc_lst_1') <> 5 THEN
    r[2] := 'FAIL: ไม่ได้ 5 ใบ';
  END IF;
  PERFORM pg_temp.rec_checks('T2', 'ขาย 5 จาก 10 ใบได้ · ระบุใบที่ขายเจาะจง', VARIADIC r);
END $$;

-- =====================================================================
-- T3  · ใส่ใบเกิน quantity ที่ประกาศไว้ไม่ได้ (ปฏิเสธทันที)
-- T3b · ใส่ใบน้อยกว่า quantity ก็ไม่ได้ (ปฏิเสธตอน commit)
-- =====================================================================
SELECT pg_temp.rec_checks('T3', 'ใส่ใบที่ 6 ทั้งที่ประกาศ quantity 5 → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO listing_item (id, listing_id, coupon_code_id)
                        VALUES ('acc_li_x', 'acc_lst_1', 'acc_cc_6')$q$,
    '23514', 'listing_item', NULL, ARRAY['trg_listing_item_max']));

SELECT pg_temp.rec_checks('T3b', 'ประกาศ quantity 3 แต่ใส่ 2 ใบ → ถูกปฏิเสธตอน commit',
  pg_temp.try_reject($q$
    INSERT INTO listing (id, seller_kind, seller_id, coupon_type_id, unit_price, quantity)
    VALUES ('acc_lst_short', 'MERCHANT', 'acc_mch_a', 'acc_ct_1', '1', 3);
    INSERT INTO listing_item (id, listing_id, coupon_code_id) VALUES
      ('acc_li_s1', 'acc_lst_short', 'acc_cc_6'), ('acc_li_s2', 'acc_lst_short', 'acc_cc_7');
    SET CONSTRAINTS trg_listing_item_count IMMEDIATE;
  $q$, '23514', 'listing', NULL, ARRAY['trg_listing_item_count']));
SELECT pg_temp.try_ok('SET CONSTRAINTS trg_listing_item_count DEFERRED');

-- =====================================================================
-- T4 · ลงขายใบที่ไม่ได้อยู่ในมือตัวเองไม่ได้                    RFD 12 · F5
-- T5 · ใบที่ยังไม่ HELD · T6 · ใบคนละประเภท · T7 · ใบที่หมดอายุ
-- =====================================================================
SELECT pg_temp.rec_checks('T4', 'ลงขายคูปองของร้านอื่น → ถูกปฏิเสธที่ฐานข้อมูล',
  pg_temp.try_reject(pg_temp.list_one('acc_lst_x4', 'acc_ct_1', 'acc_cc_other'),
    '23514', 'listing_item', NULL, ARRAY['trg_listing_item_valid']));
SELECT pg_temp.rec_checks('T5', 'ลงขายใบที่สถานะยังไม่ HELD → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.list_one('acc_lst_x5', 'acc_ct_1', 'acc_cc_unheld'),
    '23514', 'listing_item', NULL, ARRAY['trg_listing_item_valid']));
SELECT pg_temp.rec_checks('T6', 'ใส่คูปองคนละประเภทกับรายการขาย → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.list_one('acc_lst_x6', 'acc_ct_1', 'acc_cc_type2'),
    '23514', 'listing_item', NULL, ARRAY['trg_listing_item_valid']));
SELECT pg_temp.rec_checks('T7', 'ลงขายคูปองที่หมดอายุไปแล้ว → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.list_one('acc_lst_x7', 'acc_ct_old', 'acc_cc_exp'),
    '23514', 'listing_item', NULL, ARRAY['trg_listing_item_valid']));

-- =====================================================================
-- T8 · ราคาต้องมากกว่า 0 · เป็น uint256
-- =====================================================================
SELECT pg_temp.rec_checks('T8', 'ราคา 0 หรือติดลบ → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO listing (id, seller_kind, seller_id, coupon_type_id, unit_price, quantity)
                        VALUES ('acc_lst_p0', 'MERCHANT', 'acc_mch_a', 'acc_ct_1', '0', 1)$q$,
    '23514', 'listing', NULL, ARRAY['ck_listing_unit_price_uint256']),
  pg_temp.try_reject($q$INSERT INTO listing (id, seller_kind, seller_id, coupon_type_id, unit_price, quantity)
                        VALUES ('acc_lst_pn', 'MERCHANT', 'acc_mch_a', 'acc_ct_1', '-1', 1)$q$,
    '23514', 'listing', NULL, ARRAY['ck_listing_unit_price_uint256']));

-- =====================================================================
-- T9  · คูปองใบเดียวกันจองสองครั้งไม่ได้ (reservation)          RFD 12 D3
-- T9b · คูปองใบเดียวกันอยู่ในสองรายการขายที่ยังเปิดไม่ได้ แม้ไม่ได้จอง reservation
-- T10 · รายการขายเองก็จองซ้อนไม่ได้
-- =====================================================================
INSERT INTO reservation (id, operation_id, subject_type, subject_id, amount)
VALUES ('acc_rsv_c1', 'acc_op_1', 'COUPON_UNIT', 'acc_cc_10', '1');

SELECT pg_temp.rec_checks('T9', 'จองคูปองใบเดียวกันในสองรายการขาย → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO reservation (id, operation_id, subject_type, subject_id, amount)
                        VALUES ('acc_rsv_c1b', 'acc_op_2', 'COUPON_UNIT', 'acc_cc_10', '1')$q$,
    '23505', 'reservation', ARRAY['subject_id', 'subject_type']));

SELECT pg_temp.rec_checks('T9b', 'ใส่ใบที่อยู่ในรายการขายที่ยังเปิดอยู่ ลงอีกรายการ → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.list_one('acc_lst_dup', 'acc_ct_1', 'acc_cc_2'),
    '23514', 'listing_item', NULL, ARRAY['trg_listing_item_valid']));

INSERT INTO reservation (id, operation_id, subject_type, subject_id, amount)
VALUES ('acc_rsv_l1', 'acc_op_1', 'LISTING', 'acc_lst_1', '1');

SELECT pg_temp.rec_checks('T10', 'จองรายการขายเดิมซ้อนกัน → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO reservation (id, operation_id, subject_type, subject_id, amount)
                        VALUES ('acc_rsv_l1b', 'acc_op_2', 'LISTING', 'acc_lst_1', '1')$q$,
    '23505', 'reservation', ARRAY['subject_id', 'subject_type']));

-- =====================================================================
-- T11 · ลงขาย = step ที่เรียงกัน · ตาม ABI จริง: setApprovalForAll → listCoupon
-- T12 · step_index ซ้ำในงานเดียวกันไม่ได้
-- =====================================================================
DO $$
DECLARE names text[]; r text;
BEGIN
  r := pg_temp.try_ok($q$
    INSERT INTO operation_step (id, operation_id, step_index, name) VALUES
      ('acc_st_1', 'acc_op_1', 1, 'setApprovalForAll'),
      ('acc_st_2', 'acc_op_1', 2, 'listCoupon')
  $q$);
  SELECT array_agg(name ORDER BY step_index) INTO names FROM operation_step WHERE operation_id = 'acc_op_1';
  PERFORM pg_temp.rec_checks('T11', 'ลงขายมี step เรียง setApprovalForAll → listCoupon', r,
    CASE WHEN names = ARRAY['setApprovalForAll', 'listCoupon'] THEN NULL
         ELSE 'FAIL: ได้ ' || coalesce(array_to_string(names, ' → '), 'ไม่มี') END);
END $$;

SELECT pg_temp.rec_checks('T12', 'step_index ซ้ำในงานเดียวกัน → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO operation_step (id, operation_id, step_index, name)
                        VALUES ('acc_st_dup', 'acc_op_1', 2, 'listCoupon-retry')$q$,
    '23505', 'operation_step', ARRAY['operation_id', 'step_index']));

-- =====================================================================
-- T13 · ห้าม broadcast ก่อน raw_tx ลงตาราง                   RFD 2 R2
-- T14 · nonce ของ signer ตัวหนึ่งใช้ซ้ำไม่ได้                  RFD 4
-- =====================================================================
SELECT pg_temp.rec_checks('T13', 'เปลี่ยน step เป็น SUBMITTED โดยไม่มี raw_tx → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE operation_step SET status = 'SUBMITTED' WHERE id = 'acc_st_1'$q$,
    '23514', 'operation_step', NULL, ARRAY['ck_rawtx_before_broadcast']));

UPDATE operation_step
   SET raw_tx = '\x02f8', signer_address = '0x' || repeat('3b', 20), nonce = 90,
       status = 'SUBMITTED', tx_hash = '0x' || repeat('b1', 32)
 WHERE id = 'acc_st_1';

-- step 1 ของอีก operation · step 2 ของงานเดิมจะชนกฎลำดับ step (0012) ก่อนถึง nonce
INSERT INTO operation_step (id, operation_id, step_index, name)
VALUES ('acc_st_o2', 'acc_op_2', 1, 'setApprovalForAll');

SELECT pg_temp.rec_checks('T14', 'ใช้ nonce เดิมของ signer ตัวเดิมซ้ำ → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    UPDATE operation_step
       SET raw_tx = '\x02f8', signer_address = '0x' || repeat('3b', 20), nonce = 90,
           status = 'SUBMITTED', tx_hash = '0x' || repeat('b2', 32)
     WHERE id = 'acc_st_o2'
  $q$, '23505', 'operation_step', ARRAY['nonce', 'signer_address']));

-- =====================================================================
-- T15 · ปล่อยจองก่อนงานจบไม่ได้ · พอ CHAIN_REVERTED แล้วปล่อยได้   RFD 9
-- T16 · ปล่อยแล้วคูปองใบนั้นจองใหม่ได้
-- =====================================================================
DO $$
DECLARE r text[];
BEGIN
  r[1] := pg_temp.try_reject($q$UPDATE reservation SET released_at = now() WHERE id = 'acc_rsv_c1'$q$,
    '23514', 'reservation', NULL, ARRAY['trg_reservation_release_timing']);
  UPDATE operation SET status = 'SUBMITTED' WHERE id = 'acc_op_1';
  UPDATE operation SET status = 'CHAIN_REVERTED' WHERE id = 'acc_op_1';
  r[2] := pg_temp.try_ok($q$UPDATE reservation SET released_at = now() WHERE id = 'acc_rsv_c1'$q$);
  PERFORM pg_temp.rec_checks('T15', 'ปล่อยจองก่อนงานจบไม่ได้ · พอ CHAIN_REVERTED แล้วปล่อยได้', VARIADIC r);
END $$;

SELECT pg_temp.rec_checks('T16', 'ปล่อยจองแล้วคูปองใบเดิมจองใหม่ได้',
  pg_temp.try_ok($q$INSERT INTO reservation (id, operation_id, subject_type, subject_id, amount)
                    VALUES ('acc_rsv_c1c', 'acc_op_2', 'COUPON_UNIT', 'acc_cc_10', '1')$q$));

-- T16b · รายการที่ FAILED ปล่อยใบคืน · ใบเดิมลงรายการใหม่ได้
DO $$
DECLARE r text[];
BEGIN
  r[1] := pg_temp.try_ok(pg_temp.list_one('acc_lst_f', 'acc_ct_1', 'acc_cc_8'));
  UPDATE listing SET status = 'FAILED' WHERE id = 'acc_lst_f';
  r[2] := pg_temp.try_ok(pg_temp.list_one('acc_lst_g', 'acc_ct_1', 'acc_cc_8'));
  PERFORM pg_temp.rec_checks('T16b', 'รายการเดิม FAILED แล้ว ใบเดิมลงรายการใหม่ได้', VARIADIC r);
END $$;

-- =====================================================================
-- T17  · ค่าที่มาจาก chain แก้ไม่ได้ · การปิดรายการเขียนได้ครั้งเดียว
-- T17b · chain_listing_id ต้องเป็น uint256
-- T18  · contract + chain_listing_id ผูกกับสองรายการไม่ได้
-- =====================================================================
INSERT INTO listing_chain (listing_id, contract_address, chain_listing_id, created_block, created_tx_hash)
VALUES ('acc_lst_1', '0x' || repeat('d8', 20), '42', 1001, '0x' || repeat('db', 32));

DO $$
DECLARE r text[];
BEGIN
  r[1] := pg_temp.try_reject($q$UPDATE listing_chain SET chain_listing_id = '99' WHERE listing_id = 'acc_lst_1'$q$,
    '23514', 'listing_chain', NULL, ARRAY['trg_listing_chain_immutable']);
  r[2] := pg_temp.try_reject($q$UPDATE listing_chain SET contract_address = '0x' || repeat('d9', 20)
                                 WHERE listing_id = 'acc_lst_1'$q$,
    '23514', 'listing_chain', NULL, ARRAY['trg_listing_chain_immutable']);
  r[3] := pg_temp.try_reject($q$UPDATE listing_chain SET created_block = 1000 WHERE listing_id = 'acc_lst_1'$q$,
    '23514', 'listing_chain', NULL, ARRAY['trg_listing_chain_immutable']);
  r[4] := pg_temp.try_reject($q$UPDATE listing_chain SET created_tx_hash = '0x' || repeat('dc', 32)
                                 WHERE listing_id = 'acc_lst_1'$q$,
    '23514', 'listing_chain', NULL, ARRAY['trg_listing_chain_immutable']);
  -- ปิดรายการครั้งแรกได้ (ใน savepoint · ไม่ทิ้งผลไว้) · แก้ผลการปิดซ้ำไม่ได้
  r[5] := pg_temp.try_reject($q$
    UPDATE listing_chain SET closed_block = 1100, closed_tx_hash = '0x' || repeat('dd', 32),
                             close_reason = 'CANCELLED' WHERE listing_id = 'acc_lst_1';
    UPDATE listing_chain SET close_reason = 'SOLD' WHERE listing_id = 'acc_lst_1';
  $q$, '23514', 'listing_chain', NULL, ARRAY['trg_listing_chain_immutable']);
  PERFORM pg_temp.rec_checks('T17',
    'แก้ chain_listing_id / contract / block / tx หรือผลการปิดที่เขียนแล้ว → ถูกปฏิเสธ', VARIADIC r);
END $$;

SELECT pg_temp.rec_checks('T17b', 'chain_listing_id ที่ไม่ใช่ uint256 → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO listing_chain (listing_id, contract_address, chain_listing_id, created_block, created_tx_hash)
    VALUES ('acc_lst_g', '0x' || repeat('d8', 20), '042', 1002, '0x' || repeat('de', 32))
  $q$, '23514', 'listing_chain', NULL, ARRAY['ck_listing_chain_id_uint256']));

SELECT pg_temp.rec_checks('T18', 'contract + chain_listing_id ซ้ำ → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO listing_chain (listing_id, contract_address, chain_listing_id, created_block, created_tx_hash)
    VALUES ('acc_lst_g', '0x' || repeat('d8', 20), '42', 1002, '0x' || repeat('df', 32))
  $q$, '23505', 'listing_chain', ARRAY['chain_listing_id', 'contract_address']));

-- =====================================================================
-- T19 · รายการขายอันเดียวมีงานค้างทีละงานเดียว
--       งานแรกคือการสั่งสร้างซ้ำ (งานเดิม CHAIN_REVERTED ไปแล้วใน T15) · คำขอถอดใช้ไม่ได้ที่นี่
--       เพราะรายการยัง PENDING และ FEAT-09 (0012) ถอดได้เฉพาะรายการ ACTIVE
-- =====================================================================
INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
VALUES ('acc_op_r1', 'merchant:acc_mch_a', 'LISTING_CREATE', 'acc_lst_1', '{}');
INSERT INTO outbox (id, operation_id, topic, payload)
VALUES ('acc_obx_r1', 'acc_op_r1', 'listing.create.v1', '{}');

SELECT pg_temp.rec_checks('T19', 'สั่งงานกับรายการขายเดิมซ้อนกัน → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
                        VALUES ('acc_op_r2', 'merchant:acc_mch_a', 'LISTING_CREATE', 'acc_lst_1', '{}')$q$,
    '23505', 'operation', ARRAY['subject_id'], ARRAY['uq_listing_inflight']));

-- =====================================================================
-- T20 · ราคาเก็บ uint256 สูงสุดได้ · เกินหรือรูปแบบอื่นไม่ได้      RFD 14 D1
-- =====================================================================
DO $$
DECLARE r text[];
BEGIN
  r[1] := pg_temp.try_ok(format($q$
    INSERT INTO listing (id, seller_kind, seller_id, coupon_type_id, unit_price, quantity)
    VALUES ('acc_lst_max', 'MERCHANT', 'acc_mch_a', 'acc_ct_1', %L, 1);
    INSERT INTO listing_item (id, listing_id, coupon_code_id) VALUES ('acc_li_max', 'acc_lst_max', 'acc_cc_9');
  $q$, (2::numeric ^ 256 - 1)::text));
  IF (SELECT unit_price FROM listing WHERE id = 'acc_lst_max') IS DISTINCT FROM (2::numeric ^ 256 - 1)::text THEN
    r[2] := 'FAIL: ค่าที่อ่านกลับไม่ตรง';
  END IF;
  r[3] := pg_temp.try_reject(format($q$INSERT INTO listing (id, seller_kind, seller_id, coupon_type_id, unit_price, quantity)
                                       VALUES ('acc_lst_over', 'MERCHANT', 'acc_mch_a', 'acc_ct_1', %L, 1)$q$,
                                    (2::numeric ^ 256)::text),
    '23514', 'listing', NULL, ARRAY['ck_listing_unit_price_uint256']);
  r[4] := pg_temp.try_reject($q$INSERT INTO listing (id, seller_kind, seller_id, coupon_type_id, unit_price, quantity)
                                 VALUES ('acc_lst_e', 'MERCHANT', 'acc_mch_a', 'acc_ct_1', '1e3', 1)$q$,
    '23514', 'listing', NULL, ARRAY['ck_listing_unit_price_uint256']);
  PERFORM pg_temp.rec_checks('T20', 'ราคา 2^256-1 เก็บได้ครบทุกหลัก · 2^256 · 1e3 → ถูกปฏิเสธ', VARIADIC r);
END $$;

-- =====================================================================
-- T21 · topic ของ outbox ต้องมีเวอร์ชันในชื่อ                RFD 7 D9
-- =====================================================================
SELECT pg_temp.rec_checks('T21', 'topic ที่ไม่มีเวอร์ชันต่อท้าย → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO outbox (id, operation_id, topic, payload)
                        VALUES ('acc_obx_bad', 'acc_op_2', 'listing.create', '{}')$q$,
    '23514', 'outbox', NULL, ARRAY['ck_outbox_topic_version']));

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
-- T23 · รายการขายที่ ACTIVE ต้องมีแถวฝั่ง chain
--       ตรวจข้อมูลทั้งฐาน · และลองตั้ง ACTIVE โดยไม่มีแถว chain → ถูกปฏิเสธตอน commit
-- T2b · พอรายการออกจาก PENDING แล้ว ใส่ใบเพิ่มไม่ได้
-- =====================================================================
UPDATE listing SET status = 'ACTIVE', active_at = now() WHERE id = 'acc_lst_1';

DO $$
DECLARE bad text; r text;
BEGIN
  r := pg_temp.try_reject($q$
         UPDATE listing SET status = 'ACTIVE', active_at = now() WHERE id = 'acc_lst_g';
         SET CONSTRAINTS trg_listing_active_has_chain IMMEDIATE;
       $q$, '23514', 'listing', NULL, ARRAY['trg_listing_active_has_chain']);
  SELECT string_agg(l.id, ',') INTO bad
    FROM listing l LEFT JOIN listing_chain c ON c.listing_id = l.id
   WHERE l.status = 'ACTIVE' AND c.listing_id IS NULL;
  PERFORM pg_temp.rec_checks('T23', 'ไม่มีรายการ ACTIVE ที่ไม่อยู่บน chain · ตั้ง ACTIVE โดยไม่มีแถว chain → ถูกปฏิเสธ',
    r, CASE WHEN bad IS NULL THEN NULL ELSE 'FAIL: ข้อมูลผิดกฎ ' || bad END);
END $$;
SELECT pg_temp.try_ok('SET CONSTRAINTS trg_listing_active_has_chain DEFERRED');

SELECT pg_temp.rec_checks('T2b', 'ใส่ใบเพิ่มในรายการที่ ACTIVE แล้ว → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO listing_item (id, listing_id, coupon_code_id)
                        VALUES ('acc_li_late', 'acc_lst_1', 'acc_cc_10')$q$,
    '23514', 'listing_item', NULL, ARRAY['trg_listing_item_valid']));

-- =====================================================================
-- T24 · ทุกใบในรายการขายที่ยังเปิด (PENDING / ACTIVE) อยู่ในมือผู้ขายจริง (ทั้งฐาน)
-- =====================================================================
DO $$
DECLARE bad text;
BEGIN
  SELECT string_agg(i.id, ',') INTO bad
    FROM listing_item i
    JOIN listing l ON l.id = i.listing_id
    JOIN coupon_code c ON c.id = i.coupon_code_id
   WHERE l.status IN ('PENDING', 'ACTIVE')
     AND (c.holder_kind IS DISTINCT FROM l.seller_kind OR c.holder_id IS DISTINCT FROM l.seller_id);
  PERFORM pg_temp.rec('T24', 'ทุกใบในรายการขายที่ยังเปิดอยู่ในมือผู้ขายจริง',
                      CASE WHEN bad IS NULL THEN 'PASS' ELSE 'FAIL' END, bad);
END $$;

-- =====================================================================
-- T25 · อ่าน log ช่วงเดิมซ้ำได้ · chain_event ไม่เกิดแถวซ้ำ        RFD 5 D2
-- =====================================================================
INSERT INTO raw_block (block_hash, block_number, parent_hash, "timestamp")
VALUES ('0x' || repeat('b8', 32), 900000002, '0x' || repeat('b6', 32), now());
-- FEAT-14 · บล็อกที่อ่านแล้วต้องอยู่ใต้ cursor ตอน commit (0017)
INSERT INTO indexer_cursor (id, last_block, last_block_hash)
VALUES ('main', 900000002, '0x' || repeat('b8', 32));

DO $$
DECLARE n1 int; n2 int;
BEGIN
  INSERT INTO chain_event (id, block_number, block_hash, tx_hash, log_index, address, topic0, topics,
                           data, block_timestamp)
  VALUES ('acc_ce_1', 900000002, '0x' || repeat('b8', 32), '0x' || repeat('e8', 32), 0,
          '0x' || repeat('d8', 20), '0x' || repeat('00', 32), '[]', '\x00', now())
  ON CONFLICT (tx_hash, log_index) DO NOTHING;
  GET DIAGNOSTICS n1 = ROW_COUNT;
  INSERT INTO chain_event (id, block_number, block_hash, tx_hash, log_index, address, topic0, topics,
                           data, block_timestamp)
  VALUES ('acc_ce_2', 900000002, '0x' || repeat('b8', 32), '0x' || repeat('e8', 32), 0,
          '0x' || repeat('d8', 20), '0x' || repeat('00', 32), '[]', '\x00', now())
  ON CONFLICT (tx_hash, log_index) DO NOTHING;
  GET DIAGNOSTICS n2 = ROW_COUNT;
  PERFORM pg_temp.rec('T25', 'อ่าน log ช่วงเดิมซ้ำ → chain_event ไม่เกิดแถวซ้ำ',
                      CASE WHEN n1 = 1 AND n2 = 0 THEN 'PASS' ELSE 'FAIL' END, format('ได้ %s · %s', n1, n2));
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
