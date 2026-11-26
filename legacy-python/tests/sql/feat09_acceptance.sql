-- =====================================================================
-- FEAT-09 · Delist · acceptance สำหรับ blockchain-reward-platform-be
--
-- ต้นฉบับ : blockchain-reward-platform/reference/feat-09/tests/acceptance.sql (34 ข้อ)
-- schema  : migrations 0002 → 0012_feat09_delist
--
-- รัน (Postgres ใน docker):
--   docker exec -i blockchain-reward-platform-be-postgres-1 \
--     psql -U reward -d reward < tests/sql/feat09_acceptance.sql
--
-- ผล  PASS ฐานข้อมูลบังคับกฎนี้ได้จริง · FAIL ไม่ได้ หรือถูกปฏิเสธด้วยกฎตัวอื่น
--     ERROR test เองรันไม่ได้ · ดูหมายเหตุ
--
-- ต่างจากต้นฉบับ · หลักฐานอยู่ใน docs/reference-findings.md
--   1. Marketplace จริงถอดด้วย delistCoupon(listingId) tx เดียว · contract คืนคูปองให้ผู้ขายเอง
--      ไม่มี cancelListing → releaseFromVault · T16 จึงตรวจว่าการถอดเป็น 1 step
--      และกฎลำดับ step (T18 T19) ทดสอบกับงานลงขาย setApprovalForAll → listCoupon
--   2. กฎลำดับ step ใช้กับงาน LISTING_CREATE / LISTING_DELETE / LISTING_BUY เท่านั้น
--      T19b · mint คูปองเป็นชุด (COUPON_ISSUE) ยิงชุดถัดไปได้โดยไม่ต้องรอชุดก่อน FINALIZED
--   3. T26b เพิ่ม · รายการขายต้องเริ่มที่ PENDING
--   4. T7 บันทึกผู้สั่งใน operation_event ตามคอลัมน์ของ backend (seq, metadata json)
--   5. hash / address เป็นสตริง hex '0x…' · ราคาและ chain_listing_id เป็นสตริง uint256
--   6. ข้อมูลทดสอบขึ้นต้นด้วย acc_ · ข้อ "ต้องถูกปฏิเสธ" ตรวจว่าชนกฎที่ตั้งใจจริง
--
-- ทั้งไฟล์อยู่ใน transaction เดียวแล้ว ROLLBACK · รันซ้ำกี่รอบก็ได้
-- =====================================================================
\set ON_ERROR_STOP on
\pset format aligned

BEGIN;

-- ควรได้ 0012_feat09_delist ขึ้นไป
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
INSERT INTO signer (address, key_ref) VALUES ('0x' || repeat('3c', 20), 'kms/acc-signer-1');

INSERT INTO coupon_type (id, merchant_id, name, name_normalized, face_value, total_supply, status,
                         expires_at, live_at)
VALUES ('acc_ct_1', 'acc_mch_a', 'คูปองส่วนลด 100', 'คูปองส่วนลด 100', '100000000000000000000', 100,
        'LIVE', now() + interval '180 days', now());
INSERT INTO coupon_type_chain (coupon_type_id, contract_address, chain_type_id, deployed_block, deployed_tx_hash)
VALUES ('acc_ct_1', '0x' || repeat('c9', 20), '1', 1001, '0x' || repeat('c1', 32));

-- ร้าน A ถือคูปอง 14 ใบ · ร้าน B ถือ 1 ใบ
INSERT INTO coupon_code (id, coupon_type_id, code_hash, code_ref, state, holder_kind, holder_id, held_at)
SELECT 'acc_cc_' || g, 'acc_ct_1', pg_temp.h('CODE-' || g), pg_temp.h('acc_cc_' || g),
       'HELD', 'MERCHANT', 'acc_mch_a', now()
  FROM generate_series(1, 14) g;
INSERT INTO coupon_code (id, coupon_type_id, code_hash, code_ref, state, holder_kind, holder_id, held_at)
VALUES ('acc_cc_b1', 'acc_ct_1', pg_temp.h('B1'), pg_temp.h('acc_cc_b1'), 'HELD', 'MERCHANT', 'acc_mch_b', now());

-- รายการขายที่ ACTIVE แล้ว · ผลของ FEAT-08 ที่ FINALIZED ไปเรียบร้อย
-- operation + outbox · รายการ + ใบ · จองคูปองและรายการ · แถว chain · แล้วค่อย ACTIVE
-- FEAT-13 · สถานะงานเดินทีละขั้นตามเส้นทางที่อนุญาต · event เกิดเองทุกขั้น
CREATE FUNCTION pg_temp.advance(p_op text, VARIADIC p_statuses text[]) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE s text;
BEGIN
  FOREACH s IN ARRAY p_statuses LOOP
    UPDATE operation SET status = s, finalized_at = CASE WHEN s = 'FINALIZED' THEN now() END WHERE id = p_op;
  END LOOP;
END $$;

CREATE FUNCTION pg_temp.mk_active(p_lst text, p_seller text, p_codes text[], p_chain_id text)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE c text; i int := 0;
BEGIN
  INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
  VALUES ('acc_opc_' || p_lst, 'merchant:' || p_seller, 'LISTING_CREATE', p_lst, '{}');
  PERFORM pg_temp.advance('acc_opc_' || p_lst, 'SUBMITTED', 'CONFIRMED', 'FINALIZED');
  INSERT INTO outbox (id, operation_id, topic, payload)
  VALUES ('acc_obc_' || p_lst, 'acc_opc_' || p_lst, 'listing.create.v1', '{}');
  INSERT INTO listing (id, seller_kind, seller_id, coupon_type_id, unit_price, quantity)
  VALUES (p_lst, 'MERCHANT', p_seller, 'acc_ct_1', '80000000000000000000', array_length(p_codes, 1));
  FOREACH c IN ARRAY p_codes LOOP
    i := i + 1;
    INSERT INTO listing_item (id, listing_id, coupon_code_id) VALUES (p_lst || '_i' || i, p_lst, c);
    INSERT INTO reservation (id, operation_id, subject_type, subject_id, amount)
    VALUES (p_lst || '_r' || i, 'acc_opc_' || p_lst, 'COUPON_UNIT', c, '1');
  END LOOP;
  INSERT INTO reservation (id, operation_id, subject_type, subject_id, amount)
  VALUES (p_lst || '_rL', 'acc_opc_' || p_lst, 'LISTING', p_lst, '1');
  INSERT INTO listing_chain (listing_id, contract_address, chain_listing_id, created_block, created_tx_hash)
  VALUES (p_lst, '0x' || repeat('d9', 20), p_chain_id, 1000, pg_temp.h(p_lst));
  UPDATE listing SET status = 'ACTIVE', active_at = now() WHERE id = p_lst;
END $$;

SELECT pg_temp.mk_active('acc_lst_a', 'acc_mch_a', ARRAY['acc_cc_1', 'acc_cc_2', 'acc_cc_3'], '9001');
SELECT pg_temp.mk_active('acc_lst_b', 'acc_mch_b', ARRAY['acc_cc_b1'], '9002');
SELECT pg_temp.mk_active('acc_lst_c', 'acc_mch_a', ARRAY['acc_cc_5'],  '9003');
SELECT pg_temp.mk_active('acc_lst_d', 'acc_mch_a', ARRAY['acc_cc_6'],  '9004');
SELECT pg_temp.mk_active('acc_lst_e', 'acc_mch_a', ARRAY['acc_cc_7'],  '9005');
SELECT pg_temp.mk_active('acc_lst_f', 'acc_mch_a', ARRAY['acc_cc_8'],  '9006');
SELECT pg_temp.mk_active('acc_lst_g', 'acc_mch_a', ARRAY['acc_cc_9'],  '9007');
SELECT pg_temp.mk_active('acc_lst_h', 'acc_mch_a', ARRAY['acc_cc_10'], '9008');
SELECT pg_temp.mk_active('acc_lst_i', 'acc_mch_a', ARRAY['acc_cc_11'], '9009');

-- รายการที่ยังไม่ขึ้น chain · คำขอสร้างยังค้าง (SUBMITTED)
INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
VALUES ('acc_opc_lst_p', 'merchant:acc_mch_a', 'LISTING_CREATE', 'acc_lst_p', '{}');
SELECT pg_temp.advance('acc_opc_lst_p', 'SUBMITTED');
INSERT INTO outbox (id, operation_id, topic, payload)
VALUES ('acc_obc_lst_p', 'acc_opc_lst_p', 'listing.create.v1', '{}');
INSERT INTO listing (id, seller_kind, seller_id, coupon_type_id, unit_price, quantity)
VALUES ('acc_lst_p', 'MERCHANT', 'acc_mch_a', 'acc_ct_1', '80000000000000000000', 1);
INSERT INTO listing_item (id, listing_id, coupon_code_id) VALUES ('acc_lst_p_i1', 'acc_lst_p', 'acc_cc_12');

-- คำขอถอด · คืน SQL ของ INSERT operation
CREATE FUNCTION pg_temp.delist(p_op text, p_scope text, p_lst text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT format($q$INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
                   VALUES (%L, %L, 'LISTING_DELETE', %L, '{}')$q$, p_op, p_scope, p_lst)
$$;

-- =====================================================================
-- T1 · ส่ง Idempotency-Key เดิมซ้ำ → ไม่เกิดงานใหม่            RFD 3 R1
-- =====================================================================
DO $$
DECLARE n1 int; n2 int;
BEGIN
  INSERT INTO idempotency_key (id, scope, key, fingerprint, expires_at)
  VALUES ('acc_ik_1', 'merchant:acc_mch_a', 'delist-11111111-2222-3333', pg_temp.h('d'), now() + interval '1 day')
  ON CONFLICT (scope, key) DO NOTHING;
  GET DIAGNOSTICS n1 = ROW_COUNT;
  INSERT INTO idempotency_key (id, scope, key, fingerprint, expires_at)
  VALUES ('acc_ik_2', 'merchant:acc_mch_a', 'delist-11111111-2222-3333', pg_temp.h('d'), now() + interval '1 day')
  ON CONFLICT (scope, key) DO NOTHING;
  GET DIAGNOSTICS n2 = ROW_COUNT;
  PERFORM pg_temp.rec('T1', 'ถอดซ้ำด้วย key เดิม → แถวที่สองได้ 0 rows',
                      CASE WHEN n1 = 1 AND n2 = 0 THEN 'PASS' ELSE 'FAIL' END, format('ได้ %s · %s', n1, n2));
END $$;

-- =====================================================================
-- T2 · ถอดรายการที่ ACTIVE และเป็นของตัวเอง → ยอมรับ
-- T3 · F5 · ถอดรายการของร้านอื่น → ถูกปฏิเสธที่ฐานข้อมูล
-- T4 · F5 · ถอดรายการที่ไม่มีอยู่ → ถูกปฏิเสธ (ไม่ใช่ "ถือว่าเป็นของคนขอ")
-- T5 · ไม่ระบุ subject_id → ถูกปฏิเสธ
-- T6 · scope ลูกค้าถอดรายการของร้าน (id ตรงกัน แต่คนละชนิด) → ถูกปฏิเสธ
-- =====================================================================
SELECT pg_temp.rec_checks('T2', 'ถอดรายการ ACTIVE ของตัวเอง → ยอมรับ',
  pg_temp.try_ok(pg_temp.delist('acc_opd_a', 'merchant:acc_mch_a', 'acc_lst_a')));
INSERT INTO outbox (id, operation_id, topic, payload)
SELECT 'acc_obd_a', 'acc_opd_a', 'listing.delete.v1', '{}' WHERE EXISTS (SELECT 1 FROM operation WHERE id = 'acc_opd_a');

SELECT pg_temp.rec_checks('T3', 'ถอดรายการของร้านอื่น → ถูกปฏิเสธที่ฐานข้อมูล',
  pg_temp.try_reject(pg_temp.delist('acc_opd_b', 'merchant:acc_mch_a', 'acc_lst_b'),
    '23514', 'operation', NULL, ARRAY['trg_delist_admissible']));
SELECT pg_temp.rec_checks('T4', 'ถอดรายการที่ไม่มีอยู่ → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.delist('acc_opd_x', 'merchant:acc_mch_a', 'acc_lst_ไม่มีจริง'),
    '23514', 'operation', NULL, ARRAY['trg_delist_admissible']));
SELECT pg_temp.rec_checks('T5', 'ถอดโดยไม่ระบุ subject_id → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.delist('acc_opd_n', 'merchant:acc_mch_a', NULL),
    '23514', 'operation', NULL, ARRAY['trg_delist_admissible']));
SELECT pg_temp.rec_checks('T6', 'scope customer ที่ id ตรงกับร้าน ถอดรายการของร้าน → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.delist('acc_opd_c1', 'customer:acc_mch_a', 'acc_lst_c'),
    '23514', 'operation', NULL, ARRAY['trg_delist_admissible']));

-- =====================================================================
-- T7 · platform admin ถอดได้ทุกใบ · และบันทึกว่าใครสั่ง
-- T8 · actor_scope รูปแบบเพี้ยน → ถูกปฏิเสธ
-- =====================================================================
DO $$
DECLARE r text[];
BEGIN
  -- ผู้สั่งส่งผ่าน set_config · ฐานข้อมูลเขียน operation_event เอง (FEAT-13)
  r[1] := pg_temp.try_ok(
    $q$SELECT set_config('app.operation_actor', 'PLATFORM_ADMIN', true),
              set_config('app.operation_event_metadata', '{"by": "acc_adm_7"}', true);$q$
    || pg_temp.delist('acc_opd_adm', 'admin:acc_adm_7', 'acc_lst_c')
    || $q$; SELECT set_config('app.operation_actor', '', true),
                   set_config('app.operation_event_metadata', '', true);$q$);
  r[2] := pg_temp.try_ok($q$
    INSERT INTO outbox (id, operation_id, topic, payload)
    VALUES ('acc_obd_adm', 'acc_opd_adm', 'listing.delete.v1', '{}');
  $q$);
  IF (SELECT count(*) FROM operation_event WHERE operation_id = 'acc_opd_adm' AND actor = 'PLATFORM_ADMIN') <> 1 THEN
    r[3] := 'FAIL: ไม่มีบันทึกผู้สั่ง';
  ELSIF (SELECT metadata ->> 'by' FROM operation_event WHERE operation_id = 'acc_opd_adm' AND seq = 1)
        IS DISTINCT FROM 'acc_adm_7' THEN
    r[3] := 'FAIL: ไม่มีชื่อ admin ที่สั่ง';
  END IF;
  PERFORM pg_temp.rec_checks('T7', 'admin ถอดรายการของร้านได้ · บันทึกผู้สั่งใน operation_event', VARIADIC r);
END $$;

SELECT pg_temp.rec_checks('T8', 'actor_scope ที่ไม่มีชนิดนำหน้า (mch_a) → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.delist('acc_opd_bad', 'acc_mch_a', 'acc_lst_d'),
    '23514', 'operation', NULL, ARRAY['ck_operation_actor_scope']),
  pg_temp.try_reject(pg_temp.delist('acc_opd_bad2', 'merchant:', 'acc_lst_d'),
    '23514', 'operation', NULL, ARRAY['ck_operation_actor_scope']));

-- =====================================================================
-- T9  · ถอดรายการที่ยัง PENDING → ถูกปฏิเสธ (ยังไม่มีอะไรบน chain ให้ยกเลิก)
--       ทดสอบหลังงานสร้างจบแล้ว เพื่อให้ชนเงื่อนไขสถานะจริง ไม่ใช่ uq_listing_inflight
-- T10 · SOLD แล้ว → ถูกปฏิเสธ · T11 · DELISTED แล้ว → ถูกปฏิเสธ
-- =====================================================================
DO $$
DECLARE r text;
BEGIN
  UPDATE operation SET status = 'CHAIN_REVERTED' WHERE id = 'acc_opc_lst_p';
  r := pg_temp.try_reject(pg_temp.delist('acc_opd_p', 'merchant:acc_mch_a', 'acc_lst_p'),
    '23514', 'operation', NULL, ARRAY['trg_delist_admissible']);
  PERFORM pg_temp.rec_checks('T9', 'ถอดรายการที่ยัง PENDING → ถูกปฏิเสธ', r);
END $$;

UPDATE listing SET status = 'SOLD', closed_at = now() WHERE id = 'acc_lst_d';
UPDATE listing SET status = 'DELISTED', closed_at = now() WHERE id = 'acc_lst_e';
SELECT pg_temp.rec_checks('T10', 'ถอดรายการที่ SOLD แล้ว → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.delist('acc_opd_d', 'merchant:acc_mch_a', 'acc_lst_d'),
    '23514', 'operation', NULL, ARRAY['trg_delist_admissible']));
SELECT pg_temp.rec_checks('T11', 'ถอดรายการที่ DELISTED แล้ว → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.delist('acc_opd_e', 'merchant:acc_mch_a', 'acc_lst_e'),
    '23514', 'operation', NULL, ARRAY['trg_delist_admissible']));

-- =====================================================================
-- T12 · สองคำขอถอดใบเดียวกัน (ต่าง key) → ตัวที่สองชน uq_listing_inflight
-- T13 · ลูกค้ากดซื้อค้างอยู่ ร้านกดถอด → ถอดแพ้ที่ฐานข้อมูล
-- T14 · งานซื้อที่ MANUAL_REVIEW ยังกันงานใหม่
-- T15 · งานซื้อที่ CHAIN_REVERTED ไม่กันงานใหม่
-- =====================================================================
SELECT pg_temp.rec_checks('T12', 'คำขอถอดซ้อนบนรายการเดียวกัน → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.delist('acc_opd_a2', 'merchant:acc_mch_a', 'acc_lst_a'),
    '23505', 'operation', ARRAY['subject_id'], ARRAY['uq_listing_inflight']));

INSERT INTO operation (id, actor_scope, kind, subject_id, payload) VALUES
  ('acc_opb_f', 'customer:acc_cus_1', 'LISTING_BUY', 'acc_lst_f', '{}'),
  ('acc_opb_g', 'customer:acc_cus_1', 'LISTING_BUY', 'acc_lst_g', '{}'),
  ('acc_opb_h', 'customer:acc_cus_1', 'LISTING_BUY', 'acc_lst_h', '{}');
SELECT pg_temp.advance('acc_opb_f', 'SUBMITTED');
SELECT pg_temp.advance('acc_opb_g', 'SUBMITTED', 'MANUAL_REVIEW');
SELECT pg_temp.advance('acc_opb_h', 'SUBMITTED', 'CHAIN_REVERTED');
INSERT INTO outbox (id, operation_id, topic, payload) VALUES
  ('acc_obb_f', 'acc_opb_f', 'listing.buy.v1', '{}'),
  ('acc_obb_g', 'acc_opb_g', 'listing.buy.v1', '{}'),
  ('acc_obb_h', 'acc_opb_h', 'listing.buy.v1', '{}');

SELECT pg_temp.rec_checks('T13', 'มีงานซื้อค้าง (SUBMITTED) แล้วร้านกดถอด → ถอดแพ้',
  pg_temp.try_reject(pg_temp.delist('acc_opd_f', 'merchant:acc_mch_a', 'acc_lst_f'),
    '23505', 'operation', ARRAY['subject_id'], ARRAY['uq_listing_inflight']));
SELECT pg_temp.rec_checks('T14', 'งานซื้อที่ MANUAL_REVIEW ยังกันคำขอถอด',
  pg_temp.try_reject(pg_temp.delist('acc_opd_g', 'merchant:acc_mch_a', 'acc_lst_g'),
    '23505', 'operation', ARRAY['subject_id'], ARRAY['uq_listing_inflight']));
SELECT pg_temp.rec_checks('T15', 'งานซื้อที่ CHAIN_REVERTED ไม่กันคำขอถอด',
  pg_temp.try_ok(pg_temp.delist('acc_opd_h', 'merchant:acc_mch_a', 'acc_lst_h')));
INSERT INTO outbox (id, operation_id, topic, payload)
SELECT 'acc_obd_h', 'acc_opd_h', 'listing.delete.v1', '{}' WHERE EXISTS (SELECT 1 FROM operation WHERE id = 'acc_opd_h');

-- =====================================================================
-- T16 · ถอด = 1 step (delistCoupon) · ลงขาย = 2 step เรียงกัน (setApprovalForAll → listCoupon)
-- T17 · step_index ซ้ำใน operation เดียว → ชน uq_step_order
-- T18 · ยิง step 2 ก่อน step 1 FINALIZED → ถูกปฏิเสธ
-- T19 · step 1 FINALIZED แล้ว step 2 ยิงได้
-- T19b · mint เป็นชุด (COUPON_ISSUE) ไม่ต้องรอชุดก่อน
-- =====================================================================
INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
VALUES ('acc_opc_q', 'merchant:acc_mch_a', 'LISTING_CREATE', 'acc_lst_q', '{}');
INSERT INTO outbox (id, operation_id, topic, payload) VALUES ('acc_obc_q', 'acc_opc_q', 'listing.create.v1', '{}');

DO $$
DECLARE r text[]; v text[];
BEGIN
  r[1] := pg_temp.try_ok($q$
    INSERT INTO operation_step (id, operation_id, step_index, name)
    VALUES ('acc_st_a1', 'acc_opd_a', 1, 'delistCoupon');
    INSERT INTO operation_step (id, operation_id, step_index, name) VALUES
      ('acc_st_q1', 'acc_opc_q', 1, 'setApprovalForAll'),
      ('acc_st_q2', 'acc_opc_q', 2, 'listCoupon');
  $q$);
  SELECT array_agg(name ORDER BY step_index) INTO v FROM operation_step WHERE operation_id = 'acc_opd_a';
  IF v IS DISTINCT FROM ARRAY['delistCoupon'] THEN r[2] := 'FAIL: ถอดได้ ' || coalesce(array_to_string(v, ','), '-'); END IF;
  SELECT array_agg(name ORDER BY step_index) INTO v FROM operation_step WHERE operation_id = 'acc_opc_q';
  IF v IS DISTINCT FROM ARRAY['setApprovalForAll', 'listCoupon'] THEN
    r[3] := 'FAIL: ลงขายได้ ' || coalesce(array_to_string(v, ','), '-');
  END IF;
  PERFORM pg_temp.rec_checks('T16', 'ถอดมี step เดียว delistCoupon · ลงขายมี setApprovalForAll → listCoupon', VARIADIC r);
END $$;

SELECT pg_temp.rec_checks('T17', 'step_index ซ้ำใน operation เดียว → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO operation_step (id, operation_id, step_index, name)
                        VALUES ('acc_st_dup', 'acc_opc_q', 1, 'setApprovalForAllAgain')$q$,
    '23505', 'operation_step', ARRAY['operation_id', 'step_index']));

SELECT pg_temp.rec_checks('T18', 'ยิง listCoupon ก่อน setApprovalForAll FINALIZED → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE operation_step SET status = 'SUBMITTED', raw_tx = '\xdead', submitted_at = now()
                        WHERE id = 'acc_st_q2'$q$,
    '23514', 'operation_step', NULL, ARRAY['trg_step_order_gate']),
  pg_temp.try_reject($q$INSERT INTO operation_step (id, operation_id, step_index, name, status, raw_tx)
                        VALUES ('acc_st_q3', 'acc_opc_q', 3, 'extra', 'SUBMITTED', '\xdead')$q$,
    '23514', 'operation_step', NULL, ARRAY['trg_step_order_gate']));

DO $$
DECLARE r text[];
BEGIN
  r[1] := pg_temp.try_ok($q$
    UPDATE operation_step SET status = 'SUBMITTED', raw_tx = '\xaa01', submitted_at = now() WHERE id = 'acc_st_q1';
    UPDATE operation_step SET status = 'FINALIZED', tx_hash = '0x' || repeat('a1', 32), finalized_at = now()
     WHERE id = 'acc_st_q1';
  $q$);
  r[2] := pg_temp.try_ok($q$UPDATE operation_step SET status = 'SUBMITTED', raw_tx = '\xaa02', submitted_at = now()
                            WHERE id = 'acc_st_q2'$q$);
  PERFORM pg_temp.rec_checks('T19', 'step 1 FINALIZED แล้ว step 2 ยิงได้', VARIADIC r);
END $$;

INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
VALUES ('acc_opi', 'merchant:acc_mch_a', 'COUPON_ISSUE', 'acc_ct_1', '{}');
INSERT INTO outbox (id, operation_id, topic, payload) VALUES ('acc_obi', 'acc_opi', 'coupon.issue.v1', '{}');
INSERT INTO operation_step (id, operation_id, step_index, name, mint_count) VALUES
  ('acc_st_m1', 'acc_opi', 1, 'mintCoupon-1', 500),
  ('acc_st_m2', 'acc_opi', 2, 'mintCoupon-2', 500);

SELECT pg_temp.rec_checks('T19b', 'mint ชุดที่ 2 ยิงได้ขณะชุดที่ 1 ยัง SUBMITTED (งานที่ไม่ขึ้นต่อกัน)',
  pg_temp.try_ok($q$
    UPDATE operation_step SET status = 'SUBMITTED', raw_tx = '\xbb01' WHERE id = 'acc_st_m1';
    UPDATE operation_step SET status = 'SUBMITTED', raw_tx = '\xbb02' WHERE id = 'acc_st_m2';
  $q$));

-- =====================================================================
-- T20 · R2 · สร้าง step เป็น SUBMITTED โดยไม่มี raw_tx → ถูกปฏิเสธ
-- =====================================================================
SELECT pg_temp.rec_checks('T20', 'step SUBMITTED โดยไม่มี raw_tx → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO operation_step (id, operation_id, step_index, name, status)
                        VALUES ('acc_st_nr', 'acc_opd_adm', 1, 'delistCoupon', 'SUBMITTED')$q$,
    '23514', 'operation_step', NULL, ARRAY['ck_rawtx_before_broadcast']));

-- =====================================================================
-- T21 · ปล่อยจองคูปองขณะรายการยัง ACTIVE → ถูกปฏิเสธ
--       (งานลงขาย FINALIZED ไปนานแล้ว · trg_reservation_release_timing จึงไม่กัน)
-- T22 · ถอดจบ · รายการ DELISTED · ปล่อยคูปองได้
-- T23 · closed_at ถูกเติมให้เองเมื่อปิดรายการ
-- T24 · คูปองไม่เปลี่ยน state ตลอดการลงขายและถอด
-- =====================================================================
SELECT pg_temp.rec_checks('T21', 'ปล่อยจองคูปองขณะรายการยัง ACTIVE → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE reservation SET released_at = now() WHERE id = 'acc_lst_a_r1'$q$,
    '23514', 'reservation', NULL, ARRAY['trg_coupon_hold_until_closed']));

DO $$
DECLARE r text[];
BEGIN
  r[1] := pg_temp.try_ok($q$
    UPDATE operation_step SET status = 'SUBMITTED', raw_tx = '\xcc01', submitted_at = now() WHERE id = 'acc_st_a1';
    UPDATE operation_step SET status = 'FINALIZED', tx_hash = '0x' || repeat('a2', 32), finalized_at = now()
     WHERE id = 'acc_st_a1';
    SELECT pg_temp.advance('acc_opd_a', 'SUBMITTED', 'CONFIRMED', 'FINALIZED');
    UPDATE listing_chain SET closed_block = 1100, close_reason = 'CANCELLED',
           closed_tx_hash = '0x' || repeat('a2', 32) WHERE listing_id = 'acc_lst_a';
  $q$);
  -- ไม่ใส่ closed_at · ให้ฐานข้อมูลเติมเอง (T23)
  r[2] := pg_temp.try_ok($q$UPDATE listing SET status = 'DELISTED' WHERE id = 'acc_lst_a'$q$);
  r[3] := pg_temp.try_ok($q$UPDATE reservation SET released_at = now()
                            WHERE operation_id = 'acc_opc_acc_lst_a' AND released_at IS NULL$q$);
  IF (SELECT count(*) FROM reservation WHERE operation_id = 'acc_opc_acc_lst_a' AND released_at IS NOT NULL) <> 4 THEN
    r[4] := 'FAIL: ปล่อยไม่ครบ 4 แถว (3 ใบ + รายการ)';
  END IF;
  PERFORM pg_temp.rec_checks('T22', 'ถอดจบ → DELISTED → ปล่อยจองคูปองและรายการได้ครบ', VARIADIC r);
END $$;

DO $$
DECLARE v timestamptz; a timestamptz;
BEGIN
  SELECT closed_at, active_at INTO v, a FROM listing WHERE id = 'acc_lst_a';
  PERFORM pg_temp.rec('T23', 'ปิดรายการแล้ว closed_at ถูกเติมให้เอง และไม่ก่อน active_at',
    CASE WHEN v IS NOT NULL AND v >= a THEN 'PASS' ELSE 'FAIL' END, format('closed_at %s', v));
END $$;

DO $$
DECLARE n int;
BEGIN
  SELECT count(*) INTO n FROM coupon_code
   WHERE id IN ('acc_cc_1', 'acc_cc_2', 'acc_cc_3')
     AND state = 'HELD' AND holder_kind = 'MERCHANT' AND holder_id = 'acc_mch_a';
  PERFORM pg_temp.rec('T24', 'คูปองยัง HELD ในมือร้านเดิม ตลอดการลงขายและถอด',
                      CASE WHEN n = 3 THEN 'PASS' ELSE 'FAIL' END, format('%s/3', n));
END $$;

-- =====================================================================
-- T25 · ปล่อยจองแล้ว คูปองใบเดิมลงขายรายการใหม่ได้
-- T26 · DELISTED → ACTIVE ไม่ได้
-- T26b · รายการใหม่ต้องเริ่มที่ PENDING
-- =====================================================================
SELECT pg_temp.rec_checks('T25', 'ถอดแล้ว คูปองใบเดิมลงขายรายการใหม่และจองใหม่ได้',
  pg_temp.try_ok($q$
    INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
    VALUES ('acc_opc_new', 'merchant:acc_mch_a', 'LISTING_CREATE', 'acc_lst_new', '{}');
    INSERT INTO outbox (id, operation_id, topic, payload) VALUES ('acc_obc_new', 'acc_opc_new', 'listing.create.v1', '{}');
    INSERT INTO listing (id, seller_kind, seller_id, coupon_type_id, unit_price, quantity)
    VALUES ('acc_lst_new', 'MERCHANT', 'acc_mch_a', 'acc_ct_1', '90000000000000000000', 1);
    INSERT INTO listing_item (id, listing_id, coupon_code_id) VALUES ('acc_li_new', 'acc_lst_new', 'acc_cc_1');
    INSERT INTO reservation (id, operation_id, subject_type, subject_id, amount)
    VALUES ('acc_rsv_new', 'acc_opc_new', 'COUPON_UNIT', 'acc_cc_1', '1');
  $q$));

SELECT pg_temp.rec_checks('T26', 'DELISTED → ACTIVE (ปลุกรายการที่ถอดแล้ว) → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE listing SET status = 'ACTIVE', closed_at = NULL WHERE id = 'acc_lst_a'$q$,
    '23514', 'listing', NULL, ARRAY['trg_listing_status_transition']));

SELECT pg_temp.rec_checks('T26b', 'สร้างรายการใหม่ที่สถานะ ACTIVE ทันที → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO listing (id, seller_kind, seller_id, coupon_type_id, unit_price, quantity,
                                             status, active_at)
                        VALUES ('acc_lst_born_active', 'MERCHANT', 'acc_mch_a', 'acc_ct_1', '1', 1, 'ACTIVE', now())$q$,
    '23514', 'listing', NULL, ARRAY['trg_listing_status_transition']));

-- =====================================================================
-- T27 · มีคนซื้อตัดหน้า · ถอด CHAIN_REVERTED แล้วรายการกลายเป็น SOLD (ผลปกติ ไม่ใช่ error)
-- T28 · ปิดด้วยเหตุผลหนึ่งแล้ว เขียนทับด้วยอีกเหตุผลไม่ได้
-- =====================================================================
DO $$
DECLARE r text[]; v text; c text;
BEGIN
  r[1] := pg_temp.try_ok($q$
    INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
    VALUES ('acc_opd_i', 'merchant:acc_mch_a', 'LISTING_DELETE', 'acc_lst_i', '{}');
    INSERT INTO outbox (id, operation_id, topic, payload) VALUES ('acc_obd_i', 'acc_opd_i', 'listing.delete.v1', '{}');
    UPDATE operation SET status = 'SUBMITTED' WHERE id = 'acc_opd_i';
    UPDATE operation SET status = 'CHAIN_REVERTED', error_note = 'delistCoupon reverted · listing sold'
     WHERE id = 'acc_opd_i';
    UPDATE listing_chain SET closed_block = 1200, close_reason = 'SOLD', closed_tx_hash = '0x' || repeat('b9', 32)
     WHERE listing_id = 'acc_lst_i';
  $q$);
  r[2] := pg_temp.try_ok($q$UPDATE listing SET status = 'SOLD' WHERE id = 'acc_lst_i'$q$);
  SELECT l.status, ch.close_reason INTO v, c FROM listing l JOIN listing_chain ch ON ch.listing_id = l.id
   WHERE l.id = 'acc_lst_i';
  IF v IS DISTINCT FROM 'SOLD' OR c IS DISTINCT FROM 'SOLD' THEN r[3] := format('FAIL: ได้ %s / %s', v, c); END IF;
  PERFORM pg_temp.rec_checks('T27', 'ถอดแพ้คนซื้อ · operation CHAIN_REVERTED · รายการ SOLD', VARIADIC r);
END $$;

SELECT pg_temp.rec_checks('T28', 'เปลี่ยน close_reason SOLD เป็น CANCELLED ย้อนหลัง → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE listing_chain SET close_reason = 'CANCELLED' WHERE listing_id = 'acc_lst_i'$q$,
    '23514', 'listing_chain', NULL, ARRAY['trg_listing_chain_immutable']));

-- =====================================================================
-- T29 · outbox topic ต้องมีเวอร์ชันลงท้าย
-- T30 · outbox ที่ยังไม่ส่ง หาได้ด้วยเงื่อนไข sent_at IS NULL
-- =====================================================================
SELECT pg_temp.rec_checks('T29', 'topic ที่ไม่มีเวอร์ชันต่อท้าย → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO outbox (id, operation_id, topic, payload)
                        VALUES ('acc_obx_bad', 'acc_opd_a', 'listing.delete', '{}')$q$,
    '23514', 'outbox', NULL, ARRAY['ck_outbox_topic_version']));

DO $$
DECLARE n int;
BEGIN
  UPDATE outbox SET sent_at = now() WHERE id = 'acc_obd_a';
  SELECT count(*) INTO n FROM outbox WHERE sent_at IS NULL AND id IN ('acc_obd_a', 'acc_obd_adm');
  PERFORM pg_temp.rec('T30', 'outbox ที่ส่งแล้วไม่ถูกหยิบซ้ำ · เหลือที่ยังไม่ส่ง 1 แถว',
                      CASE WHEN n = 1 THEN 'PASS' ELSE 'FAIL' END, format('%s แถว', n));
END $$;

-- =====================================================================
-- T31 · tx_hash ซ้ำข้าม operation → ชน uq_step_txhash
-- T32 · nonce ซ้ำของ signer เดียวกัน → ชน uq_signer_nonce
-- T33 · chain_listing_id เปลี่ยนไม่ได้
-- =====================================================================
SELECT pg_temp.rec_checks('T31', 'tx_hash เดียวกันในสอง operation → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO operation_step (id, operation_id, step_index, name, status, raw_tx, tx_hash)
                        VALUES ('acc_st_h1', 'acc_opd_h', 1, 'delistCoupon', 'FINALIZED', '\xbb', '0x' || repeat('a1', 32))$q$,
    '23505', 'operation_step', ARRAY['tx_hash']));

SELECT pg_temp.rec_checks('T32', 'nonce เดียวกันของ signer เดียวกัน → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO operation_step (id, operation_id, step_index, name, status, raw_tx, signer_address, nonce) VALUES
      ('acc_st_n1', 'acc_opd_h',   1, 'delistCoupon', 'SUBMITTED', '\xc1', '0x' || repeat('3c', 20), 41),
      ('acc_st_n2', 'acc_opd_adm', 1, 'delistCoupon', 'SUBMITTED', '\xc2', '0x' || repeat('3c', 20), 41)
  $q$, '23505', 'operation_step', ARRAY['nonce', 'signer_address']));

SELECT pg_temp.rec_checks('T33', 'แก้ chain_listing_id → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE listing_chain SET chain_listing_id = '999999' WHERE listing_id = 'acc_lst_c'$q$,
    '23514', 'listing_chain', NULL, ARRAY['trg_listing_chain_immutable']));

-- =====================================================================
-- T34 · อ่าน log ช่วงเดิมซ้ำได้ · chain_event ไม่เกิดแถวซ้ำ        RFD 5 D2
-- =====================================================================
INSERT INTO raw_block (block_hash, block_number, parent_hash, "timestamp")
VALUES ('0x' || repeat('b9', 32), 900000003, '0x' || repeat('b6', 32), now());
-- FEAT-14 · บล็อกที่อ่านแล้วต้องอยู่ใต้ cursor ตอน commit (0017)
INSERT INTO indexer_cursor (id, last_block, last_block_hash)
VALUES ('main', 900000003, '0x' || repeat('b9', 32));

DO $$
DECLARE n1 int; n2 int;
BEGIN
  INSERT INTO chain_event (id, block_number, block_hash, tx_hash, log_index, address, topic0, topics,
                           data, block_timestamp)
  VALUES ('acc_ce_1', 900000003, '0x' || repeat('b9', 32), '0x' || repeat('e9', 32), 0,
          '0x' || repeat('d9', 20), '0x' || repeat('00', 32), '[]', '\x00', now())
  ON CONFLICT (tx_hash, log_index) DO NOTHING;
  GET DIAGNOSTICS n1 = ROW_COUNT;
  INSERT INTO chain_event (id, block_number, block_hash, tx_hash, log_index, address, topic0, topics,
                           data, block_timestamp)
  VALUES ('acc_ce_2', 900000003, '0x' || repeat('b9', 32), '0x' || repeat('e9', 32), 0,
          '0x' || repeat('d9', 20), '0x' || repeat('00', 32), '[]', '\x00', now())
  ON CONFLICT (tx_hash, log_index) DO NOTHING;
  GET DIAGNOSTICS n2 = ROW_COUNT;
  PERFORM pg_temp.rec('T34', 'อ่าน log ช่วงเดิมซ้ำ → chain_event ไม่เกิดแถวซ้ำ',
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
