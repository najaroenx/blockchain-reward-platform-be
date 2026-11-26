-- =====================================================================
-- FEAT-10 · Buy Coupon · acceptance สำหรับ blockchain-reward-platform-be
--
-- ต้นฉบับ : blockchain-reward-platform/reference/feat-10/tests/acceptance.sql (36 ข้อ)
-- schema  : migrations 0002 → 0013_feat10_purchase
--
-- รัน (Postgres ใน docker):
--   docker exec -i blockchain-reward-platform-be-postgres-1 \
--     psql -U reward -d reward < tests/sql/feat10_acceptance.sql
--
-- ผล  PASS ฐานข้อมูลบังคับกฎนี้ได้จริง · FAIL ไม่ได้ หรือถูกปฏิเสธด้วยกฎตัวอื่น
--     ERROR test เองรันไม่ได้ · ดูหมายเหตุ
--
-- ต่างจากต้นฉบับ · หลักฐานอยู่ใน docs/reference-findings.md
--   1. Marketplace จริง buyCoupon(listingId, amount) ล็อก THB ไว้ใน Vault escrow · ผู้ขายได้เงินตอน
--      redeem (FEAT-11) หรือคืนผู้ซื้อตอนหมดอายุ · ledger ตอนซื้อจึงย้ายเงินผู้ซื้อ → ESCROW:<purchase>
--      ไม่ใช่ → ผู้ขาย + platform (T15) · fee / net ถูกคิดและบันทึกไว้บน purchase ตามต้นฉบับ
--   2. step จริง approve (THB ERC-20) → buyCoupon (T25 T26)
--   3. account_id ของ THB ขึ้นต้นด้วยชื่อบัญชี เช่น CUSTOMER_THB:cus_1 · ตรวจทั้งสองตาราง (T12b)
--   4. เพิ่ม T14b ledger ที่ไม่ได้อัปเดตยอด · T18b ledger แก้/ลบไม่ได้ · T20b purchase ต้องตรงกับ listing
--      T23b อัตรา fee ต้องเป็นอัตราที่มีผล ณ ตอนซื้อ
--   5. deferred trigger ตรวจด้วย SET CONSTRAINTS <ชื่อ> IMMEDIATE ใน savepoint ของข้อนั้น
--   6. จำนวนเงินเป็นสตริง (0002) · ตรวจรูปแบบด้วย f_is_int256 / f_is_uint256
--   7. T15 เขียนแถว escrow ของ Vault คู่กับบัญชี ESCROW (ต้องมีตั้งแต่ 0014 · FEAT-11) · ต้องรันที่ head
--   8. เงินเข้าระบบผ่านการฝากจริง (webhook → การฝากที่ยืนยัน → thb_mint) ตาม FEAT-12 (0015)
--   9. ข้อมูลทดสอบขึ้นต้นด้วย acc_ · ข้อ "ต้องถูกปฏิเสธ" ตรวจว่าชนกฎที่ตั้งใจจริง
--
-- ทั้งไฟล์อยู่ใน transaction เดียวแล้ว ROLLBACK · รันซ้ำกี่รอบก็ได้
-- =====================================================================
\set ON_ERROR_STOP on
\pset format aligned

BEGIN;

-- ควรได้ 0013_feat10_purchase ขึ้นไป
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


-- เลขเงินใช้บ่อย · 1 THB = 10^18 base units
CREATE FUNCTION pg_temp.thb(p numeric) RETURNS text LANGUAGE sql IMMUTABLE AS
$$ SELECT (p * 1000000000000000000)::numeric(78,0)::text $$;

-- ------------------------------------------------------------ fixture
INSERT INTO merchant (id, name, status, activated_at) VALUES
  ('acc_mch_a', 'ร้านผู้ขาย', 'ACTIVE', now()),
  ('acc_mch_b', 'ร้านอื่น', 'ACTIVE', now());
INSERT INTO signer (address, key_ref) VALUES ('0x' || repeat('3d', 20), 'kms/acc-signer-1');
INSERT INTO wallet (id, address, key_ref, owner_kind, owner_id) VALUES
  ('acc_wal_s', '0x' || repeat('d1', 20), 'kms/acc-seller', 'MERCHANT', 'acc_mch_a'),
  ('acc_wal_rich', '0x' || repeat('d2', 20), 'kms/acc-rich', 'CUSTOMER', 'acc_cus_rich');

-- อัตราค่าธรรมเนียม · ทั้งฐานข้อมูลใช้ตารางเดียว จึงวางสองช่วงไว้ใกล้ปัจจุบันมาก
-- 250 bps ตั้งแต่ 1 ชั่วโมงก่อน · 300 bps ตั้งแต่ 30 วันข้างหน้า
INSERT INTO fee_schedule (id, bps, effective_from, note) VALUES
  ('acc_fee_v1', 250, now() - interval '1 hour', '2.50%'),
  ('acc_fee_v2', 300, now() + interval '30 days', '3.00% ในอนาคต');

INSERT INTO coupon_type (id, merchant_id, name, name_normalized, face_value, total_supply, status,
                         expires_at, live_at)
VALUES ('acc_ct_1', 'acc_mch_a', 'คูปอง 100 บาท', 'คูปอง 100 บาท', pg_temp.thb(100), 100, 'LIVE',
        now() + interval '180 days', now());
INSERT INTO coupon_type_chain (coupon_type_id, contract_address, chain_type_id, deployed_block, deployed_tx_hash)
VALUES ('acc_ct_1', '0x' || repeat('ca', 20), '1', 1001, '0x' || repeat('c1', 32));

INSERT INTO coupon_code (id, coupon_type_id, code_hash, code_ref, state, holder_kind, holder_id, held_at)
SELECT 'acc_cc_' || g, 'acc_ct_1', pg_temp.h('C' || g), pg_temp.h('acc_cc_' || g),
       'HELD', 'MERCHANT', 'acc_mch_a', now()
  FROM generate_series(1, 20) g;

-- นำ THB เข้าระบบอย่างซื่อสัตย์ · webhook ที่ลงนาม → การฝากที่ยืนยัน → thb_mint → ledger คู่กับ ISSUANCE
-- (FEAT-12 · 0015 ตรวจว่า ISSUANCE ขยับได้เฉพาะจากการฝากจริง)
CREATE FUNCTION pg_temp.deposit(p_account text, p_id text, p_amount text) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE v_op text := 'acc_opm_' || p_id; v_dep text := 'acc_dep_' || p_id; v_acc text := p_account || ':' || p_id;
BEGIN
  INSERT INTO psp_webhook (id, provider, provider_event_id, event_type, raw_body, signature, signature_ok)
  VALUES ('acc_wh_' || p_id, 'acc_psp', 'evt_' || p_id, 'charge.complete', '\x00', 'sig', true);
  INSERT INTO thb_deposit (id, holder_kind, holder_id, provider, provider_ref, amount_satang, amount_base, expires_at)
  VALUES (v_dep, CASE p_account WHEN 'MERCHANT_THB' THEN 'MERCHANT' ELSE 'CUSTOMER' END, p_id, 'acc_psp',
          'ref_' || p_id, (p_amount::numeric / 10000000000000000)::bigint, p_amount, now() + interval '1 day');
  UPDATE thb_deposit SET status = 'CONFIRMED', webhook_id = 'acc_wh_' || p_id, confirmed_at = now() WHERE id = v_dep;
  INSERT INTO operation (id, actor_scope, kind, subject_id, payload) VALUES (v_op, 'system:minter', 'THB_MINT', v_dep, '{}');
  INSERT INTO outbox (id, operation_id, topic, payload) VALUES (v_op || '_o', v_op, 'thb.mint.v1', '{}');
  INSERT INTO thb_mint (deposit_id, operation_id, amount_base, to_account, to_account_id)
  VALUES (v_dep, v_op, p_amount, p_account, v_acc);
  INSERT INTO ledger_entry (id, operation_id, entry_seq, account, account_id, amount, memo) VALUES
    (v_op || '_a', v_op, 1, p_account, v_acc, p_amount, 'ฝากเข้า'),
    (v_op || '_b', v_op, 2, 'ISSUANCE', 'ISSUANCE:thb', '-' || p_amount, 'คู่บัญชี');
  INSERT INTO thb_balance (account, account_id, amount) VALUES (p_account, v_acc, p_amount)
  ON CONFLICT (account, account_id)
    DO UPDATE SET amount = (thb_balance.amount::numeric + p_amount::numeric)::text;
  INSERT INTO thb_balance (account, account_id, amount) VALUES ('ISSUANCE', 'ISSUANCE:thb', '-' || p_amount)
  ON CONFLICT (account, account_id)
    DO UPDATE SET amount = (thb_balance.amount::numeric - p_amount::numeric)::text;
  UPDATE operation SET status = 'SUBMITTED' WHERE id = v_op AND status = 'PENDING';
  UPDATE operation SET status = 'CONFIRMED' WHERE id = v_op AND status = 'SUBMITTED';
  UPDATE operation SET status = 'FINALIZED', finalized_at = now() WHERE id = v_op;
  UPDATE thb_deposit SET status = 'MINTED', minted_at = now() WHERE id = v_dep;
END $$;

SELECT pg_temp.deposit('CUSTOMER_THB', 'acc_cus_rich', pg_temp.thb(1000));
SELECT pg_temp.deposit('CUSTOMER_THB', 'acc_cus_two', pg_temp.thb(200));
SELECT pg_temp.deposit('CUSTOMER_THB', 'acc_cus_x', pg_temp.thb(500));
-- acc_cus_poor ไม่มีเงินและไม่มีแถว thb_balance

-- รายการขายที่ ACTIVE แล้ว · ผลของ FEAT-08
CREATE FUNCTION pg_temp.mk_active(p_lst text, p_codes text[], p_unit text, p_chain_id text)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE c text; i int := 0;
BEGIN
  INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
  VALUES ('acc_opc_' || p_lst, 'merchant:acc_mch_a', 'LISTING_CREATE', p_lst, '{}');
  UPDATE operation SET status = 'SUBMITTED' WHERE id = 'acc_opc_' || p_lst;
  UPDATE operation SET status = 'CONFIRMED' WHERE id = 'acc_opc_' || p_lst;
  UPDATE operation SET status = 'FINALIZED', finalized_at = now() WHERE id = 'acc_opc_' || p_lst;
  INSERT INTO outbox (id, operation_id, topic, payload)
  VALUES ('acc_obc_' || p_lst, 'acc_opc_' || p_lst, 'listing.create.v1', '{}');
  INSERT INTO listing (id, seller_kind, seller_id, coupon_type_id, unit_price, quantity)
  VALUES (p_lst, 'MERCHANT', 'acc_mch_a', 'acc_ct_1', p_unit, array_length(p_codes, 1));
  FOREACH c IN ARRAY p_codes LOOP
    i := i + 1;
    INSERT INTO listing_item (id, listing_id, coupon_code_id) VALUES (p_lst || '_i' || i, p_lst, c);
    INSERT INTO reservation (id, operation_id, subject_type, subject_id, amount)
    VALUES (p_lst || '_r' || i, 'acc_opc_' || p_lst, 'COUPON_UNIT', c, '1');
  END LOOP;
  INSERT INTO listing_chain (listing_id, contract_address, chain_listing_id, created_block, created_tx_hash)
  VALUES (p_lst, '0x' || repeat('da', 20), p_chain_id, 1000, pg_temp.h(p_lst));
  UPDATE listing SET status = 'ACTIVE', active_at = now() WHERE id = p_lst;
END $$;

SELECT pg_temp.mk_active('acc_lst_a', ARRAY['acc_cc_1', 'acc_cc_2'], pg_temp.thb(80), '9101');
SELECT pg_temp.mk_active('acc_lst_b', ARRAY['acc_cc_3'],  pg_temp.thb(80), '9102');
SELECT pg_temp.mk_active('acc_lst_c', ARRAY['acc_cc_4'],  pg_temp.thb(80), '9103');
SELECT pg_temp.mk_active('acc_lst_d', ARRAY['acc_cc_5'],  pg_temp.thb(80), '9104');
SELECT pg_temp.mk_active('acc_lst_e', ARRAY['acc_cc_6'],  pg_temp.thb(80), '9105');
SELECT pg_temp.mk_active('acc_lst_f', ARRAY['acc_cc_7'],  pg_temp.thb(80), '9106');
SELECT pg_temp.mk_active('acc_lst_g', ARRAY['acc_cc_8'],  pg_temp.thb(80), '9107');
SELECT pg_temp.mk_active('acc_lst_h', ARRAY['acc_cc_9'],  pg_temp.thb(80), '9108');
-- ราคาที่หารด้วย 10000 ไม่ลงตัว · ทดสอบการปัดเศษ
SELECT pg_temp.mk_active('acc_lst_odd', ARRAY['acc_cc_10'], '3333', '9109');

-- คำขอซื้อ · คืน SQL ของ INSERT operation (+ outbox)
CREATE FUNCTION pg_temp.buy(p_op text, p_scope text, p_lst text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT format($q$INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
                   VALUES (%L, %L, 'LISTING_BUY', %L, '{}');
                   INSERT INTO outbox (id, operation_id, topic, payload)
                   VALUES (%L, %L, 'listing.buy.v1', '{}')$q$,
                p_op, p_scope, p_lst, p_op || '_o', p_op)
$$;

-- purchase · คืน SQL ของ INSERT
CREATE FUNCTION pg_temp.purchase(p_id text, p_lst text, p_buyer text, p_op text, p_unit text, p_qty int,
                                 p_total text, p_bps int, p_fee text, p_net text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT format($q$INSERT INTO purchase (id, listing_id, buyer_kind, buyer_id, seller_kind, seller_id,
                                         unit_price, quantity, total_price, fee_bps, fee_amount,
                                         net_to_seller, operation_id)
                   VALUES (%L, %L, 'CUSTOMER', %L, 'MERCHANT', 'acc_mch_a', %L, %s, %L, %s, %L, %L, %L)$q$,
                p_id, p_lst, p_buyer, p_unit, p_qty, p_total, p_bps, p_fee, p_net, p_op)
$$;

CREATE FUNCTION pg_temp.thb_reserve(p_id text, p_op text, p_subject text, p_amount text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT format($q$INSERT INTO reservation (id, operation_id, subject_type, subject_id, amount)
                   VALUES (%L, %L, 'THB_BALANCE', %L, %L)$q$, p_id, p_op, p_subject, p_amount)
$$;

-- =====================================================================
-- T1 · Idempotency-Key เดิมซ้ำ → ไม่เกิดงานใหม่                RFD 3 R1
-- =====================================================================
DO $$
DECLARE n1 int; n2 int;
BEGIN
  INSERT INTO idempotency_key (id, scope, key, fingerprint, expires_at)
  VALUES ('acc_ik_1', 'customer:acc_cus_rich', 'buy-1111-2222-3333', pg_temp.h('b'), now() + interval '1 day')
  ON CONFLICT (scope, key) DO NOTHING;
  GET DIAGNOSTICS n1 = ROW_COUNT;
  INSERT INTO idempotency_key (id, scope, key, fingerprint, expires_at)
  VALUES ('acc_ik_2', 'customer:acc_cus_rich', 'buy-1111-2222-3333', pg_temp.h('b'), now() + interval '1 day')
  ON CONFLICT (scope, key) DO NOTHING;
  GET DIAGNOSTICS n2 = ROW_COUNT;
  PERFORM pg_temp.rec('T1', 'ซื้อซ้ำด้วย key เดิม → แถวที่สองได้ 0 rows',
                      CASE WHEN n1 = 1 AND n2 = 0 THEN 'PASS' ELSE 'FAIL' END, format('ได้ %s · %s', n1, n2));
END $$;

-- =====================================================================
-- T2 · ซื้อรายการ ACTIVE ของคนอื่น → ยอมรับ
-- T3 · ซื้อรายการขายของตัวเอง → ถูกปฏิเสธ
-- T4 · ซื้อรายการที่ไม่มีอยู่ → ถูกปฏิเสธ
-- T5 · ซื้อรายการที่ DELISTED แล้ว → ถูกปฏิเสธ
-- =====================================================================
SELECT pg_temp.rec_checks('T2', 'ซื้อรายการ ACTIVE ของคนอื่น → ยอมรับ',
  pg_temp.try_ok(pg_temp.buy('acc_opb_a', 'customer:acc_cus_rich', 'acc_lst_a')));
SELECT pg_temp.rec_checks('T3', 'ซื้อรายการขายของตัวเอง → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.buy('acc_opb_self', 'merchant:acc_mch_a', 'acc_lst_b'),
    '23514', 'operation', NULL, ARRAY['trg_buy_admissible']));
SELECT pg_temp.rec_checks('T4', 'ซื้อรายการที่ไม่มีอยู่ → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.buy('acc_opb_ghost', 'customer:acc_cus_rich', 'acc_lst_ไม่มีจริง'),
    '23514', 'operation', NULL, ARRAY['trg_buy_admissible']));
UPDATE listing SET status = 'DELISTED', closed_at = now() WHERE id = 'acc_lst_c';
SELECT pg_temp.rec_checks('T5', 'ซื้อรายการที่ DELISTED แล้ว → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.buy('acc_opb_c', 'customer:acc_cus_rich', 'acc_lst_c'),
    '23514', 'operation', NULL, ARRAY['trg_buy_admissible']));

-- =====================================================================
-- T6 · F4 · ยอด THB ไม่พอ → ถูกปฏิเสธ · ไม่มี autoMint
-- T7 · F4 · หลังถูกปฏิเสธ ไม่มีเงินงอกขึ้นมาและไม่มี ledger
-- =====================================================================
SELECT pg_temp.try_ok(pg_temp.buy('acc_opb_poor', 'customer:acc_cus_poor', 'acc_lst_d'));
SELECT pg_temp.rec_checks('T6', 'F4 · จอง THB ที่ไม่มี → ถูกปฏิเสธ ไม่เสกเงินให้',
  pg_temp.try_reject(pg_temp.thb_reserve('acc_rsv_poor', 'acc_opb_poor', 'CUSTOMER_THB:acc_cus_poor', pg_temp.thb(80)),
    '23514', 'reservation', NULL, ARRAY['trg_thb_reservation_balance']));
DO $$
DECLARE n_bal int; n_led int;
BEGIN
  SELECT count(*) INTO n_bal FROM thb_balance WHERE account_id = 'CUSTOMER_THB:acc_cus_poor';
  SELECT count(*) INTO n_led FROM ledger_entry WHERE account_id = 'CUSTOMER_THB:acc_cus_poor';
  PERFORM pg_temp.rec('T7', 'F4 · ปฏิเสธแล้วไม่มีแถวยอดและไม่มี ledger งอกขึ้น',
                      CASE WHEN n_bal = 0 AND n_led = 0 THEN 'PASS' ELSE 'FAIL' END, format('%s · %s', n_bal, n_led));
END $$;
-- ลูกค้าจนยกเลิกคำขอ · ไม่ให้ค้างกันรายการ d
UPDATE operation SET status = 'SUBMITTED' WHERE id = 'acc_opb_poor' AND status = 'PENDING';
UPDATE operation SET status = 'CHAIN_REVERTED' WHERE id = 'acc_opb_poor';

-- =====================================================================
-- T8 · สองคนซื้อรายการเดียวกัน → คนที่สองชน uq_listing_inflight
-- T9 · ซื้อขณะร้านกดถอดค้างอยู่ → ถูกปฏิเสธ
-- =====================================================================
SELECT pg_temp.rec_checks('T8', 'สองคนซื้อรายการเดียวกัน → คนที่สองถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.buy('acc_opb_a2', 'customer:acc_cus_two', 'acc_lst_a'),
    '23505', 'operation', ARRAY['subject_id'], ARRAY['uq_listing_inflight']));

INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
VALUES ('acc_opd_e', 'merchant:acc_mch_a', 'LISTING_DELETE', 'acc_lst_e', '{}');
UPDATE operation SET status = 'SUBMITTED' WHERE id = 'acc_opd_e';
INSERT INTO outbox (id, operation_id, topic, payload) VALUES ('acc_obd_e', 'acc_opd_e', 'listing.delete.v1', '{}');
SELECT pg_temp.rec_checks('T9', 'ซื้อขณะมีคำขอถอดค้าง → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.buy('acc_opb_e', 'customer:acc_cus_rich', 'acc_lst_e'),
    '23505', 'operation', ARRAY['subject_id'], ARRAY['uq_listing_inflight']));

-- =====================================================================
-- T10 · THB บวกกันได้ · จองสองครั้งไม่เกินยอด → ผ่านทั้งคู่
-- T11 · ครั้งที่สามรวมเกินยอด → ถูกปฏิเสธ (มี 500 · จองไป 400 · ขออีก 200)
-- T12 · subject_id ของ THB_BALANCE รูปแบบเพี้ยน → ถูกปฏิเสธด้วย 23514 ไม่ใช่ error อื่น
-- =====================================================================
SELECT pg_temp.try_ok(pg_temp.buy('acc_opb_x1', 'customer:acc_cus_x', 'acc_lst_f'));
SELECT pg_temp.try_ok(pg_temp.buy('acc_opb_x2', 'customer:acc_cus_x', 'acc_lst_g'));
SELECT pg_temp.try_ok(pg_temp.buy('acc_opb_x3', 'customer:acc_cus_x', 'acc_lst_h'));

DO $$
DECLARE r text[]; n int;
BEGIN
  r[1] := pg_temp.try_ok(pg_temp.thb_reserve('acc_rsv_x1', 'acc_opb_x1', 'CUSTOMER_THB:acc_cus_x', pg_temp.thb(200)));
  r[2] := pg_temp.try_ok(pg_temp.thb_reserve('acc_rsv_x2', 'acc_opb_x2', 'CUSTOMER_THB:acc_cus_x', pg_temp.thb(200)));
  SELECT count(*) INTO n FROM reservation
   WHERE subject_type = 'THB_BALANCE' AND subject_id = 'CUSTOMER_THB:acc_cus_x' AND released_at IS NULL;
  IF n <> 2 THEN r[3] := format('FAIL: จองค้าง %s ไม่ใช่ 2', n); END IF;
  PERFORM pg_temp.rec_checks('T10', 'THB บวกกันได้ · จองสองครั้งรวม 400 จาก 500 → ผ่านทั้งคู่', VARIADIC r);
END $$;

SELECT pg_temp.rec_checks('T11', 'จองครั้งที่สามรวมเกินยอด → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.thb_reserve('acc_rsv_x3', 'acc_opb_x3', 'CUSTOMER_THB:acc_cus_x', pg_temp.thb(200)),
    '23514', 'reservation', NULL, ARRAY['trg_thb_reservation_balance']));

SELECT pg_temp.rec_checks('T12', 'subject_id ของ THB ไม่มีรูปแบบ / เป็นบัญชีที่จ่ายไม่ได้ → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.thb_reserve('acc_rsv_bad', 'acc_opb_x3', 'ไม่มีโคลอน', '1'),
    '23514', 'reservation', NULL, ARRAY['ck_reservation_thb_subject']),
  pg_temp.try_reject(pg_temp.thb_reserve('acc_rsv_bad2', 'acc_opb_x3', 'ISSUANCE:thb', '1'),
    '23514', 'reservation', NULL, ARRAY['ck_reservation_thb_subject']));

-- T12b · account_id ต้องขึ้นต้นด้วยชื่อบัญชีของตัวเอง
SELECT pg_temp.rec_checks('T12b', 'account_id ไม่ตรงกับ account (MERCHANT_THB:acc_cus_x ในบัญชี CUSTOMER_THB) → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO thb_balance (account, account_id, amount)
                        VALUES ('CUSTOMER_THB', 'MERCHANT_THB:acc_cus_x', '0')$q$,
    '23514', 'thb_balance', NULL, ARRAY['ck_thb_balance_account_id']),
  pg_temp.try_reject($q$INSERT INTO ledger_entry (id, operation_id, entry_seq, account, account_id, amount)
                        VALUES ('acc_le_pfx', 'acc_opb_x1', 7, 'CUSTOMER_THB', 'acc_cus_x', '1')$q$,
    '23514', 'ledger_entry', NULL, ARRAY['ck_ledger_entry_account_id']));

-- =====================================================================
-- T13  · F8 · ledger ที่รวมไม่ได้ศูนย์ → ไม่ผ่านตอน commit
-- T14  · F4 · ตั้งยอดเองโดยไม่มี ledger → ไม่ผ่านตอน commit
-- T14b · เขียน ledger แต่ไม่อัปเดตยอด → ไม่ผ่านตอน commit (ต้นฉบับตรวจจากฝั่งยอดเท่านั้น)
--        ใช้บัญชีใหม่ที่ไม่มีแถวยอด · การตรวจจากฝั่งยอดจึงไม่มีโอกาสจับได้แทน
-- =====================================================================
SELECT pg_temp.rec_checks('T13', 'F8 · ledger ของ operation รวมไม่ได้ศูนย์ → ไม่ผ่าน',
  pg_temp.try_reject($q$
    INSERT INTO ledger_entry (id, operation_id, entry_seq, account, account_id, amount)
    VALUES ('acc_le_bad', 'acc_opb_a', 1, 'CUSTOMER_THB', 'CUSTOMER_THB:acc_cus_rich', '-80000000000000000000');
    UPDATE thb_balance SET amount = (amount::numeric - 80000000000000000000)::text
     WHERE account_id = 'CUSTOMER_THB:acc_cus_rich';
    SET CONSTRAINTS trg_ledger_balanced IMMEDIATE;
  $q$, '23514', 'ledger_entry', NULL, ARRAY['trg_ledger_balanced']));
SELECT pg_temp.try_ok('SET CONSTRAINTS trg_ledger_balanced DEFERRED');

SELECT pg_temp.rec_checks('T14', 'F4 · ตั้งยอดเองโดยไม่มี ledger → ไม่ผ่าน',
  pg_temp.try_reject($q$
    INSERT INTO thb_balance (account, account_id, amount)
    VALUES ('CUSTOMER_THB', 'CUSTOMER_THB:acc_cus_poor', '999000000000000000000');
    SET CONSTRAINTS trg_balance_matches_ledger IMMEDIATE;
  $q$, '23514', 'thb_balance', NULL, ARRAY['trg_balance_matches_ledger']));
SELECT pg_temp.try_ok('SET CONSTRAINTS trg_balance_matches_ledger DEFERRED');

SELECT pg_temp.rec_checks('T14b', 'เขียน ledger ที่สมดุลแต่ไม่อัปเดตยอดคงเหลือ → ไม่ผ่าน',
  pg_temp.try_reject($q$
    INSERT INTO operation (id, actor_scope, kind, payload)
    VALUES ('acc_opm_ghost', 'system:psp', 'THB_MINT', '{}');
    UPDATE operation SET status = 'SUBMITTED' WHERE id = 'acc_opm_ghost';
    UPDATE operation SET status = 'CONFIRMED' WHERE id = 'acc_opm_ghost';
    UPDATE operation SET status = 'FINALIZED', finalized_at = now() WHERE id = 'acc_opm_ghost';
    INSERT INTO ledger_entry (id, operation_id, entry_seq, account, account_id, amount) VALUES
      ('acc_le_g1', 'acc_opm_ghost', 1, 'CUSTOMER_THB', 'CUSTOMER_THB:acc_cus_ghost', '5'),
      ('acc_le_g2', 'acc_opm_ghost', 2, 'ISSUANCE', 'ISSUANCE:acc_ghost', '-5');
    SET CONSTRAINTS trg_balance_matches_ledger IMMEDIATE;
  $q$, '23514', 'ledger_entry', NULL, ARRAY['trg_balance_matches_ledger']));
SELECT pg_temp.try_ok('SET CONSTRAINTS trg_balance_matches_ledger DEFERRED');

-- =====================================================================
-- T15 · ซื้อสำเร็จ · เงินผู้ซื้อเข้า escrow (Vault) · ledger สองแถวรวมได้ศูนย์ · ยอดทุกบัญชีตรง
--       ผู้ขายยังไม่ได้เงิน · ได้ตอน redeem (FEAT-11)
--       แถว escrow (จาก FundsLocked) เขียนใน transaction เดียวกัน · 0014 ตรวจว่าตรงกับ ESCROW:<purchase>
-- =====================================================================
INSERT INTO raw_block (block_hash, block_number, parent_hash, "timestamp")
VALUES ('0x' || repeat('ba', 32), 900000004, '0x' || repeat('b6', 32), now());
-- FEAT-14 · บล็อกที่อ่านแล้วต้องอยู่ใต้ cursor ตอน commit (0017)
INSERT INTO indexer_cursor (id, last_block, last_block_hash)
VALUES ('main', 900000004, '0x' || repeat('ba', 32));

DO $$
DECLARE
  r text[];
  v_total numeric := 160000000000000000000;   -- 80 x 2 ใบ
  v_bps int := 250;
  v_fee numeric := div(160000000000000000000 * 250, 10000);
  v_buyer numeric; v_escrow numeric; v_seller numeric;
BEGIN
  r[1] := pg_temp.try_ok(pg_temp.purchase('acc_pur_a', 'acc_lst_a', 'acc_cus_rich', 'acc_opb_a',
                         pg_temp.thb(80), 2, v_total::text, v_bps, v_fee::text, (v_total - v_fee)::text));
  r[2] := pg_temp.try_ok(pg_temp.thb_reserve('acc_rsv_a', 'acc_opb_a', 'CUSTOMER_THB:acc_cus_rich', v_total::text));
  r[3] := pg_temp.try_ok(format($q$
    INSERT INTO chain_event (id, block_number, block_hash, tx_hash, log_index, address, topic0, topics, data,
                             block_timestamp, operation_id)
    VALUES ('acc_ce_buy', 900000004, '0x' || repeat('ba', 32), '0x' || repeat('c1', 32), 0,
            '0x' || repeat('da', 20), '0x' || repeat('01', 32), '[]', '\x00', now(), 'acc_opb_a');
    INSERT INTO ledger_entry (id, operation_id, entry_seq, account, account_id, amount, memo) VALUES
      ('acc_le_a1', 'acc_opb_a', 1, 'CUSTOMER_THB', 'CUSTOMER_THB:acc_cus_rich', '-%1$s', 'จ่ายค่าคูปอง'),
      ('acc_le_a2', 'acc_opb_a', 2, 'ESCROW', 'ESCROW:acc_pur_a', '%1$s', 'ล็อกใน Vault');
    UPDATE thb_balance SET amount = (amount::numeric - %1$s)::text WHERE account_id = 'CUSTOMER_THB:acc_cus_rich';
    INSERT INTO thb_balance (account, account_id, amount) VALUES ('ESCROW', 'ESCROW:acc_pur_a', '%1$s');
    INSERT INTO escrow (id, chain_token_id, contract_address, seller_wallet_id, buyer_wallet_id,
                        total_amount, remaining_amount, coupons_locked, chain_escrow_id, purchase_id)
    VALUES ('acc_esc_a', '1', '0x' || repeat('fb', 20), 'acc_wal_s', 'acc_wal_rich', '%1$s', '%1$s', 2,
            '601', 'acc_pur_a');
    UPDATE operation SET status = 'SUBMITTED' WHERE id = 'acc_opb_a' AND status = 'PENDING';
    UPDATE operation SET status = 'CONFIRMED' WHERE id = 'acc_opb_a' AND status = 'SUBMITTED';
    UPDATE operation SET status = 'FINALIZED', finalized_at = now() WHERE id = 'acc_opb_a';
    UPDATE purchase SET status = 'SETTLED', settled_at = now() WHERE id = 'acc_pur_a';
    UPDATE listing SET status = 'SOLD' WHERE id = 'acc_lst_a';
    UPDATE reservation SET released_at = now()
     WHERE operation_id IN ('acc_opb_a', 'acc_opc_acc_lst_a') AND released_at IS NULL;
    UPDATE coupon_code SET holder_kind = 'CUSTOMER', holder_id = 'acc_cus_rich' WHERE id IN ('acc_cc_1', 'acc_cc_2');
  $q$, v_total));
  r[4] := pg_temp.try_ok('SET CONSTRAINTS ALL IMMEDIATE');
  PERFORM pg_temp.try_ok('SET CONSTRAINTS ALL DEFERRED');

  SELECT amount::numeric INTO v_buyer  FROM thb_balance WHERE account_id = 'CUSTOMER_THB:acc_cus_rich';
  SELECT amount::numeric INTO v_escrow FROM thb_balance WHERE account_id = 'ESCROW:acc_pur_a';
  SELECT amount::numeric INTO v_seller FROM thb_balance WHERE account_id = 'MERCHANT_THB:acc_mch_a';
  IF v_buyer IS DISTINCT FROM 1000000000000000000000 - v_total
     OR v_escrow IS DISTINCT FROM v_total OR v_seller IS NOT NULL THEN
    r[5] := format('FAIL: ผู้ซื้อ %s · escrow %s · ผู้ขาย %s', v_buyer, v_escrow, v_seller);
  END IF;
  PERFORM pg_temp.rec_checks('T15', 'ซื้อสำเร็จ · ผู้ซื้อ −160 · escrow +160 · ผู้ขายยังไม่ได้ · ตรวจตอน commit ผ่าน',
                             VARIADIC r);
END $$;

-- =====================================================================
-- T16 · ผลรวม ledger ทั้งตาราง = ศูนย์
-- T17 · F4 · ลบ ISSUANCE = ผลรวมบัญชีอื่นทั้งหมด
-- =====================================================================
DO $$
DECLARE v numeric;
BEGIN
  SELECT coalesce(sum(amount::numeric), 0) INTO v FROM ledger_entry;
  PERFORM pg_temp.rec('T16', 'ผลรวม ledger ทั้งตาราง = ศูนย์', CASE WHEN v = 0 THEN 'PASS' ELSE 'FAIL' END, v::text);
END $$;

DO $$
DECLARE v_iss numeric; v_rest numeric;
BEGIN
  SELECT coalesce(sum(amount::numeric), 0) INTO v_iss  FROM thb_balance WHERE account = 'ISSUANCE';
  SELECT coalesce(sum(amount::numeric), 0) INTO v_rest FROM thb_balance WHERE account <> 'ISSUANCE';
  PERFORM pg_temp.rec('T17', 'F4 · ลบ ISSUANCE = ผลรวมบัญชีอื่นทั้งหมด',
                      CASE WHEN -v_iss = v_rest THEN 'PASS' ELSE 'FAIL' END, format('%s · %s', -v_iss, v_rest));
END $$;

-- =====================================================================
-- T18  · ledger amount = 0 หรือไม่ใช่ตัวเลข → ถูกปฏิเสธ
-- T18b · ledger แก้หรือลบไม่ได้ · แก้ผิดด้วยแถวกลับรายการเท่านั้น
-- =====================================================================
SELECT pg_temp.rec_checks('T18', 'ledger amount 0 / -0 / 1e3 → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO ledger_entry (id, operation_id, entry_seq, account, account_id, amount)
                        VALUES ('acc_le_z', 'acc_opb_a', 9, 'CUSTOMER_THB', 'CUSTOMER_THB:acc_cus_rich', '0')$q$,
    '23514', 'ledger_entry', NULL, ARRAY['ck_ledger_entry_amount']),
  pg_temp.try_reject($q$INSERT INTO ledger_entry (id, operation_id, entry_seq, account, account_id, amount)
                        VALUES ('acc_le_z2', 'acc_opb_a', 9, 'CUSTOMER_THB', 'CUSTOMER_THB:acc_cus_rich', '-0')$q$,
    '23514', 'ledger_entry', NULL, ARRAY['ck_ledger_entry_amount']),
  pg_temp.try_reject($q$INSERT INTO ledger_entry (id, operation_id, entry_seq, account, account_id, amount)
                        VALUES ('acc_le_z3', 'acc_opb_a', 9, 'CUSTOMER_THB', 'CUSTOMER_THB:acc_cus_rich', '1e3')$q$,
    '23514', 'ledger_entry', NULL, ARRAY['ck_ledger_entry_amount']));

SELECT pg_temp.rec_checks('T18b', 'แก้หรือลบแถว ledger → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE ledger_entry SET memo = 'แก้' WHERE id = 'acc_le_a1'$q$,
    '23514', 'ledger_entry', NULL, ARRAY['trg_ledger_append_only']),
  pg_temp.try_reject($q$DELETE FROM ledger_entry WHERE id = 'acc_le_a1'$q$,
    '23514', 'ledger_entry', NULL, ARRAY['trg_ledger_append_only']));

-- =====================================================================
-- T19 · fee + net ≠ total → ถูกปฏิเสธ (ขาดหนึ่งหน่วยก็ไม่ได้)
-- T20 · total ≠ unit × qty → ถูกปฏิเสธ
-- T20b · purchase ต้องตรงกับ listing และ operation ของตัวเอง
-- T21 · fee ไม่ตรงกับ bps → ถูกปฏิเสธ แม้ fee + net = total
-- T22 · ปัดเศษลง · เศษเป็นของผู้ขาย (3333 x 250 / 10000 → fee 83 · net 3250)
-- =====================================================================
SELECT pg_temp.rec_checks('T19', 'fee + net ขาดหนึ่งหน่วย → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.purchase('acc_pur_b1', 'acc_lst_f', 'acc_cus_x', 'acc_opb_x1', pg_temp.thb(80), 1,
                     pg_temp.thb(80), 250, pg_temp.thb(2), '77999999999999999999'),
    '23514', 'purchase', NULL, ARRAY['trg_purchase_math']));
SELECT pg_temp.rec_checks('T20', 'total ไม่เท่ากับ unit × qty → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.purchase('acc_pur_b2', 'acc_lst_f', 'acc_cus_x', 'acc_opb_x1', pg_temp.thb(80), 1,
                     pg_temp.thb(160), 250, pg_temp.thb(4), pg_temp.thb(156)),
    '23514', 'purchase', NULL, ARRAY['trg_purchase_math']));
SELECT pg_temp.rec_checks('T20b', 'ราคาไม่ตรง listing · ผู้ซื้อไม่ตรง operation · operation ของรายการอื่น → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.purchase('acc_pur_b4', 'acc_lst_f', 'acc_cus_x', 'acc_opb_x1', pg_temp.thb(70), 1,
                     pg_temp.thb(70), 250, '1750000000000000000', '68250000000000000000'),
    '23514', 'purchase', NULL, ARRAY['trg_purchase_terms']),
  pg_temp.try_reject(pg_temp.purchase('acc_pur_b5', 'acc_lst_f', 'acc_cus_two', 'acc_opb_x1', pg_temp.thb(80), 1,
                     pg_temp.thb(80), 250, pg_temp.thb(2), pg_temp.thb(78)),
    '23514', 'purchase', NULL, ARRAY['trg_purchase_terms']),
  pg_temp.try_reject(pg_temp.purchase('acc_pur_b6', 'acc_lst_f', 'acc_cus_x', 'acc_opb_x2', pg_temp.thb(80), 1,
                     pg_temp.thb(80), 250, pg_temp.thb(2), pg_temp.thb(78)),
    '23514', 'purchase', NULL, ARRAY['trg_purchase_terms']));
SELECT pg_temp.rec_checks('T21', 'fee ไม่ตรงกับ bps (fee 8 · net 72 · รวม 80) → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.purchase('acc_pur_b3', 'acc_lst_f', 'acc_cus_x', 'acc_opb_x1', pg_temp.thb(80), 1,
                     pg_temp.thb(80), 250, pg_temp.thb(8), pg_temp.thb(72)),
    '23514', 'purchase', NULL, ARRAY['trg_purchase_math']));

SELECT pg_temp.try_ok(pg_temp.buy('acc_opb_odd', 'customer:acc_cus_two', 'acc_lst_odd'));
DO $$
DECLARE r text; v_fee text; v_net text;
BEGIN
  r := pg_temp.try_ok(pg_temp.purchase('acc_pur_odd', 'acc_lst_odd', 'acc_cus_two', 'acc_opb_odd', '3333', 1,
                      '3333', 250, div(3333 * 250, 10000)::text, (3333 - div(3333 * 250, 10000))::text));
  SELECT fee_amount, net_to_seller INTO v_fee, v_net FROM purchase WHERE id = 'acc_pur_odd';
  PERFORM pg_temp.rec_checks('T22', 'ปัดเศษลง · เศษเป็นของผู้ขาย (fee 83 · net 3250)', r,
    CASE WHEN v_fee = '83' AND v_net = '3250' THEN NULL ELSE format('FAIL: fee %s · net %s', v_fee, v_net) END);
END $$;

-- =====================================================================
-- T23  · อัตรา fee อิงเวลา · purchase เก็บอัตราของตัวเองไว้
-- T23b · purchase ต้องใช้อัตราที่มีผล ณ ตอนซื้อ
-- =====================================================================
DO $$
DECLARE v_now int; v_future int; v_saved int;
BEGIN
  v_now    := f_fee_bps_at(now());
  v_future := f_fee_bps_at(now() + interval '60 days');
  SELECT fee_bps INTO v_saved FROM purchase WHERE id = 'acc_pur_a';
  PERFORM pg_temp.rec('T23', 'อัตรา fee อิงเวลา (ตอนนี้ 250 · อีก 60 วัน 300) · การซื้อเก่าเก็บ 250',
    CASE WHEN v_now = 250 AND v_future = 300 AND v_saved = 250 THEN 'PASS' ELSE 'FAIL' END,
    format('%s · %s · %s', v_now, v_future, v_saved));
EXCEPTION WHEN OTHERS THEN
  PERFORM pg_temp.rec('T23', 'อัตรา fee อิงเวลา · การซื้อเก่าเก็บอัตราของตัวเอง', 'ERROR', SQLSTATE || ' · ' || SQLERRM);
END $$;

SELECT pg_temp.rec_checks('T23b', 'ใช้อัตรา 300 ทั้งที่อัตราตอนนี้คือ 250 → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.purchase('acc_pur_b7', 'acc_lst_f', 'acc_cus_x', 'acc_opb_x1', pg_temp.thb(80), 1,
                     pg_temp.thb(80), 300, '2400000000000000000', '77600000000000000000'),
    '23514', 'purchase', NULL, ARRAY['trg_purchase_terms']));

-- =====================================================================
-- T24 · แก้ราคา / จำนวน / ผู้ขายของรายการขาย → ถูกปฏิเสธ
-- =====================================================================
SELECT pg_temp.rec_checks('T24', 'แก้ราคาหรือผู้ขายของรายการที่ ACTIVE → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE listing SET unit_price = '10' WHERE id = 'acc_lst_f'$q$,
    '23514', 'listing', NULL, ARRAY['trg_listing_terms_fixed']),
  pg_temp.try_reject($q$UPDATE listing SET seller_id = 'acc_mch_b' WHERE id = 'acc_lst_f'$q$,
    '23514', 'listing', NULL, ARRAY['trg_listing_terms_fixed']));

-- =====================================================================
-- T25 · ซื้อ = สอง step เรียง approve (THB) → buyCoupon
-- T26 · ยิง buyCoupon ก่อน approve จบ → ถูกปฏิเสธ
-- T27 · R2 · SUBMITTED โดยไม่มี raw_tx → ถูกปฏิเสธ
-- =====================================================================
DO $$
DECLARE r text; v text[];
BEGIN
  r := pg_temp.try_ok($q$INSERT INTO operation_step (id, operation_id, step_index, name) VALUES
                          ('acc_st_f1', 'acc_opb_x1', 1, 'approve'), ('acc_st_f2', 'acc_opb_x1', 2, 'buyCoupon')$q$);
  SELECT array_agg(name ORDER BY step_index) INTO v FROM operation_step WHERE operation_id = 'acc_opb_x1';
  PERFORM pg_temp.rec_checks('T25', 'ซื้อ = 2 step เรียง approve → buyCoupon', r,
    CASE WHEN v = ARRAY['approve', 'buyCoupon'] THEN NULL ELSE 'FAIL: ได้ ' || coalesce(array_to_string(v, ','), '-') END);
END $$;

SELECT pg_temp.rec_checks('T26', 'ยิง buyCoupon ก่อน approve FINALIZED → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE operation_step SET status = 'SUBMITTED', raw_tx = '\x02', submitted_at = now()
                        WHERE id = 'acc_st_f2'$q$,
    '23514', 'operation_step', NULL, ARRAY['trg_step_order_gate']));
SELECT pg_temp.rec_checks('T27', 'R2 · SUBMITTED โดยไม่มี raw_tx → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO operation_step (id, operation_id, step_index, name, status)
                        VALUES ('acc_st_nr', 'acc_opb_x2', 1, 'approve', 'SUBMITTED')$q$,
    '23514', 'operation_step', NULL, ARRAY['ck_rawtx_before_broadcast']));

-- =====================================================================
-- T28 · ยอดคูปองที่ถือ อ้าง chain_event ที่ไม่มีจริงไม่ได้
-- T29 · ซื้อสำเร็จ · คูปองอยู่กับผู้ซื้อ · listing SOLD
-- T30 · รายการเดียวขายซ้ำสองครั้ง → ชน uq_purchase_listing
-- =====================================================================
SELECT pg_temp.rec_checks('T28', 'บันทึกยอดถือคูปองโดยอ้าง event ที่ไม่มี → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO coupon_holding (id, coupon_type_id, holder_kind, holder_id, quantity, chain_event_id,
                                                    as_of_block, as_of_time)
                        VALUES ('acc_hold_x', 'acc_ct_1', 'CUSTOMER', 'acc_cus_two', '1', 'acc_ce_ไม่มีจริง', 1, now())$q$,
    '23503', 'coupon_holding', ARRAY['chain_event_id']));

DO $$
DECLARE n int; v text;
BEGIN
  SELECT count(*) INTO n FROM coupon_code
   WHERE id IN ('acc_cc_1', 'acc_cc_2') AND holder_kind = 'CUSTOMER' AND holder_id = 'acc_cus_rich';
  SELECT status INTO v FROM listing WHERE id = 'acc_lst_a';
  PERFORM pg_temp.rec('T29', 'ซื้อสำเร็จ · คูปอง 2 ใบอยู่กับผู้ซื้อ · listing SOLD',
                      CASE WHEN n = 2 AND v = 'SOLD' THEN 'PASS' ELSE 'FAIL' END, format('%s ใบ · %s', n, v));
END $$;

SELECT pg_temp.rec_checks('T30', 'ขายรายการเดิมซ้ำ → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.purchase('acc_pur_dup', 'acc_lst_a', 'acc_cus_rich', 'acc_opb_a', pg_temp.thb(80), 2,
                     pg_temp.thb(160), 250, pg_temp.thb(4), pg_temp.thb(156)),
    '23505', 'purchase', ARRAY['listing_id'], ARRAY['uq_purchase_listing']));

-- =====================================================================
-- T31 · tx revert · ปล่อยเงินที่จองคืน · ยอดไม่เปลี่ยน · ยอดใช้ได้กลับมา (500 − 200 = 300)
-- T32 · ปล่อยจองขณะ operation ยังไม่จบ → ถูกปฏิเสธ
-- =====================================================================
DO $$
DECLARE r text[]; v_before text; v_after text; v_avail numeric;
BEGIN
  SELECT amount INTO v_before FROM thb_balance WHERE account_id = 'CUSTOMER_THB:acc_cus_x';
  r[1] := pg_temp.try_ok($q$
    UPDATE operation SET status = 'SUBMITTED' WHERE id = 'acc_opb_x2' AND status = 'PENDING';
    UPDATE operation SET status = 'CHAIN_REVERTED', error_note = 'buyCoupon reverted' WHERE id = 'acc_opb_x2';
    UPDATE reservation SET released_at = now() WHERE id = 'acc_rsv_x2';
  $q$);
  SELECT amount INTO v_after FROM thb_balance WHERE account_id = 'CUSTOMER_THB:acc_cus_x';
  BEGIN
    v_avail := f_available_thb('CUSTOMER_THB:acc_cus_x');
  EXCEPTION WHEN OTHERS THEN r[2] := 'ERROR: ' || SQLERRM;
  END;
  IF v_before IS DISTINCT FROM v_after OR v_avail IS DISTINCT FROM 300000000000000000000 THEN
    r[3] := format('FAIL: ยอด %s → %s · ใช้ได้ %s', v_before, v_after, v_avail);
  END IF;
  PERFORM pg_temp.rec_checks('T31', 'tx revert · ปล่อยจองคืน · ยอดไม่เปลี่ยน ยอดใช้ได้กลับเป็น 300', VARIADIC r);
END $$;

SELECT pg_temp.rec_checks('T32', 'ปล่อยจองขณะ operation ยัง PENDING → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE reservation SET released_at = now() WHERE id = 'acc_rsv_x1'$q$,
    '23514', 'reservation', NULL, ARRAY['trg_reservation_release_timing']));

-- =====================================================================
-- T33 · บัญชีที่ไม่ใช่ ISSUANCE ติดลบไม่ได้ (escrow ด้วย)
-- T34 · outbox topic ต้องมีเวอร์ชัน
-- T35 · อ่าน log ช่วงเดิมซ้ำได้                              RFD 5 D2
-- =====================================================================
SELECT pg_temp.rec_checks('T33', 'ยอดลูกค้าหรือ escrow ติดลบ → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO thb_balance (account, account_id, amount)
                        VALUES ('CUSTOMER_THB', 'CUSTOMER_THB:acc_cus_neg', '-1')$q$,
    '23514', 'thb_balance', NULL, ARRAY['ck_thb_balance_sign']),
  pg_temp.try_reject($q$INSERT INTO thb_balance (account, account_id, amount)
                        VALUES ('ESCROW', 'ESCROW:acc_neg', '-1')$q$,
    '23514', 'thb_balance', NULL, ARRAY['ck_thb_balance_sign']));

SELECT pg_temp.rec_checks('T34', 'outbox topic ไม่มี .vN → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO outbox (id, operation_id, topic, payload)
                        VALUES ('acc_obx_bad', 'acc_opb_a', 'listing.buy', '{}')$q$,
    '23514', 'outbox', NULL, ARRAY['ck_outbox_topic_version']));

DO $$
DECLARE n1 int; n2 int;
BEGIN
  INSERT INTO chain_event (id, block_number, block_hash, tx_hash, log_index, address, topic0, topics, data, block_timestamp)
  VALUES ('acc_ce_1', 900000004, '0x' || repeat('ba', 32), '0x' || repeat('ea', 32), 0,
          '0x' || repeat('da', 20), '0x' || repeat('00', 32), '[]', '\x00', now())
  ON CONFLICT (tx_hash, log_index) DO NOTHING;
  GET DIAGNOSTICS n1 = ROW_COUNT;
  INSERT INTO chain_event (id, block_number, block_hash, tx_hash, log_index, address, topic0, topics, data, block_timestamp)
  VALUES ('acc_ce_2', 900000004, '0x' || repeat('ba', 32), '0x' || repeat('ea', 32), 0,
          '0x' || repeat('da', 20), '0x' || repeat('00', 32), '[]', '\x00', now())
  ON CONFLICT (tx_hash, log_index) DO NOTHING;
  GET DIAGNOSTICS n2 = ROW_COUNT;
  PERFORM pg_temp.rec('T35', 'อ่าน log ช่วงเดิมซ้ำ → chain_event ไม่เกิดแถวซ้ำ',
                      CASE WHEN n1 = 1 AND n2 = 0 THEN 'PASS' ELSE 'FAIL' END, format('ได้ %s · %s', n1, n2));
END $$;

-- =====================================================================
-- T36 · ปิดท้าย · ตรวจตอน commit ทั้งหมดผ่าน · ledger รวม 0 · ทุกยอดตรงกับ ledger ของตัวเอง (ทั้งฐาน)
-- =====================================================================
DO $$
DECLARE r text; v_sum numeric; v_bad text;
BEGIN
  r := pg_temp.try_ok('SET CONSTRAINTS ALL IMMEDIATE');
  SELECT coalesce(sum(amount::numeric), 0) INTO v_sum FROM ledger_entry;
  SELECT string_agg(b.account_id, ',') INTO v_bad FROM thb_balance b
   WHERE b.amount::numeric <> coalesce((SELECT sum(l.amount::numeric) FROM ledger_entry l
                                         WHERE l.account = b.account AND l.account_id = b.account_id), 0);
  PERFORM pg_temp.rec_checks('T36', 'ปิดท้าย · ตรวจตอน commit ผ่าน · ledger รวม 0 · ทุกยอดตรงกับ ledger', r,
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
