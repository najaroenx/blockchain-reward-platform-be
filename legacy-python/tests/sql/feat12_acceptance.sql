-- =====================================================================
-- FEAT-12 · THB Deposit & Mint · acceptance สำหรับ blockchain-reward-platform-be
--
-- ต้นฉบับ : blockchain-reward-platform/reference/feat-12/tests/acceptance.sql (34 ข้อ)
-- schema  : migrations 0002 → 0015_feat12_deposit_mint
--
-- รัน (Postgres ใน docker):
--   docker exec -i blockchain-reward-platform-be-postgres-1 \
--     psql -U reward -d reward < tests/sql/feat12_acceptance.sql
--
-- ผล  PASS ฐานข้อมูลบังคับกฎนี้ได้จริง · FAIL ไม่ได้ หรือถูกปฏิเสธด้วยกฎตัวอื่น
--     ERROR test เองรันไม่ได้ · ดูหมายเหตุ
--
-- ต่างจากต้นฉบับ · หลักฐานอยู่ใน docs/reference-findings.md
--   1. ISSUANCE ต้องมีที่มาจริง ตรวจตอน commit · mint = แถว thb_mint ของการฝากที่ยืนยันแล้ว
--      burn = การตีกลับ BURNED · ต้นฉบับตรวจแค่ชนิด operation (T16b T16c เพิ่ม)
--   2. available_at_reversal ฐานข้อมูลคำนวณเองแล้วเทียบ · ไม่เชื่อค่าที่ส่งมา (T22b เพิ่ม)
--   3. webhook หนึ่งตัวยืนยันได้หนึ่งการฝาก และต้องลายเซ็นผ่าน (T7b T7c เพิ่ม)
--      fixture ต้นฉบับใช้ wh_2 ยืนยันสองการฝาก · ที่นี่แยก webhook
--   4. สถานะต้องมีหลักฐาน · MINTED ต้องมี thb_mint · REVERSED ต้องมีการตีกลับ BURNED (T15b T23b เพิ่ม)
--      thb_mint ต้องอ้าง THB_MINT operation ของการฝากนั้น (T10b เพิ่ม)
--   5. T16 ใช้ operation ชนิดอื่นที่ไม่ใช่ LISTING_BUY (0013 ตรวจว่า LISTING_BUY อ้าง listing จริง)
--      ยอดที่ถูกกันไว้ของลูกค้าใน T22 ก็เช่นกัน
--   6. step ของ mint ชื่อ mint ตาม ABI ของ THB (mint(to, amount))
--   7. จำนวนเงินเป็นสตริง · ข้อมูลทดสอบขึ้นต้นด้วย acc_ · ข้อ "ต้องถูกปฏิเสธ" ตรวจว่าชนกฎที่ตั้งใจจริง
--
-- ทั้งไฟล์อยู่ใน transaction เดียวแล้ว ROLLBACK · รันซ้ำกี่รอบก็ได้
-- =====================================================================
\set ON_ERROR_STOP on
\pset format aligned

BEGIN;

-- ควรได้ 0015_feat12_deposit_mint ขึ้นไป
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


-- 1 บาท = 100 สตางค์ = 10^18 base units · 1 สตางค์ = 10^16
CREATE FUNCTION pg_temp.satang(p_baht numeric) RETURNS bigint LANGUAGE sql IMMUTABLE AS
$$ SELECT (p_baht * 100)::bigint $$;
CREATE FUNCTION pg_temp.base(p_satang bigint) RETURNS text LANGUAGE sql IMMUTABLE AS
$$ SELECT (p_satang::numeric * 10000000000000000)::text $$;

CREATE FUNCTION pg_temp.webhook(p_id text, p_evt text, p_type text, p_ok boolean DEFAULT true)
RETURNS void LANGUAGE sql AS $$
  INSERT INTO psp_webhook (id, provider, provider_event_id, event_type, raw_body, signature, signature_ok)
  VALUES (p_id, 'acc_psp', p_evt, p_type, convert_to('{"id":"' || p_evt || '"}', 'UTF8'), 'sig_' || p_evt, p_ok)
$$;

-- การฝากที่ยืนยันแล้ว
CREATE FUNCTION pg_temp.confirmed(p_dep text, p_holder text, p_satang bigint, p_wh text)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO thb_deposit (id, holder_kind, holder_id, provider, provider_ref, amount_satang, amount_base, expires_at)
  VALUES (p_dep, 'CUSTOMER', p_holder, 'acc_psp', 'ref_' || p_dep, p_satang, pg_temp.base(p_satang),
          now() + interval '1 day');
  UPDATE thb_deposit SET status = 'CONFIRMED', webhook_id = p_wh, confirmed_at = now() WHERE id = p_dep;
END $$;

-- mint ครบวงจร · operation → chain_event → thb_mint → ledger + ยอด → MINTED
CREATE FUNCTION pg_temp.do_mint(p_dep text) RETURNS void LANGUAGE plpgsql AS $$
DECLARE v_op text := 'acc_opm_' || p_dep; v_amt text; v_hid text; v_key text;
BEGIN
  SELECT amount_base, holder_id INTO v_amt, v_hid FROM thb_deposit WHERE id = p_dep;
  v_key := 'CUSTOMER_THB:' || v_hid;
  INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
  VALUES (v_op, 'system:minter', 'THB_MINT', p_dep, '{}');
  INSERT INTO outbox (id, operation_id, topic, payload) VALUES (v_op || '_o', v_op, 'thb.mint.v1', '{}');
  INSERT INTO chain_event (id, block_number, block_hash, tx_hash, log_index, address, topic0, topics, data,
                           block_timestamp, operation_id)
  VALUES ('acc_ce_' || p_dep, 900000006, '0x' || repeat('bc', 32), pg_temp.h(p_dep), 0,
          '0x' || repeat('dc', 20), '0x' || repeat('02', 32), '[]', '\x00', now(), v_op);
  INSERT INTO thb_mint (deposit_id, operation_id, amount_base, to_account, to_account_id,
                        chain_event_id, tx_hash, minted_block, applied_at)
  VALUES (p_dep, v_op, v_amt, 'CUSTOMER_THB', v_key, 'acc_ce_' || p_dep, pg_temp.h(p_dep), 900000006, now());
  INSERT INTO ledger_entry (id, operation_id, entry_seq, account, account_id, amount, memo) VALUES
    (v_op || '_a', v_op, 1, 'CUSTOMER_THB', v_key, v_amt, 'THB เข้าจากการฝาก'),
    (v_op || '_b', v_op, 2, 'ISSUANCE', 'ISSUANCE:acc_thb', '-' || v_amt, 'คู่บัญชีของการ mint');
  INSERT INTO thb_balance (account, account_id, amount) VALUES ('CUSTOMER_THB', v_key, v_amt)
  ON CONFLICT (account, account_id) DO UPDATE SET amount = (thb_balance.amount::numeric + v_amt::numeric)::text;
  INSERT INTO thb_balance (account, account_id, amount) VALUES ('ISSUANCE', 'ISSUANCE:acc_thb', '-' || v_amt)
  ON CONFLICT (account, account_id) DO UPDATE SET amount = (thb_balance.amount::numeric - v_amt::numeric)::text;
  UPDATE operation SET status = 'SUBMITTED' WHERE id = v_op AND status = 'PENDING';
  UPDATE operation SET status = 'CONFIRMED' WHERE id = v_op AND status = 'SUBMITTED';
  UPDATE operation SET status = 'FINALIZED', finalized_at = now() WHERE id = v_op;
  UPDATE thb_deposit SET status = 'MINTED', minted_at = now() WHERE id = p_dep;
END $$;

-- ------------------------------------------------------------ fixture
INSERT INTO signer (address, key_ref) VALUES ('0x' || repeat('3f', 20), 'kms/acc-minter-1');
INSERT INTO raw_block (block_hash, block_number, parent_hash, "timestamp")
VALUES ('0x' || repeat('bc', 32), 900000006, '0x' || repeat('b6', 32), now());
-- FEAT-14 · บล็อกที่อ่านแล้วต้องอยู่ใต้ cursor ตอน commit (0017)
INSERT INTO indexer_cursor (id, last_block, last_block_hash)
VALUES ('main', 900000006, '0x' || repeat('bc', 32));

SELECT pg_temp.webhook('acc_wh_' || g, 'evt_' || g, 'charge.complete') FROM generate_series(1, 9) g;
SELECT pg_temp.webhook('acc_wh_r1', 'evt_r1', 'charge.reverse');
SELECT pg_temp.webhook('acc_wh_r2', 'evt_r2', 'charge.reverse');
SELECT pg_temp.webhook('acc_wh_r3', 'evt_r3', 'charge.reverse');
SELECT pg_temp.webhook('acc_wh_bad', 'evt_bad', 'charge.complete', false);

SELECT pg_temp.confirmed('acc_dep_1', 'acc_cus_1',     pg_temp.satang(100), 'acc_wh_1');
SELECT pg_temp.confirmed('acc_dep_2', 'acc_cus_2',     pg_temp.satang(200), 'acc_wh_2');
SELECT pg_temp.confirmed('acc_dep_3', 'acc_cus_3',     pg_temp.satang(300), 'acc_wh_3');
SELECT pg_temp.confirmed('acc_dep_4', 'acc_cus_4',     pg_temp.satang(400), 'acc_wh_4');
SELECT pg_temp.confirmed('acc_dep_5', 'acc_cus_spent', pg_temp.satang(500), 'acc_wh_5');
SELECT pg_temp.confirmed('acc_dep_6', 'acc_cus_2',     pg_temp.satang(600), 'acc_wh_6');
SELECT pg_temp.confirmed('acc_dep_7', 'acc_cus_3',     pg_temp.satang(700), 'acc_wh_7');

-- =====================================================================
-- T1 · webhook เดิมที่ผู้ให้บริการส่งซ้ำ → ไม่ประมวลผลสองรอบ
-- T2 · payload ดิบแก้ไม่ได้ · T3 · ทำเครื่องหมายว่าประมวลผลแล้วได้
-- =====================================================================
DO $$
DECLARE n int;
BEGIN
  INSERT INTO psp_webhook (id, provider, provider_event_id, event_type, raw_body, signature, signature_ok)
  VALUES ('acc_wh_1_retry', 'acc_psp', 'evt_1', 'charge.complete', '\x00', 'sig', true)
  ON CONFLICT (provider, provider_event_id) DO NOTHING;
  GET DIAGNOSTICS n = ROW_COUNT;
  PERFORM pg_temp.rec('T1', 'webhook ที่ผู้ให้บริการ retry (event id เดิม) → ได้ 0 rows',
                      CASE WHEN n = 0 THEN 'PASS' ELSE 'FAIL' END, format('ได้ %s', n));
END $$;

SELECT pg_temp.rec_checks('T2', 'แก้ raw_body / signature / signature_ok ของ webhook → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE psp_webhook SET raw_body = '\x01' WHERE id = 'acc_wh_1'$q$,
    '23514', 'psp_webhook', NULL, ARRAY['trg_psp_webhook_immutable']),
  pg_temp.try_reject($q$UPDATE psp_webhook SET signature_ok = true WHERE id = 'acc_wh_bad'$q$,
    '23514', 'psp_webhook', NULL, ARRAY['trg_psp_webhook_immutable']));
SELECT pg_temp.rec_checks('T3', 'ทำเครื่องหมาย processed_at ได้',
  pg_temp.try_ok($q$UPDATE psp_webhook SET processed_at = now() WHERE id = 'acc_wh_1'$q$));

-- =====================================================================
-- T4 · 100.50 บาท = 10050 สตางค์ = 100.5e18 base units
-- T5 · base units ไม่ตรงกับสตางค์ (ผิดหลักสิบยกกำลัง) → ถูกปฏิเสธ
-- T6 · provider_ref เดิม → ฝากซ้ำไม่ได้
-- =====================================================================
DO $$
DECLARE r text; v text;
BEGIN
  r := pg_temp.try_ok(format($q$INSERT INTO thb_deposit (id, holder_kind, holder_id, provider, provider_ref,
                                                         amount_satang, amount_base, expires_at)
                                VALUES ('acc_dep_x', 'CUSTOMER', 'acc_cus_1', 'acc_psp', 'ref_x', %s, %L,
                                        now() + interval '1 day')$q$,
                             pg_temp.satang(100.50), pg_temp.base(pg_temp.satang(100.50))));
  SELECT amount_base INTO v FROM thb_deposit WHERE id = 'acc_dep_x';
  PERFORM pg_temp.rec_checks('T4', '100.50 บาท = 10050 สตางค์ = 100.5e18 base units', r,
    CASE WHEN v = '100500000000000000000' THEN NULL ELSE 'FAIL: ได้ ' || coalesce(v, 'NULL') END);
END $$;

SELECT pg_temp.rec_checks('T5', 'base ผิดหลักสิบยกกำลัง / ไม่ใช่ตัวเลข → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO thb_deposit (id, holder_kind, holder_id, provider, provider_ref,
                                                 amount_satang, amount_base, expires_at)
                        VALUES ('acc_dep_bad', 'CUSTOMER', 'acc_cus_1', 'acc_psp', 'ref_bad', 10000,
                                '100000000000000000000000', now() + interval '1 day')$q$,
    '23514', 'thb_deposit', NULL, ARRAY['trg_deposit_amount_exact']),
  pg_temp.try_reject($q$INSERT INTO thb_deposit (id, holder_kind, holder_id, provider, provider_ref,
                                                 amount_satang, amount_base, expires_at)
                        VALUES ('acc_dep_bad2', 'CUSTOMER', 'acc_cus_1', 'acc_psp', 'ref_bad2', 10000,
                                '1e20', now() + interval '1 day')$q$,
    '23514', 'thb_deposit', NULL, ARRAY['ck_thb_deposit_base_uint256']));

SELECT pg_temp.rec_checks('T6', 'provider_ref ซ้ำ → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO thb_deposit (id, holder_kind, holder_id, provider, provider_ref,
                                                 amount_satang, amount_base, expires_at)
                        VALUES ('acc_dep_dup', 'CUSTOMER', 'acc_cus_1', 'acc_psp', 'ref_x', 10000,
                                '100000000000000000000', now() + interval '1 day')$q$,
    '23505', 'thb_deposit', ARRAY['provider', 'provider_ref']));

-- =====================================================================
-- T7  · ยืนยันการฝากโดยไม่มี webhook → ถูกปฏิเสธ
-- T7b · ยืนยันด้วย webhook ที่ลายเซ็นไม่ผ่าน หรือไม่ใช่ charge.complete → ถูกปฏิเสธ
-- T7c · webhook ตัวเดียวยืนยันสองการฝากไม่ได้
-- =====================================================================
SELECT pg_temp.rec_checks('T7', 'ยืนยันการฝากโดยไม่มี webhook → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE thb_deposit SET status = 'CONFIRMED', confirmed_at = now() WHERE id = 'acc_dep_x'$q$,
    '23514', 'thb_deposit', NULL, ARRAY['ck_dep_needs_webhook']));
SELECT pg_temp.rec_checks('T7b', 'ยืนยันด้วย webhook ลายเซ็นไม่ผ่าน / webhook ตีกลับ → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE thb_deposit SET status = 'CONFIRMED', confirmed_at = now(), webhook_id = 'acc_wh_bad'
                        WHERE id = 'acc_dep_x'$q$,
    '23514', 'thb_deposit', NULL, ARRAY['trg_deposit_evidence']),
  pg_temp.try_reject($q$UPDATE thb_deposit SET status = 'CONFIRMED', confirmed_at = now(), webhook_id = 'acc_wh_r3'
                        WHERE id = 'acc_dep_x'$q$,
    '23514', 'thb_deposit', NULL, ARRAY['trg_deposit_evidence']));
SELECT pg_temp.rec_checks('T7c', 'webhook ที่ยืนยันการฝากอื่นไปแล้ว ใช้ยืนยันอีกรายการ → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE thb_deposit SET status = 'CONFIRMED', confirmed_at = now(), webhook_id = 'acc_wh_2'
                        WHERE id = 'acc_dep_x'$q$,
    '23505', 'thb_deposit', ARRAY['webhook_id']));

-- =====================================================================
-- T8  · F4 · mint จากการฝากที่ยัง PENDING_PAYMENT → ถูกปฏิเสธ
-- T9  · F4 · mint ยอดมากกว่าที่ฝาก → ถูกปฏิเสธ
-- T10 · mint ไปบัญชีที่ไม่ใช่เจ้าของการฝาก → ถูกปฏิเสธ
-- T10b · thb_mint ต้องอ้าง THB_MINT operation ของการฝากนั้นเอง
-- =====================================================================
INSERT INTO operation (id, actor_scope, kind, subject_id, payload) VALUES
  ('acc_opm_x',  'system:minter', 'THB_MINT', 'acc_dep_x', '{}'),
  ('acc_opm_2',  'system:minter', 'THB_MINT', 'acc_dep_2', '{}'),
  ('acc_opx_2',  'system:minter', 'POINT_TRANSFER', 'acc_dep_2', '{}');
INSERT INTO outbox (id, operation_id, topic, payload) VALUES
  ('acc_obm_x', 'acc_opm_x', 'thb.mint.v1', '{}'), ('acc_obm_2', 'acc_opm_2', 'thb.mint.v1', '{}'),
  ('acc_obx_2', 'acc_opx_2', 'point.transfer.v1', '{}');

CREATE FUNCTION pg_temp.mint_row(p_dep text, p_op text, p_amount text, p_account_id text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT format($q$INSERT INTO thb_mint (deposit_id, operation_id, amount_base, to_account, to_account_id)
                   VALUES (%L, %L, %L, 'CUSTOMER_THB', %L)$q$, p_dep, p_op, p_amount, p_account_id)
$$;

SELECT pg_temp.rec_checks('T8', 'F4 · mint จากการฝากที่ยัง PENDING_PAYMENT → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.mint_row('acc_dep_x', 'acc_opm_x', '100500000000000000000', 'CUSTOMER_THB:acc_cus_1'),
    '23514', 'thb_mint', NULL, ARRAY['trg_mint_requires_confirmed_deposit']));
SELECT pg_temp.rec_checks('T9', 'F4 · mint 2000 บาทจากการฝาก 200 บาท → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.mint_row('acc_dep_2', 'acc_opm_2', pg_temp.base(pg_temp.satang(2000)), 'CUSTOMER_THB:acc_cus_2'),
    '23514', 'thb_mint', NULL, ARRAY['trg_mint_requires_confirmed_deposit']));
SELECT pg_temp.rec_checks('T10', 'mint ไปบัญชีของคนอื่น → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.mint_row('acc_dep_2', 'acc_opm_2', pg_temp.base(pg_temp.satang(200)), 'CUSTOMER_THB:acc_cus_1'),
    '23514', 'thb_mint', NULL, ARRAY['trg_mint_requires_confirmed_deposit']));
SELECT pg_temp.rec_checks('T10b', 'thb_mint อ้าง operation ที่ไม่ใช่ THB_MINT ของการฝากนี้ → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.mint_row('acc_dep_2', 'acc_opx_2', pg_temp.base(pg_temp.satang(200)), 'CUSTOMER_THB:acc_cus_2'),
    '23514', 'thb_mint', NULL, ARRAY['trg_mint_requires_confirmed_deposit']),
  pg_temp.try_reject(pg_temp.mint_row('acc_dep_2', 'acc_opm_x', pg_temp.base(pg_temp.satang(200)), 'CUSTOMER_THB:acc_cus_2'),
    '23514', 'thb_mint', NULL, ARRAY['trg_mint_requires_confirmed_deposit']));

-- =====================================================================
-- T11 · mint ที่ถูกต้องครบวงจร · ยอดเข้า 100 บาท · ISSUANCE ติดลบเท่ากัน · ตรวจตอน commit ผ่าน
-- =====================================================================
DO $$
DECLARE r text[]; v_bal text; v_iss text; v_st text;
BEGIN
  r[1] := pg_temp.try_ok('SELECT pg_temp.do_mint(''acc_dep_1'')');
  r[2] := pg_temp.try_ok('SET CONSTRAINTS ALL IMMEDIATE');
  PERFORM pg_temp.try_ok('SET CONSTRAINTS ALL DEFERRED');
  SELECT amount INTO v_bal FROM thb_balance WHERE account_id = 'CUSTOMER_THB:acc_cus_1';
  SELECT amount INTO v_iss FROM thb_balance WHERE account_id = 'ISSUANCE:acc_thb';
  SELECT status INTO v_st FROM thb_deposit WHERE id = 'acc_dep_1';
  IF v_bal IS DISTINCT FROM '100000000000000000000' OR v_iss IS DISTINCT FROM '-100000000000000000000'
     OR v_st IS DISTINCT FROM 'MINTED' THEN
    r[3] := format('FAIL: ยอด %s · ISSUANCE %s · สถานะ %s', v_bal, v_iss, v_st);
  END IF;
  PERFORM pg_temp.rec_checks('T11', 'mint ถูกต้อง · ยอดเข้า 100 บาท · ISSUANCE ติดลบเท่ากัน · commit ผ่าน', VARIADIC r);
END $$;

-- =====================================================================
-- T12  · mint การฝากที่ MINTED แล้ว → ถูกปฏิเสธด้วยสถานะ
-- T12b · ยัดสองแถว mint ใน statement เดียวก่อนอัปเดตสถานะ → primary key กันไว้
-- =====================================================================
INSERT INTO operation (id, actor_scope, kind, subject_id, payload) VALUES ('acc_opm_1b', 'system:minter', 'THB_MINT', 'acc_dep_1', '{}');
INSERT INTO outbox (id, operation_id, topic, payload) VALUES ('acc_obm_1b', 'acc_opm_1b', 'thb.mint.v1', '{}');
SELECT pg_temp.rec_checks('T12', 'mint การฝากที่ MINTED แล้ว → ถูกปฏิเสธด้วยสถานะ',
  pg_temp.try_reject(pg_temp.mint_row('acc_dep_1', 'acc_opm_1b', pg_temp.base(pg_temp.satang(100)), 'CUSTOMER_THB:acc_cus_1'),
    '23514', 'thb_mint', NULL, ARRAY['trg_mint_requires_confirmed_deposit']));
SELECT pg_temp.rec_checks('T12b', 'สองแถว mint ของการฝากเดียวใน statement เดียว → ชน primary key',
  pg_temp.try_reject(format($q$INSERT INTO thb_mint (deposit_id, operation_id, amount_base, to_account, to_account_id) VALUES
                                ('acc_dep_2', 'acc_opm_2', %1$L, 'CUSTOMER_THB', 'CUSTOMER_THB:acc_cus_2'),
                                ('acc_dep_2', 'acc_opm_2', %1$L, 'CUSTOMER_THB', 'CUSTOMER_THB:acc_cus_2')$q$,
                            pg_temp.base(pg_temp.satang(200))),
    '23505', 'thb_mint', ARRAY['deposit_id']));

-- =====================================================================
-- T13 · MINTED → CONFIRMED ไม่ได้ · T14 · PENDING_PAYMENT → MINTED ตรงๆ ไม่ได้
-- T15 · ยืนยันแล้วแก้ยอดไม่ได้ (แม้สถานะไม่เปลี่ยน)
-- T15b · MINTED โดยไม่มีแถว thb_mint → ถูกปฏิเสธ
-- =====================================================================
SELECT pg_temp.rec_checks('T13', 'MINTED กลับไป CONFIRMED → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE thb_deposit SET status = 'CONFIRMED', minted_at = NULL WHERE id = 'acc_dep_1'$q$,
    '23514', 'thb_deposit', NULL, ARRAY['trg_deposit_status_transition']));
SELECT pg_temp.rec_checks('T14', 'PENDING_PAYMENT ข้ามไป MINTED / สร้างการฝากที่ CONFIRMED ทันที → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE thb_deposit SET status = 'MINTED', webhook_id = 'acc_wh_8', confirmed_at = now(),
                                               minted_at = now() WHERE id = 'acc_dep_x'$q$,
    '23514', 'thb_deposit', NULL, ARRAY['trg_deposit_status_transition']),
  pg_temp.try_reject($q$INSERT INTO thb_deposit (id, holder_kind, holder_id, provider, provider_ref, amount_satang,
                                                 amount_base, status, webhook_id, confirmed_at, expires_at)
                        VALUES ('acc_dep_born', 'CUSTOMER', 'acc_cus_1', 'acc_psp', 'ref_born', 100,
                                '1000000000000000000', 'CONFIRMED', 'acc_wh_9', now(), now() + interval '1 day')$q$,
    '23514', 'thb_deposit', NULL, ARRAY['trg_deposit_status_transition']));
SELECT pg_temp.rec_checks('T15', 'ยืนยัน 300 บาทแล้วแก้เป็น 3000 → ถูกปฏิเสธ',
  pg_temp.try_reject(format($q$UPDATE thb_deposit SET amount_satang = %s, amount_base = %L WHERE id = 'acc_dep_3'$q$,
                            pg_temp.satang(3000), pg_temp.base(pg_temp.satang(3000))),
    '23514', 'thb_deposit', NULL, ARRAY['trg_deposit_status_transition']));
SELECT pg_temp.rec_checks('T15b', 'ตั้ง MINTED ทั้งที่ไม่มีแถว thb_mint → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE thb_deposit SET status = 'MINTED', minted_at = now() WHERE id = 'acc_dep_3'$q$,
    '23514', 'thb_deposit', NULL, ARRAY['trg_deposit_evidence']));

-- =====================================================================
-- T16  · F4 · operation ที่ไม่ใช่ THB_MINT แตะ ISSUANCE → ถูกปฏิเสธทันที
-- T16b · F4 · THB_MINT ที่ไม่มีการฝากรองรับ ขยับ ISSUANCE → ไม่ผ่านตอน commit
-- T16c · mint ที่มี thb_mint แต่ ledger เข้าบัญชีคนอื่น → ไม่ผ่านตอน commit
-- =====================================================================
SELECT pg_temp.try_ok('SET CONSTRAINTS ALL IMMEDIATE');
SELECT pg_temp.try_ok('SET CONSTRAINTS ALL DEFERRED');

SELECT pg_temp.rec_checks('T16', 'operation ชนิดอื่นเขียน ledger ที่แตะ ISSUANCE → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO ledger_entry (id, operation_id, entry_seq, account, account_id, amount)
                        VALUES ('acc_le_16', 'acc_opx_2', 1, 'ISSUANCE', 'ISSUANCE:acc_thb', '-5')$q$,
    '23514', 'ledger_entry', NULL, ARRAY['trg_issuance_only_from_mint']));

SELECT pg_temp.rec_checks('T16b', 'THB_MINT ที่ไม่มี thb_mint หรือการตีกลับรองรับ ขยับ ISSUANCE → ไม่ผ่าน',
  pg_temp.try_reject($q$
    INSERT INTO operation (id, actor_scope, kind, payload)
    VALUES ('acc_opm_ghost', 'admin:acc_adm', 'THB_MINT', '{}');
    UPDATE operation SET status = 'SUBMITTED' WHERE id = 'acc_opm_ghost';
    UPDATE operation SET status = 'CONFIRMED' WHERE id = 'acc_opm_ghost';
    UPDATE operation SET status = 'FINALIZED', finalized_at = now() WHERE id = 'acc_opm_ghost';
    INSERT INTO ledger_entry (id, operation_id, entry_seq, account, account_id, amount) VALUES
      ('acc_le_g1', 'acc_opm_ghost', 1, 'CUSTOMER_THB', 'CUSTOMER_THB:acc_cus_9', '777'),
      ('acc_le_g2', 'acc_opm_ghost', 2, 'ISSUANCE', 'ISSUANCE:acc_thb', '-777');
    SET CONSTRAINTS trg_issuance_backed IMMEDIATE;
  $q$, '23514', 'ledger_entry', NULL, ARRAY['trg_issuance_backed']));
SELECT pg_temp.try_ok('SET CONSTRAINTS trg_issuance_backed DEFERRED');

SELECT pg_temp.rec_checks('T16c', 'mint ของการฝาก 200 บาท แต่ ledger ให้เงินคนอื่น → ไม่ผ่าน',
  pg_temp.try_reject(format($q$
    %s;
    INSERT INTO ledger_entry (id, operation_id, entry_seq, account, account_id, amount) VALUES
      ('acc_le_c1', 'acc_opm_2', 1, 'CUSTOMER_THB', 'CUSTOMER_THB:acc_cus_9', %2$L),
      ('acc_le_c2', 'acc_opm_2', 2, 'ISSUANCE', 'ISSUANCE:acc_thb', '-' || %2$L);
    SET CONSTRAINTS trg_issuance_backed IMMEDIATE;
  $q$, pg_temp.mint_row('acc_dep_2', 'acc_opm_2', pg_temp.base(pg_temp.satang(200)), 'CUSTOMER_THB:acc_cus_2'),
       pg_temp.base(pg_temp.satang(200))),
    '23514', 'ledger_entry', NULL, ARRAY['trg_issuance_backed']));
SELECT pg_temp.try_ok('SET CONSTRAINTS trg_issuance_backed DEFERRED');

-- =====================================================================
-- T17 · F4 · ลบ ISSUANCE = ผลรวมบัญชีอื่นทั้งหมด (ทั้งฐาน)
-- T18 · ledger ของ mint ที่รวมไม่ได้ศูนย์ → ไม่ผ่านตอน commit
-- =====================================================================
DO $$
DECLARE v_iss numeric; v_rest numeric;
BEGIN
  SELECT coalesce(sum(amount::numeric), 0) INTO v_iss  FROM thb_balance WHERE account = 'ISSUANCE';
  SELECT coalesce(sum(amount::numeric), 0) INTO v_rest FROM thb_balance WHERE account <> 'ISSUANCE';
  PERFORM pg_temp.rec('T17', 'F4 · ลบ ISSUANCE = ผลรวมบัญชีอื่นทั้งหมด',
                      CASE WHEN -v_iss = v_rest THEN 'PASS' ELSE 'FAIL' END, format('%s · %s', -v_iss, v_rest));
END $$;

SELECT pg_temp.rec_checks('T18', 'ledger ของ mint รวมไม่ได้ศูนย์ → ไม่ผ่าน',
  pg_temp.try_reject($q$
    INSERT INTO ledger_entry (id, operation_id, entry_seq, account, account_id, amount)
    VALUES ('acc_le_18', 'acc_opm_acc_dep_1', 3, 'CUSTOMER_THB', 'CUSTOMER_THB:acc_cus_1', '1');
    SET CONSTRAINTS trg_ledger_balanced IMMEDIATE;
  $q$, '23514', 'ledger_entry', NULL, ARRAY['trg_ledger_balanced']));
SELECT pg_temp.try_ok('SET CONSTRAINTS trg_ledger_balanced DEFERRED');

-- =====================================================================
-- T19 · mint = หนึ่ง step (THB.mint) · T20 · R2 · SUBMITTED โดยไม่มี raw_tx → ถูกปฏิเสธ
-- =====================================================================
DO $$
DECLARE r text; v text[];
BEGIN
  r := pg_temp.try_ok($q$INSERT INTO operation_step (id, operation_id, step_index, name)
                          VALUES ('acc_st_1', 'acc_opm_acc_dep_1', 1, 'mint')$q$);
  SELECT array_agg(name ORDER BY step_index) INTO v FROM operation_step WHERE operation_id = 'acc_opm_acc_dep_1';
  PERFORM pg_temp.rec_checks('T19', 'mint = 1 step', r,
    CASE WHEN v = ARRAY['mint'] THEN NULL ELSE 'FAIL: ได้ ' || coalesce(array_to_string(v, ','), '-') END);
END $$;
SELECT pg_temp.rec_checks('T20', 'R2 · SUBMITTED โดยไม่มี raw_tx → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO operation_step (id, operation_id, step_index, name, status)
                        VALUES ('acc_st_nr', 'acc_opm_2', 1, 'mint', 'SUBMITTED')$q$,
    '23514', 'operation_step', NULL, ARRAY['ck_rawtx_before_broadcast']));

-- =====================================================================
-- T21 · ตีกลับขณะยอดยังพอ → เผาคืน · ledger สมดุล · ยอดเหลือศูนย์ · การฝาก REVERSED
-- =====================================================================
SELECT pg_temp.do_mint('acc_dep_4');
INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
VALUES ('acc_opr_4', 'system:psp', 'THB_MINT', 'acc_dep_4', '{}');
UPDATE operation SET status = 'SUBMITTED' WHERE id = 'acc_opr_4';
UPDATE operation SET status = 'CONFIRMED' WHERE id = 'acc_opr_4';
UPDATE operation SET status = 'FINALIZED', finalized_at = now() WHERE id = 'acc_opr_4';
INSERT INTO outbox (id, operation_id, topic, payload) VALUES ('acc_obr_4', 'acc_opr_4', 'thb.burn.v1', '{}');

DO $$
DECLARE r text[]; v_bal text; v_out text; v_st text;
BEGIN
  r[1] := pg_temp.try_ok(format($q$
    INSERT INTO deposit_reversal (id, deposit_id, webhook_id, operation_id, amount_base, available_at_reversal, outcome)
    VALUES ('acc_rev_4', 'acc_dep_4', 'acc_wh_r1', 'acc_opr_4', %1$L, %1$L, 'BURNED');
    INSERT INTO ledger_entry (id, operation_id, entry_seq, account, account_id, amount, memo) VALUES
      ('acc_ler_4a', 'acc_opr_4', 1, 'CUSTOMER_THB', 'CUSTOMER_THB:acc_cus_4', '-' || %1$L, 'เผาคืนจากการตีกลับ'),
      ('acc_ler_4b', 'acc_opr_4', 2, 'ISSUANCE', 'ISSUANCE:acc_thb', %1$L, 'คู่บัญชีของการเผา');
    UPDATE thb_balance SET amount = '0' WHERE account_id = 'CUSTOMER_THB:acc_cus_4';
    UPDATE thb_balance SET amount = (amount::numeric + %1$s)::text WHERE account_id = 'ISSUANCE:acc_thb';
    UPDATE thb_deposit SET status = 'REVERSED' WHERE id = 'acc_dep_4';
  $q$, pg_temp.base(pg_temp.satang(400))));
  r[2] := pg_temp.try_ok('SET CONSTRAINTS ALL IMMEDIATE');
  PERFORM pg_temp.try_ok('SET CONSTRAINTS ALL DEFERRED');
  SELECT amount INTO v_bal FROM thb_balance WHERE account_id = 'CUSTOMER_THB:acc_cus_4';
  SELECT outcome INTO v_out FROM deposit_reversal WHERE id = 'acc_rev_4';
  SELECT status INTO v_st FROM thb_deposit WHERE id = 'acc_dep_4';
  IF v_bal IS DISTINCT FROM '0' OR v_out IS DISTINCT FROM 'BURNED' OR v_st IS DISTINCT FROM 'REVERSED' THEN
    r[3] := format('FAIL: ยอด %s · %s · %s', v_bal, v_out, v_st);
  END IF;
  PERFORM pg_temp.rec_checks('T21', 'ตีกลับขณะยอดพอ → BURNED · ยอดเหลือศูนย์ · การฝาก REVERSED · commit ผ่าน', VARIADIC r);
END $$;

-- =====================================================================
-- T22  · ยอดไม่พอ (ถูกกันไว้ทั้งก้อน) แต่บอกว่า BURNED → ถูกปฏิเสธ
-- T22b · บอกยอดที่ใช้ได้ปลอม (อ้าง 500 ทั้งที่มี 0) เพื่อเลือก BURNED → ถูกปฏิเสธ
-- T23  · ยอดไม่พอ → MANUAL_REVIEW ได้ · ไม่แตะยอดเลย
-- T23b · การฝากที่ส่งให้คนตรวจ ตั้งเป็น REVERSED ไม่ได้ (ยังไม่มีการเผา)
-- T24  · ยอดพอแต่บอกว่า MANUAL_REVIEW → ถูกปฏิเสธ
-- =====================================================================
SELECT pg_temp.do_mint('acc_dep_5');
-- เงินถูกกันไว้ให้งานอื่นทั้งก้อน · ยอดที่ใช้ได้เหลือศูนย์
INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
VALUES ('acc_op_spend', 'customer:acc_cus_spent', 'POINT_TRANSFER', 'acc_spend', '{}');
INSERT INTO outbox (id, operation_id, topic, payload) VALUES ('acc_ob_spend', 'acc_op_spend', 'x.v1', '{}');
INSERT INTO reservation (id, operation_id, subject_type, subject_id, amount)
VALUES ('acc_rsv_spend', 'acc_op_spend', 'THB_BALANCE', 'CUSTOMER_THB:acc_cus_spent', pg_temp.base(pg_temp.satang(500)));
INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
VALUES ('acc_opr_5', 'system:psp', 'THB_MINT', 'acc_dep_5', '{}');
UPDATE operation SET status = 'SUBMITTED' WHERE id = 'acc_opr_5';
UPDATE operation SET status = 'CONFIRMED' WHERE id = 'acc_opr_5';
UPDATE operation SET status = 'FINALIZED', finalized_at = now() WHERE id = 'acc_opr_5';
INSERT INTO outbox (id, operation_id, topic, payload) VALUES ('acc_obr_5', 'acc_opr_5', 'thb.burn.v1', '{}');

CREATE FUNCTION pg_temp.reversal(p_id text, p_dep text, p_wh text, p_op text, p_amount text,
                                 p_available text, p_outcome text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT format($q$INSERT INTO deposit_reversal (id, deposit_id, webhook_id, operation_id, amount_base,
                                                 available_at_reversal, outcome)
                   VALUES (%L, %L, %L, %L, %L, %L, %L)$q$, p_id, p_dep, p_wh, p_op, p_amount, p_available, p_outcome)
$$;

SELECT pg_temp.rec_checks('T22', 'ยอดที่ใช้ได้ 0 แต่อ้างว่า BURNED → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.reversal('acc_rev_5', 'acc_dep_5', 'acc_wh_r2', 'acc_opr_5',
                     pg_temp.base(pg_temp.satang(500)), '0', 'BURNED'),
    '23514', 'deposit_reversal', NULL, ARRAY['trg_reversal_outcome_matches_balance']));
SELECT pg_temp.rec_checks('T22b', 'อ้างยอดที่ใช้ได้ 500 (จริงคือ 0) เพื่อเลือก BURNED → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.reversal('acc_rev_5', 'acc_dep_5', 'acc_wh_r2', 'acc_opr_5',
                     pg_temp.base(pg_temp.satang(500)), pg_temp.base(pg_temp.satang(500)), 'BURNED'),
    '23514', 'deposit_reversal', NULL, ARRAY['trg_reversal_outcome_matches_balance']));

DO $$
DECLARE r text; v_bal text; v_out text;
BEGIN
  r := pg_temp.try_ok(pg_temp.reversal('acc_rev_5b', 'acc_dep_5', 'acc_wh_r2', 'acc_opr_5',
                      pg_temp.base(pg_temp.satang(500)), '0', 'MANUAL_REVIEW'));
  SELECT amount INTO v_bal FROM thb_balance WHERE account_id = 'CUSTOMER_THB:acc_cus_spent';
  SELECT outcome INTO v_out FROM deposit_reversal WHERE id = 'acc_rev_5b';
  PERFORM pg_temp.rec_checks('T23', 'ยอดไม่พอ → MANUAL_REVIEW · ยอดไม่ถูกแตะ', r,
    CASE WHEN v_out = 'MANUAL_REVIEW' AND v_bal = pg_temp.base(pg_temp.satang(500)) THEN NULL
         ELSE format('FAIL: %s · ยอด %s', v_out, v_bal) END);
END $$;

SELECT pg_temp.rec_checks('T23b', 'การฝากที่ MANUAL_REVIEW ตั้งเป็น REVERSED → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE thb_deposit SET status = 'REVERSED' WHERE id = 'acc_dep_5'$q$,
    '23514', 'thb_deposit', NULL, ARRAY['trg_deposit_evidence']));

SELECT pg_temp.do_mint('acc_dep_6');
INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
VALUES ('acc_opr_6', 'system:psp', 'THB_MINT', 'acc_dep_6', '{}');
UPDATE operation SET status = 'SUBMITTED' WHERE id = 'acc_opr_6';
UPDATE operation SET status = 'CONFIRMED' WHERE id = 'acc_opr_6';
UPDATE operation SET status = 'FINALIZED', finalized_at = now() WHERE id = 'acc_opr_6';
INSERT INTO outbox (id, operation_id, topic, payload) VALUES ('acc_obr_6', 'acc_opr_6', 'thb.burn.v1', '{}');
SELECT pg_temp.rec_checks('T24', 'ยอดพอ (600) แต่อ้าง MANUAL_REVIEW → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.reversal('acc_rev_6', 'acc_dep_6', 'acc_wh_r3', 'acc_opr_6',
                     pg_temp.base(pg_temp.satang(600)), pg_temp.base(pg_temp.satang(600)), 'MANUAL_REVIEW'),
    '23514', 'deposit_reversal', NULL, ARRAY['trg_reversal_outcome_matches_balance']));

-- =====================================================================
-- T25 · ตีกลับการฝากที่ยังไม่ได้ mint → ถูกปฏิเสธ
-- T26 · การฝากเดียวตีกลับซ้ำไม่ได้
-- T27 · การฝากหนึ่งรายการมีงาน mint ค้างได้ทีละงาน
-- =====================================================================
INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
VALUES ('acc_opr_3', 'system:psp', 'THB_MINT', 'acc_dep_3', '{}');
UPDATE operation SET status = 'SUBMITTED' WHERE id = 'acc_opr_3';
UPDATE operation SET status = 'CONFIRMED' WHERE id = 'acc_opr_3';
UPDATE operation SET status = 'FINALIZED', finalized_at = now() WHERE id = 'acc_opr_3';
INSERT INTO outbox (id, operation_id, topic, payload) VALUES ('acc_obr_3', 'acc_opr_3', 'thb.burn.v1', '{}');
SELECT pg_temp.rec_checks('T25', 'ตีกลับการฝากที่ยังไม่ mint → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.reversal('acc_rev_3', 'acc_dep_3', 'acc_wh_r3', 'acc_opr_3',
                     pg_temp.base(pg_temp.satang(300)), '0', 'MANUAL_REVIEW'),
    '23514', 'deposit_reversal', NULL, ARRAY['trg_reversal_outcome_matches_balance']));
SELECT pg_temp.rec_checks('T26', 'ตีกลับการฝากเดิมซ้ำ → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.reversal('acc_rev_5c', 'acc_dep_5', 'acc_wh_r3', 'acc_opr_5',
                     pg_temp.base(pg_temp.satang(500)), '0', 'MANUAL_REVIEW'),
    '23505', 'deposit_reversal', ARRAY['deposit_id']));

INSERT INTO operation (id, actor_scope, kind, subject_id, payload) VALUES ('acc_opm_7a', 'system:minter', 'THB_MINT', 'acc_dep_7', '{}');
INSERT INTO outbox (id, operation_id, topic, payload) VALUES ('acc_obm_7a', 'acc_opm_7a', 'thb.mint.v1', '{}');
SELECT pg_temp.rec_checks('T27', 'สองงาน mint ค้างบนการฝากเดียว → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
                        VALUES ('acc_opm_7b', 'system:minter', 'THB_MINT', 'acc_dep_7', '{}')$q$,
    '23505', 'operation', ARRAY['subject_id'], ARRAY['uq_mint_inflight']));

-- =====================================================================
-- T28 · ยอดติดลบได้เฉพาะ ISSUANCE · T29 · ledger amount = 0 → ถูกปฏิเสธ
-- T30 · outbox topic ต้องมีเวอร์ชัน · T31 · อ่าน log ซ้ำได้
-- T32 · webhook ที่ลายเซ็นไม่ผ่าน ไม่อยู่ในคิวงาน
-- =====================================================================
SELECT pg_temp.rec_checks('T28', 'ยอดลูกค้าติดลบ → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO thb_balance (account, account_id, amount) VALUES ('CUSTOMER_THB', 'CUSTOMER_THB:acc_neg', '-1')$q$,
    '23514', 'thb_balance', NULL, ARRAY['ck_thb_balance_sign']));
SELECT pg_temp.rec_checks('T29', 'ledger amount = 0 → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO ledger_entry (id, operation_id, entry_seq, account, account_id, amount)
                        VALUES ('acc_le_z', 'acc_opm_2', 9, 'CUSTOMER_THB', 'CUSTOMER_THB:acc_cus_2', '0')$q$,
    '23514', 'ledger_entry', NULL, ARRAY['ck_ledger_entry_amount']));
SELECT pg_temp.rec_checks('T30', 'outbox topic ไม่มี .vN → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO outbox (id, operation_id, topic, payload) VALUES ('acc_obx_bad', 'acc_opm_2', 'thb.mint', '{}')$q$,
    '23514', 'outbox', NULL, ARRAY['ck_outbox_topic_version']));

DO $$
DECLARE n1 int; n2 int;
BEGIN
  INSERT INTO chain_event (id, block_number, block_hash, tx_hash, log_index, address, topic0, topics, data, block_timestamp)
  VALUES ('acc_ce_r1', 900000006, '0x' || repeat('bc', 32), '0x' || repeat('ec', 32), 0,
          '0x' || repeat('dc', 20), '0x' || repeat('00', 32), '[]', '\x00', now())
  ON CONFLICT (tx_hash, log_index) DO NOTHING;
  GET DIAGNOSTICS n1 = ROW_COUNT;
  INSERT INTO chain_event (id, block_number, block_hash, tx_hash, log_index, address, topic0, topics, data, block_timestamp)
  VALUES ('acc_ce_r2', 900000006, '0x' || repeat('bc', 32), '0x' || repeat('ec', 32), 0,
          '0x' || repeat('dc', 20), '0x' || repeat('00', 32), '[]', '\x00', now())
  ON CONFLICT (tx_hash, log_index) DO NOTHING;
  GET DIAGNOSTICS n2 = ROW_COUNT;
  PERFORM pg_temp.rec('T31', 'อ่าน log ช่วงเดิมซ้ำ → chain_event ไม่เกิดแถวซ้ำ',
                      CASE WHEN n1 = 1 AND n2 = 0 THEN 'PASS' ELSE 'FAIL' END, format('ได้ %s · %s', n1, n2));
END $$;

DO $$
DECLARE n int; v_idx text;
BEGIN
  SELECT count(*) INTO n FROM psp_webhook WHERE processed_at IS NULL AND signature_ok AND id = 'acc_wh_bad';
  SELECT indexdef INTO v_idx FROM pg_indexes WHERE indexname = 'ix_psp_unprocessed';
  PERFORM pg_temp.rec('T32', 'webhook ลายเซ็นไม่ผ่านไม่อยู่ในคิว · คิวมี index กรอง signature_ok',
    CASE WHEN n = 0 AND v_idx LIKE '%signature_ok%' THEN 'PASS' ELSE 'FAIL' END, coalesce(v_idx, 'ไม่มี index'));
END $$;

-- =====================================================================
-- T33 · ปิดท้าย · ตรวจตอน commit ทั้งหมดผ่าน · ledger รวม 0 · ทุกยอดตรงกับ ledger (ทั้งฐาน)
-- =====================================================================
DO $$
DECLARE r text; v_sum numeric; v_bad text;
BEGIN
  r := pg_temp.try_ok('SET CONSTRAINTS ALL IMMEDIATE');
  SELECT coalesce(sum(amount::numeric), 0) INTO v_sum FROM ledger_entry;
  SELECT string_agg(b.account_id, ',') INTO v_bad FROM thb_balance b
   WHERE b.amount::numeric <> coalesce((SELECT sum(l.amount::numeric) FROM ledger_entry l
                                         WHERE l.account = b.account AND l.account_id = b.account_id), 0);
  PERFORM pg_temp.rec_checks('T33', 'ปิดท้าย · ตรวจตอน commit ผ่าน · ledger รวม 0 · ทุกยอดตรงกับ ledger', r,
    CASE WHEN v_sum = 0 AND v_bad IS NULL THEN NULL ELSE format('FAIL: ผลรวม %s · ไม่ตรง %s', v_sum, v_bad) END);
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
