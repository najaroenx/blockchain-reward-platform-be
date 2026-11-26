-- =====================================================================
-- FEAT-11 · Redeem Coupon · acceptance สำหรับ blockchain-reward-platform-be
--
-- ต้นฉบับ : blockchain-reward-platform/reference/feat-11/tests/acceptance.sql (12 ข้อ)
-- schema  : migrations 0002 → 0014_feat11_redeem
--
-- รัน (Postgres ใน docker):
--   docker exec -i blockchain-reward-platform-be-postgres-1 \
--     psql -U reward -d reward < tests/sql/feat11_acceptance.sql
--
-- ผล  PASS ฐานข้อมูลบังคับกฎนี้ได้จริง · FAIL ไม่ได้ หรือถูกปฏิเสธด้วยกฎตัวอื่น
--     ERROR test เองรันไม่ได้ · ดูหมายเหตุ
--
-- ต่างจากต้นฉบับ · หลักฐานอยู่ใน docs/reference-findings.md
--   1. contract จริงใช้คูปองด้วย Coupon.redeem → Marketplace.onCouponRedeemed → Vault ปล่อยเงิน
--      ใน tx เดียว · ไม่มี burnCoupon → releaseFundsPartial สอง step · T4 T5 ใช้ step redeem
--   2. Vault มี escrow ต่อการซื้อหนึ่งครั้ง · T9 จึงห้าม escrowId ของ Vault ซ้ำ ไม่ใช่ห้าม
--      escrow active สองก้อนบนประเภทเดียว (T9b ยืนยันว่าสองก้อนอยู่ร่วมกันได้)
--   3. T11 เป็นกฎตอน commit ไม่ใช่แค่ตรวจข้อมูล · นับต่อ (ผู้ซื้อ, ประเภท) เพราะ Vault
--      ปล่อยเงินจาก escrow ของผู้ถือตามลำดับของมันเอง ไม่ได้ผูกกับรหัสแต่ละใบ
--   4. เพิ่ม T13 สิทธิ์ใช้คูปอง · T14 REDEEMED มาจาก HELD และย้อนไม่ได้ · T15 escrow ตรงกับ purchase
--      T16 escrow เดินทางเดียว · T17 escrow ตรงกับ ledger ESCROW:<purchase> · T18 ปิดเมื่อหมดเงินพอดี
--   5. T3 ปล่อยจองหลัง operation จบ (กฎของ FEAT-05) · T7 ค่าสูงสุดใช้ chain_escrow_id
--   6. เงินเข้าระบบผ่านการฝากจริง (webhook → การฝากที่ยืนยัน → thb_mint) ตาม FEAT-12 (0015)
--   7. ข้อมูลทดสอบขึ้นต้นด้วย acc_ · ข้อ "ต้องถูกปฏิเสธ" ตรวจว่าชนกฎที่ตั้งใจจริง
--
-- ทั้งไฟล์อยู่ใน transaction เดียวแล้ว ROLLBACK · รันซ้ำกี่รอบก็ได้
-- =====================================================================
\set ON_ERROR_STOP on
\pset format aligned

BEGIN;

-- ควรได้ 0014_feat11_redeem ขึ้นไป
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


CREATE FUNCTION pg_temp.thb(p numeric) RETURNS text LANGUAGE sql IMMUTABLE AS
$$ SELECT (p * 1000000000000000000)::numeric(78,0)::text $$;

CREATE FUNCTION pg_temp.has_col(p_table text, p_col text) RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT EXISTS (SELECT 1 FROM information_schema.columns
                  WHERE table_schema = current_schema() AND table_name = p_table AND column_name = p_col)
$$;

-- ------------------------------------------------------------ fixture
INSERT INTO merchant (id, name, status, activated_at) VALUES ('acc_mch_a', 'ร้านผู้ขาย', 'ACTIVE', now());
INSERT INTO signer (address, key_ref) VALUES ('0x' || repeat('3e', 20), 'kms/acc-signer-1');
INSERT INTO wallet (id, address, key_ref, owner_kind, owner_id) VALUES
  ('acc_wal_s',  '0x' || repeat('e1', 20), 'kms/acc-seller', 'MERCHANT', 'acc_mch_a'),
  ('acc_wal_b1', '0x' || repeat('e2', 20), 'kms/acc-buyer1', 'CUSTOMER', 'acc_cus_1'),
  ('acc_wal_b2', '0x' || repeat('e3', 20), 'kms/acc-buyer2', 'CUSTOMER', 'acc_cus_2');
INSERT INTO fee_schedule (id, bps, effective_from, note) VALUES ('acc_fee_0', 0, now() - interval '1 hour', 'ทดสอบ');

INSERT INTO coupon_type (id, merchant_id, name, name_normalized, face_value, total_supply, status,
                         expires_at, live_at, created_at, starts_at) VALUES
  ('acc_ct_1', 'acc_mch_a', 'คูปอง 100 บาท', 'คูปอง 100 บาท', pg_temp.thb(100), 100, 'LIVE',
   now() + interval '180 days', now(), now(), now() - interval '1 day'),
  ('acc_ct_old', 'acc_mch_a', 'คูปองหมดอายุ', 'คูปองหมดอายุ', pg_temp.thb(10), 10, 'LIVE',
   now() - interval '1 day', now() - interval '20 days', now() - interval '30 days', now() - interval '30 days');
INSERT INTO coupon_type_chain (coupon_type_id, contract_address, chain_type_id, deployed_block, deployed_tx_hash) VALUES
  ('acc_ct_1',   '0x' || repeat('cb', 20), '7', 1001, '0x' || repeat('c1', 32)),
  ('acc_ct_old', '0x' || repeat('cb', 20), '8', 1001, '0x' || repeat('c2', 32));

INSERT INTO coupon_code (id, coupon_type_id, code_hash, code_ref, state, holder_kind, holder_id, held_at)
SELECT 'acc_cc_' || g, 'acc_ct_1', pg_temp.h('CODE-' || g), pg_temp.h('acc_cc_' || g),
       'HELD', 'MERCHANT', 'acc_mch_a', now()
  FROM generate_series(1, 5) g;
INSERT INTO coupon_code (id, coupon_type_id, code_hash, code_ref, state) VALUES
  ('acc_cc_6', 'acc_ct_1', pg_temp.h('CODE-6'), pg_temp.h('acc_cc_6'), 'ISSUED');
INSERT INTO coupon_code (id, coupon_type_id, code_hash, code_ref, state, holder_kind, holder_id, held_at) VALUES
  ('acc_cc_old', 'acc_ct_old', pg_temp.h('CODE-OLD'), pg_temp.h('acc_cc_old'), 'HELD', 'CUSTOMER', 'acc_cus_1', now());

-- THB เข้าระบบผ่านการฝากจริง · webhook ที่ลงนาม → การฝากที่ยืนยัน → thb_mint (FEAT-12 · 0015)
CREATE FUNCTION pg_temp.deposit(p_id text, p_amount text) RETURNS void LANGUAGE plpgsql AS $$
DECLARE v_op text := 'acc_opm_' || p_id; v_dep text := 'acc_dep_' || p_id;
BEGIN
  INSERT INTO psp_webhook (id, provider, provider_event_id, event_type, raw_body, signature, signature_ok)
  VALUES ('acc_wh_' || p_id, 'acc_psp', 'evt_' || p_id, 'charge.complete', '\x00', 'sig', true);
  INSERT INTO thb_deposit (id, holder_kind, holder_id, provider, provider_ref, amount_satang, amount_base, expires_at)
  VALUES (v_dep, 'CUSTOMER', p_id, 'acc_psp', 'ref_' || p_id, (p_amount::numeric / 10000000000000000)::bigint,
          p_amount, now() + interval '1 day');
  UPDATE thb_deposit SET status = 'CONFIRMED', webhook_id = 'acc_wh_' || p_id, confirmed_at = now() WHERE id = v_dep;
  INSERT INTO operation (id, actor_scope, kind, subject_id, payload) VALUES (v_op, 'system:minter', 'THB_MINT', v_dep, '{}');
  INSERT INTO outbox (id, operation_id, topic, payload) VALUES (v_op || '_o', v_op, 'thb.mint.v1', '{}');
  INSERT INTO thb_mint (deposit_id, operation_id, amount_base, to_account, to_account_id)
  VALUES (v_dep, v_op, p_amount, 'CUSTOMER_THB', 'CUSTOMER_THB:' || p_id);
  INSERT INTO ledger_entry (id, operation_id, entry_seq, account, account_id, amount) VALUES
    (v_op || '_a', v_op, 1, 'CUSTOMER_THB', 'CUSTOMER_THB:' || p_id, p_amount),
    (v_op || '_b', v_op, 2, 'ISSUANCE', 'ISSUANCE:acc_' || p_id, '-' || p_amount);
  INSERT INTO thb_balance (account, account_id, amount) VALUES
    ('CUSTOMER_THB', 'CUSTOMER_THB:' || p_id, p_amount),
    ('ISSUANCE', 'ISSUANCE:acc_' || p_id, '-' || p_amount);
  UPDATE operation SET status = 'SUBMITTED' WHERE id = v_op AND status = 'PENDING';
  UPDATE operation SET status = 'CONFIRMED' WHERE id = v_op AND status = 'SUBMITTED';
  UPDATE operation SET status = 'FINALIZED', finalized_at = now() WHERE id = v_op;
  UPDATE thb_deposit SET status = 'MINTED', minted_at = now() WHERE id = v_dep;
END $$;
SELECT pg_temp.deposit('acc_cus_1', pg_temp.thb(1000));
SELECT pg_temp.deposit('acc_cus_2', pg_temp.thb(1000));

-- รายการขายที่ ACTIVE
CREATE FUNCTION pg_temp.mk_active(p_lst text, p_codes text[], p_chain_id text,
                                  p_seller_kind text DEFAULT 'MERCHANT', p_seller text DEFAULT 'acc_mch_a')
RETURNS void LANGUAGE plpgsql AS $$
DECLARE c text; i int := 0;
BEGIN
  INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
  VALUES ('acc_opc_' || p_lst, lower(p_seller_kind) || ':' || p_seller, 'LISTING_CREATE', p_lst, '{}');
  UPDATE operation SET status = 'SUBMITTED' WHERE id = 'acc_opc_' || p_lst;
  UPDATE operation SET status = 'CONFIRMED' WHERE id = 'acc_opc_' || p_lst;
  UPDATE operation SET status = 'FINALIZED', finalized_at = now() WHERE id = 'acc_opc_' || p_lst;
  INSERT INTO outbox (id, operation_id, topic, payload)
  VALUES ('acc_obc_' || p_lst, 'acc_opc_' || p_lst, 'listing.create.v1', '{}');
  INSERT INTO listing (id, seller_kind, seller_id, coupon_type_id, unit_price, quantity)
  VALUES (p_lst, p_seller_kind, p_seller, 'acc_ct_1', pg_temp.thb(80), array_length(p_codes, 1));
  FOREACH c IN ARRAY p_codes LOOP
    i := i + 1;
    INSERT INTO listing_item (id, listing_id, coupon_code_id) VALUES (p_lst || '_i' || i, p_lst, c);
  END LOOP;
  INSERT INTO listing_chain (listing_id, contract_address, chain_listing_id, created_block, created_tx_hash)
  VALUES (p_lst, '0x' || repeat('db', 20), p_chain_id, 1000, pg_temp.h(p_lst));
  UPDATE listing SET status = 'ACTIVE', active_at = now() WHERE id = p_lst;
END $$;

-- ซื้อสำเร็จ (ผลของ FEAT-10) · THB ผู้ซื้อ → ESCROW:<purchase> · Vault escrow · คูปองไปอยู่กับผู้ซื้อ
CREATE FUNCTION pg_temp.bought(p_pur text, p_lst text, p_buyer text, p_escrow_chain_id text)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
  v_op text := 'acc_opb_' || p_pur;
  v_qty int; v_total text;
  v_cols text := 'id, chain_token_id, contract_address, seller_wallet_id, buyer_wallet_id, '
                 'total_amount, remaining_amount, coupons_locked';
  v_vals text;
BEGIN
  SELECT quantity, (unit_price::numeric * quantity)::text INTO v_qty, v_total FROM listing WHERE id = p_lst;
  INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
  VALUES (v_op, 'customer:' || p_buyer, 'LISTING_BUY', p_lst, '{}');
  INSERT INTO outbox (id, operation_id, topic, payload) VALUES (v_op || '_o', v_op, 'listing.buy.v1', '{}');
  INSERT INTO purchase (id, listing_id, buyer_kind, buyer_id, seller_kind, seller_id, unit_price, quantity,
                        total_price, fee_bps, fee_amount, net_to_seller, operation_id)
  VALUES (p_pur, p_lst, 'CUSTOMER', p_buyer, 'MERCHANT', 'acc_mch_a', pg_temp.thb(80), v_qty,
          v_total, 0, '0', v_total, v_op);
  INSERT INTO ledger_entry (id, operation_id, entry_seq, account, account_id, amount) VALUES
    (v_op || '_l1', v_op, 1, 'CUSTOMER_THB', 'CUSTOMER_THB:' || p_buyer, '-' || v_total),
    (v_op || '_l2', v_op, 2, 'ESCROW', 'ESCROW:' || p_pur, v_total);
  UPDATE thb_balance SET amount = (amount::numeric - v_total::numeric)::text
   WHERE account_id = 'CUSTOMER_THB:' || p_buyer;
  INSERT INTO thb_balance (account, account_id, amount) VALUES ('ESCROW', 'ESCROW:' || p_pur, v_total);
  v_vals := format('%L, %L, %L, %L, %L, %L, %L, %s', 'acc_esc_' || p_pur, '7', '0x' || repeat('fa', 20),
                   'acc_wal_s', CASE p_buyer WHEN 'acc_cus_1' THEN 'acc_wal_b1' ELSE 'acc_wal_b2' END,
                   v_total, v_total, v_qty);
  IF pg_temp.has_col('escrow', 'purchase_id') THEN
    v_cols := v_cols || ', chain_escrow_id, purchase_id';
    v_vals := v_vals || format(', %L, %L', p_escrow_chain_id, p_pur);
  END IF;
  EXECUTE format('INSERT INTO escrow (%s) VALUES (%s)', v_cols, v_vals);
  UPDATE operation SET status = 'SUBMITTED' WHERE id = v_op AND status = 'PENDING';
  UPDATE operation SET status = 'CONFIRMED' WHERE id = v_op AND status = 'SUBMITTED';
  UPDATE operation SET status = 'FINALIZED', finalized_at = now() WHERE id = v_op;
  UPDATE purchase SET status = 'SETTLED', settled_at = now() WHERE id = p_pur;
  UPDATE listing SET status = 'SOLD', closed_at = now() WHERE id = p_lst;
  UPDATE coupon_code SET holder_kind = 'CUSTOMER', holder_id = p_buyer
   WHERE id IN (SELECT coupon_code_id FROM listing_item WHERE listing_id = p_lst);
END $$;

SELECT pg_temp.mk_active('acc_lst_1', ARRAY['acc_cc_1', 'acc_cc_2'], '9201');
SELECT pg_temp.bought('acc_pur_1', 'acc_lst_1', 'acc_cus_1', '501');
SELECT pg_temp.mk_active('acc_lst_2', ARRAY['acc_cc_3'], '9202');
SELECT pg_temp.bought('acc_pur_2', 'acc_lst_2', 'acc_cus_2', '115792089237316195423570985008687907853269984665640564039457584007913129639935');
-- ลูกค้า 1 เอา acc_cc_2 ไปลงขายต่อ · ใบนี้ใช้ไม่ได้จนกว่าจะถอด
SELECT pg_temp.mk_active('acc_lst_r', ARRAY['acc_cc_2'], '9203', 'CUSTOMER', 'acc_cus_1');
-- รายการที่ยังไม่มีคนซื้อ · ใช้กับ purchase ที่ยังไม่มี escrow
SELECT pg_temp.mk_active('acc_lst_3', ARRAY['acc_cc_4'], '9204');

CREATE FUNCTION pg_temp.redeem(p_op text, p_scope text, p_code text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT format($q$INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
                   VALUES (%L, %L, 'COUPON_REDEEM', %L, '{}');
                   INSERT INTO outbox (id, operation_id, topic, payload)
                   VALUES (%L, %L, 'coupon.redeem.v1', '{}')$q$, p_op, p_scope, p_code, p_op || '_o', p_op)
$$;

-- =====================================================================
-- T1 · Idempotency-Key ซ้ำ ต้องไม่สร้างงานใหม่            RFD 3 R1
-- =====================================================================
DO $$
DECLARE n1 int; n2 int;
BEGIN
  INSERT INTO idempotency_key (id, scope, key, fingerprint, expires_at)
  VALUES ('acc_ik_1', 'customer:acc_cus_1', '11111111-2222-3333-4444-555555555555', pg_temp.h('body'), now() + interval '1 day')
  ON CONFLICT (scope, key) DO NOTHING;
  GET DIAGNOSTICS n1 = ROW_COUNT;
  INSERT INTO idempotency_key (id, scope, key, fingerprint, expires_at)
  VALUES ('acc_ik_2', 'customer:acc_cus_1', '11111111-2222-3333-4444-555555555555', pg_temp.h('body'), now() + interval '1 day')
  ON CONFLICT (scope, key) DO NOTHING;
  GET DIAGNOSTICS n2 = ROW_COUNT;
  PERFORM pg_temp.rec('T1', 'ส่ง Idempotency-Key เดิมซ้ำ → แถวที่สองได้ 0 rows ไม่ throw',
                      CASE WHEN n1 = 1 AND n2 = 0 THEN 'PASS' ELSE 'FAIL' END, format('ได้ %s · %s', n1, n2));
END $$;

-- =====================================================================
-- T13 · สิทธิ์ใช้คูปอง · ผู้ถือจริง · ไม่หมดอายุ · ไม่ได้ลงขายอยู่ · ระบุใบ
-- =====================================================================
SELECT pg_temp.rec_checks('T13', 'ใช้คูปองของคนอื่น / ใบที่ลงขายอยู่ / หมดอายุ / ไม่ระบุใบ / ไม่มีอยู่ → ถูกปฏิเสธ · ของตัวเองได้',
  pg_temp.try_reject(pg_temp.redeem('acc_opr_x1', 'customer:acc_cus_2', 'acc_cc_1'),
    '23514', 'operation', NULL, ARRAY['trg_redeem_admissible']),
  pg_temp.try_reject(pg_temp.redeem('acc_opr_x2', 'customer:acc_cus_1', 'acc_cc_2'),
    '23514', 'operation', NULL, ARRAY['trg_redeem_admissible']),
  pg_temp.try_reject(pg_temp.redeem('acc_opr_x3', 'customer:acc_cus_1', 'acc_cc_old'),
    '23514', 'operation', NULL, ARRAY['trg_redeem_admissible']),
  pg_temp.try_reject(pg_temp.redeem('acc_opr_x4', 'customer:acc_cus_1', NULL),
    '23514', 'operation', NULL, ARRAY['trg_redeem_admissible']),
  pg_temp.try_reject(pg_temp.redeem('acc_opr_x5', 'customer:acc_cus_1', 'acc_cc_ไม่มีจริง'),
    '23514', 'operation', NULL, ARRAY['trg_redeem_admissible']),
  pg_temp.try_ok(pg_temp.redeem('acc_opr_1', 'customer:acc_cus_1', 'acc_cc_1')));

-- =====================================================================
-- T2 · จอง coupon ใบเดิมซ้อนกันไม่ได้                      RFD 11 D4
-- T3 · งานแรกจบแล้วปล่อยจอง · จองใหม่ได้ · ประวัติแถวเดิมยังอยู่
-- =====================================================================
INSERT INTO reservation (id, operation_id, subject_type, subject_id, amount)
VALUES ('acc_rsv_1', 'acc_opr_1', 'COUPON_UNIT', 'acc_cc_1', '1');
SELECT pg_temp.try_ok(pg_temp.redeem('acc_opr_2', 'customer:acc_cus_1', 'acc_cc_1'));

SELECT pg_temp.rec_checks('T2', 'จอง coupon ใบเดียวกันสองครั้ง → ครั้งที่สองถูกฐานข้อมูลปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO reservation (id, operation_id, subject_type, subject_id, amount)
                        VALUES ('acc_rsv_2', 'acc_opr_2', 'COUPON_UNIT', 'acc_cc_1', '1')$q$,
    '23505', 'reservation', ARRAY['subject_id', 'subject_type']));

DO $$
DECLARE r text[]; n int;
BEGIN
  r[1] := pg_temp.try_ok($q$
    UPDATE operation SET status = 'SUBMITTED' WHERE id = 'acc_opr_1' AND status = 'PENDING';
    UPDATE operation SET status = 'CHAIN_REVERTED' WHERE id = 'acc_opr_1';
    UPDATE reservation SET released_at = now() WHERE id = 'acc_rsv_1';
    INSERT INTO reservation (id, operation_id, subject_type, subject_id, amount)
    VALUES ('acc_rsv_3', 'acc_opr_2', 'COUPON_UNIT', 'acc_cc_1', '1');
  $q$);
  SELECT count(*) INTO n FROM reservation WHERE subject_id = 'acc_cc_1' AND subject_type = 'COUPON_UNIT';
  IF n <> 2 THEN r[2] := format('FAIL: มี %s แถว ไม่ใช่ 2', n); END IF;
  PERFORM pg_temp.rec_checks('T3', 'งานแรกจบแล้วปล่อยจอง · จองใหม่ได้ · ประวัติแถวเดิมยังอยู่', VARIADIC r);
END $$;

-- =====================================================================
-- T4 · ห้าม broadcast ก่อน raw_tx ลงตาราง                   RFD 2 R2
-- T5 · tx_hash เดียวกันเกิดสองแถวไม่ได้                     RFD 2 D4
-- T6 · re-index ช่วงเดิมซ้ำได้                              RFD 5 D2
-- =====================================================================
INSERT INTO operation_step (id, operation_id, step_index, name) VALUES ('acc_st_1', 'acc_opr_2', 1, 'redeem');

SELECT pg_temp.rec_checks('T4', 'เปลี่ยน step เป็น SUBMITTED โดยไม่มี raw_tx → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE operation_step SET status = 'SUBMITTED' WHERE id = 'acc_st_1'$q$,
    '23514', 'operation_step', NULL, ARRAY['ck_rawtx_before_broadcast']));

UPDATE operation_step
   SET raw_tx = '\x02f8', signer_address = '0x' || repeat('3e', 20), nonce = 41,
       status = 'SUBMITTED', tx_hash = '0x' || repeat('ab', 32)
 WHERE id = 'acc_st_1';

SELECT pg_temp.rec_checks('T5', 'ส่ง raw_tx ไบต์เดิมซ้ำ → tx_hash ซ้ำถูกปฏิเสธ ไม่เกิดแถวซ้ำ',
  pg_temp.try_reject($q$INSERT INTO operation_step (id, operation_id, step_index, name, raw_tx, status, tx_hash)
                        VALUES ('acc_st_2', 'acc_opr_1', 1, 'redeem', '\x02f8', 'SUBMITTED', '0x' || repeat('ab', 32))$q$,
    '23505', 'operation_step', ARRAY['tx_hash']));

INSERT INTO raw_block (block_hash, block_number, parent_hash, "timestamp")
VALUES ('0x' || repeat('bb', 32), 900000005, '0x' || repeat('b6', 32), now());
-- FEAT-14 · บล็อกที่อ่านแล้วต้องอยู่ใต้ cursor ตอน commit (0017)
INSERT INTO indexer_cursor (id, last_block, last_block_hash)
VALUES ('main', 900000005, '0x' || repeat('bb', 32));
DO $$
DECLARE n1 int; n2 int;
BEGIN
  INSERT INTO chain_event (id, block_number, block_hash, tx_hash, log_index, address, topic0, topics, data, block_timestamp)
  VALUES ('acc_ce_1', 900000005, '0x' || repeat('bb', 32), '0x' || repeat('ab', 32), 0,
          '0x' || repeat('cb', 20), '0x' || repeat('00', 32), '[]', '\x00', now())
  ON CONFLICT (tx_hash, log_index) DO NOTHING;
  GET DIAGNOSTICS n1 = ROW_COUNT;
  INSERT INTO chain_event (id, block_number, block_hash, tx_hash, log_index, address, topic0, topics, data, block_timestamp)
  VALUES ('acc_ce_2', 900000005, '0x' || repeat('bb', 32), '0x' || repeat('ab', 32), 0,
          '0x' || repeat('cb', 20), '0x' || repeat('00', 32), '[]', '\x00', now())
  ON CONFLICT (tx_hash, log_index) DO NOTHING;
  GET DIAGNOSTICS n2 = ROW_COUNT;
  PERFORM pg_temp.rec('T6', 'อ่าน log ช่วงเดิมซ้ำ → chain_event ไม่เกิดแถวซ้ำ',
                      CASE WHEN n1 = 1 AND n2 = 0 THEN 'PASS' ELSE 'FAIL' END, format('ได้ %s · %s', n1, n2));
END $$;

-- purchase ที่ยังไม่มี escrow (คำขอซื้อรายการ 3 ยังไม่จบ)
INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
VALUES ('acc_opb_acc_pur_3', 'customer:acc_cus_2', 'LISTING_BUY', 'acc_lst_3', '{}');
INSERT INTO outbox (id, operation_id, topic, payload) VALUES ('acc_obb_3', 'acc_opb_acc_pur_3', 'listing.buy.v1', '{}');
INSERT INTO purchase (id, listing_id, buyer_kind, buyer_id, seller_kind, seller_id, unit_price, quantity,
                      total_price, fee_bps, fee_amount, net_to_seller, operation_id)
VALUES ('acc_pur_3', 'acc_lst_3', 'CUSTOMER', 'acc_cus_2', 'MERCHANT', 'acc_mch_a', pg_temp.thb(80), 1,
        pg_temp.thb(80), 0, '0', pg_temp.thb(80), 'acc_opb_acc_pur_3');

-- =====================================================================
-- T7 · เก็บ uint256 สูงสุดได้ครบทุกหลัก (escrowId ของ Vault)   RFD 14 D1
-- T8 · ยอด escrow ติดลบ / เกินยอดที่ล็อก → ถูกปฏิเสธ
-- =====================================================================
DO $$
DECLARE v text;
BEGIN
  EXECUTE 'SELECT chain_escrow_id FROM escrow WHERE id = ''acc_esc_acc_pur_2''' INTO v;
  PERFORM pg_temp.rec('T7', 'เก็บ escrowId = 2^256-1 ได้ครบทุกหลัก',
    CASE WHEN v = (2::numeric ^ 256 - 1)::text THEN 'PASS' ELSE 'FAIL' END, coalesce(left(v, 20), 'NULL'));
EXCEPTION WHEN OTHERS THEN
  PERFORM pg_temp.rec('T7', 'เก็บ escrowId = 2^256-1 ได้ครบทุกหลัก', 'ERROR', SQLSTATE || ' · ' || SQLERRM);
END $$;

SELECT pg_temp.rec_checks('T8', 'ยอดคงเหลือ escrow ติดลบ หรือมากกว่ายอดที่ล็อก → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE escrow SET remaining_amount = '-1' WHERE id = 'acc_esc_acc_pur_1'$q$,
    '23514', 'escrow', NULL, ARRAY['ck_escrow_remaining_uint256']),
  pg_temp.try_reject($q$INSERT INTO escrow (id, chain_token_id, contract_address, seller_wallet_id, buyer_wallet_id,
                                            total_amount, remaining_amount, coupons_locked, chain_escrow_id, purchase_id)
                        VALUES ('acc_esc_big', '7', '0x' || repeat('fa', 20), 'acc_wal_s', 'acc_wal_b2',
                                '80000000000000000000', '80000000000000000001', 1, '777', 'acc_pur_3')$q$,
    '23514', 'escrow', NULL, ARRAY['ck_escrow_remaining_le_total']));

-- =====================================================================
-- T9  · escrowId ของ Vault เดียวกันผูกกับสองแถวไม่ได้ · purchase หนึ่งมี escrow เดียว
-- T9b · escrow ที่ active สองก้อนบนคูปองประเภทเดียวกัน (คนละการซื้อ) อยู่ร่วมกันได้
-- =====================================================================
CREATE FUNCTION pg_temp.escrow3(p_id text, p_chain_id text, p_purchase text DEFAULT 'acc_pur_3',
                                p_total text DEFAULT '80000000000000000000', p_buyer_wallet text DEFAULT 'acc_wal_b2')
RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT format($q$INSERT INTO escrow (id, chain_token_id, contract_address, seller_wallet_id, buyer_wallet_id,
                                       total_amount, remaining_amount, coupons_locked, chain_escrow_id, purchase_id)
                   VALUES (%L, '7', '0x' || repeat('fa', 20), 'acc_wal_s', %L, %L, %L, 1, %L, %L)$q$,
                p_id, p_buyer_wallet, p_total, p_total, p_chain_id, p_purchase)
$$;

SELECT pg_temp.rec_checks('T9', 'escrowId ของ Vault ซ้ำ หรือ escrow ที่สองของ purchase เดิม → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.escrow3('acc_esc_dup', '501'),
    '23505', 'escrow', ARRAY['chain_escrow_id', 'contract_address']),
  pg_temp.try_reject(pg_temp.escrow3('acc_esc_dup2', '502', 'acc_pur_2'),
    '23505', 'escrow', ARRAY['purchase_id']));

DO $$
DECLARE n int;
BEGIN
  SELECT count(*) INTO n FROM escrow WHERE id IN ('acc_esc_acc_pur_1', 'acc_esc_acc_pur_2') AND active;
  PERFORM pg_temp.rec('T9b', 'escrow active สองก้อนบนคูปองประเภทเดียวกัน อยู่ร่วมกันได้',
                      CASE WHEN n = 2 THEN 'PASS' ELSE 'FAIL' END, format('%s ก้อน', n));
END $$;

-- =====================================================================
-- T10 · invariant · operation ทุกแถวต้องมี outbox คู่กัน (ทั้งฐาน)   RFD 2 D1
-- =====================================================================
DO $$
DECLARE bad text;
BEGIN
  SELECT string_agg(o.id, ',') INTO bad FROM operation o LEFT JOIN outbox b ON b.operation_id = o.id WHERE b.id IS NULL;
  PERFORM pg_temp.rec('T10', 'ไม่มี operation ที่ไม่มีใครมาทำ (orphan = 0)',
                      CASE WHEN bad IS NULL THEN 'PASS' ELSE 'FAIL' END, bad);
END $$;

-- =====================================================================
-- T15 · escrow ต้องตรงกับ purchase · และสิ่งที่ล็อกไว้แก้ไม่ได้
-- T16 · escrow เดินทางเดียว · คงเหลือลดอย่างเดียว · ใช้ไปเพิ่มอย่างเดียว · ปิดแล้วเปิดไม่ได้
-- T18 · ปิดเมื่อเงินหมดพอดี · ปิดทั้งที่ยังมีเงิน หรือเงินหมดแต่ยังเปิด → ถูกปฏิเสธ
-- =====================================================================
SELECT pg_temp.rec_checks('T15', 'escrow ยอดไม่ตรง / ผู้ซื้อไม่ตรง purchase / แก้ยอดที่ล็อก → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.escrow3('acc_esc_3a', '503', 'acc_pur_3', '70000000000000000000'),
    '23514', 'escrow', NULL, ARRAY['trg_escrow_terms']),
  pg_temp.try_reject(pg_temp.escrow3('acc_esc_3b', '503', 'acc_pur_3', '80000000000000000000', 'acc_wal_b1'),
    '23514', 'escrow', NULL, ARRAY['trg_escrow_terms']),
  pg_temp.try_reject($q$UPDATE escrow SET total_amount = '1' WHERE id = 'acc_esc_acc_pur_1'$q$,
    '23514', 'escrow', NULL, ARRAY['trg_escrow_terms']));

SELECT pg_temp.rec_checks('T16', 'คงเหลือเพิ่ม / จำนวนที่ใช้ลด / เปิด escrow ที่ปิดแล้ว → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE escrow SET remaining_amount = '200000000000000000000' WHERE id = 'acc_esc_acc_pur_1'$q$,
    '23514', 'escrow', NULL, ARRAY['trg_escrow_progress']),
  pg_temp.try_reject($q$UPDATE escrow SET coupons_redeemed = 1 WHERE id = 'acc_esc_acc_pur_1';
                        UPDATE escrow SET coupons_redeemed = 0 WHERE id = 'acc_esc_acc_pur_1'$q$,
    '23514', 'escrow', NULL, ARRAY['trg_escrow_progress']),
  pg_temp.try_reject($q$UPDATE escrow SET remaining_amount = '0', active = false WHERE id = 'acc_esc_acc_pur_1';
                        UPDATE escrow SET remaining_amount = '0', active = true WHERE id = 'acc_esc_acc_pur_1'$q$,
    '23514', 'escrow', NULL, ARRAY['trg_escrow_progress', 'ck_escrow_closed']));

SELECT pg_temp.rec_checks('T18', 'ปิด escrow ทั้งที่ยังมีเงิน หรือเงินหมดแต่ยังเปิด → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE escrow SET active = false WHERE id = 'acc_esc_acc_pur_1'$q$,
    '23514', 'escrow', NULL, ARRAY['ck_escrow_closed']),
  pg_temp.try_reject($q$UPDATE escrow SET remaining_amount = '0' WHERE id = 'acc_esc_acc_pur_1'$q$,
    '23514', 'escrow', NULL, ARRAY['ck_escrow_closed']));

-- ตรวจตอน commit ทุกอย่างที่ค้างอยู่ก่อน T17 · หลังจากนี้ trigger ฝั่งยอดจะไม่มี event เก่าค้าง
-- แต่ละกรณีของ T17 จึงถูกจับได้ด้วย trigger ฝั่งที่มันทดสอบเท่านั้น
SELECT pg_temp.try_ok('SET CONSTRAINTS ALL IMMEDIATE');
SELECT pg_temp.try_ok('SET CONSTRAINTS ALL DEFERRED');

-- =====================================================================
-- T17 · escrow ตรงกับ ledger ESCROW:<purchase> ตอน commit
--       ลดยอด escrow โดยไม่มี ledger · เงินเข้า ESCROW โดยไม่มี escrow ของ Vault → ไม่ผ่าน
-- =====================================================================
SELECT pg_temp.rec_checks('T17', 'escrow ไม่ตรงกับ ledger · เงินใน ESCROW โดยไม่มี escrow ของ Vault → ไม่ผ่านตอน commit',
  pg_temp.try_reject($q$
    UPDATE escrow SET remaining_amount = '100000000000000000000', coupons_redeemed = 1 WHERE id = 'acc_esc_acc_pur_1';
    SET CONSTRAINTS trg_escrow_matches_ledger IMMEDIATE;
  $q$, '23514', 'escrow', NULL, ARRAY['trg_escrow_matches_ledger']),
  pg_temp.try_reject($q$
    INSERT INTO ledger_entry (id, operation_id, entry_seq, account, account_id, amount) VALUES
      ('acc_le_3a', 'acc_opb_acc_pur_3', 1, 'CUSTOMER_THB', 'CUSTOMER_THB:acc_cus_2', '-80000000000000000000'),
      ('acc_le_3b', 'acc_opb_acc_pur_3', 2, 'ESCROW', 'ESCROW:acc_pur_3', '80000000000000000000');
    UPDATE thb_balance SET amount = (amount::numeric - 80000000000000000000)::text
     WHERE account_id = 'CUSTOMER_THB:acc_cus_2';
    INSERT INTO thb_balance (account, account_id, amount) VALUES ('ESCROW', 'ESCROW:acc_pur_3', '80000000000000000000');
    SET CONSTRAINTS trg_escrow_matches_ledger IMMEDIATE;
  $q$, '23514', 'thb_balance', NULL, ARRAY['trg_escrow_matches_ledger']));
SELECT pg_temp.try_ok('SET CONSTRAINTS trg_escrow_matches_ledger DEFERRED');

-- =====================================================================
-- T11  · คูปองที่ใช้แล้ว ผู้ขายต้องได้เงิน · ตรวจตอน commit (ต้นฉบับเป็นแค่การตรวจข้อมูล)
--        ใช้คูปองโดยไม่ปล่อยเงินจาก escrow → ไม่ผ่าน
-- T11b · ใช้คูปองพร้อมปล่อยเงิน (Vault ปล่อย 80 ให้ผู้ขาย) → ผ่าน · ยอดผู้ขาย 80 · escrow เหลือ 80
-- =====================================================================
SELECT pg_temp.rec_checks('T11', 'ใช้คูปองที่ซื้อมาโดยผู้ขายไม่ได้เงิน → ไม่ผ่านตอน commit',
  pg_temp.try_reject($q$
    UPDATE coupon_code SET state = 'REDEEMED', redeemed_at = now() WHERE id = 'acc_cc_1';
    SET CONSTRAINTS trg_redeemed_coupon_paid IMMEDIATE;
  $q$, '23514', 'coupon_code', NULL, ARRAY['trg_redeemed_coupon_paid']));
SELECT pg_temp.try_ok('SET CONSTRAINTS trg_redeemed_coupon_paid DEFERRED');

DO $$
DECLARE r text[]; v_seller text; v_escrow text; v_rem text;
BEGIN
  r[1] := pg_temp.try_ok($q$
    UPDATE coupon_code SET state = 'REDEEMED', redeemed_at = now() WHERE id = 'acc_cc_1';
    UPDATE escrow SET remaining_amount = '80000000000000000000', coupons_redeemed = 1 WHERE id = 'acc_esc_acc_pur_1';
    INSERT INTO ledger_entry (id, operation_id, entry_seq, account, account_id, amount) VALUES
      ('acc_le_r1', 'acc_opr_2', 1, 'ESCROW', 'ESCROW:acc_pur_1', '-80000000000000000000'),
      ('acc_le_r2', 'acc_opr_2', 2, 'MERCHANT_THB', 'MERCHANT_THB:acc_mch_a', '80000000000000000000');
    UPDATE thb_balance SET amount = '80000000000000000000' WHERE account_id = 'ESCROW:acc_pur_1';
    INSERT INTO thb_balance (account, account_id, amount) VALUES ('MERCHANT_THB', 'MERCHANT_THB:acc_mch_a', '80000000000000000000');
    UPDATE operation SET status = 'SUBMITTED' WHERE id = 'acc_opr_2' AND status = 'PENDING';
    UPDATE operation SET status = 'CONFIRMED' WHERE id = 'acc_opr_2' AND status = 'SUBMITTED';
    UPDATE operation SET status = 'FINALIZED', finalized_at = now() WHERE id = 'acc_opr_2';
  $q$);
  r[2] := pg_temp.try_ok('SET CONSTRAINTS ALL IMMEDIATE');
  PERFORM pg_temp.try_ok('SET CONSTRAINTS ALL DEFERRED');
  SELECT amount INTO v_seller FROM thb_balance WHERE account_id = 'MERCHANT_THB:acc_mch_a';
  SELECT amount INTO v_escrow FROM thb_balance WHERE account_id = 'ESCROW:acc_pur_1';
  SELECT remaining_amount INTO v_rem FROM escrow WHERE id = 'acc_esc_acc_pur_1';
  IF v_seller IS DISTINCT FROM '80000000000000000000' OR v_escrow IS DISTINCT FROM v_rem THEN
    r[3] := format('FAIL: ผู้ขาย %s · ESCROW %s · escrow %s', v_seller, v_escrow, v_rem);
  END IF;
  PERFORM pg_temp.rec_checks('T11b', 'ใช้คูปองพร้อมปล่อยเงิน 80 ให้ผู้ขาย → ผ่าน · ทุกยอดตรงกัน', VARIADIC r);
END $$;

-- =====================================================================
-- T12 · state = REDEEMED โดยไม่มี redeemed_at → ถูกปฏิเสธ
-- T14 · REDEEMED มาจาก HELD เท่านั้น · ใช้แล้วย้อนกลับไม่ได้
-- =====================================================================
SELECT pg_temp.rec_checks('T12', 'state=REDEEMED โดยไม่มี redeemed_at → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE coupon_code SET state = 'REDEEMED' WHERE id = 'acc_cc_5'$q$,
    '23514', 'coupon_code', NULL, ARRAY['ck_code_redeemed']));

SELECT pg_temp.rec_checks('T14', 'คูปองที่ใช้แล้วกลับเป็น HELD / ใบ ISSUED ถูกตั้งเป็น REDEEMED → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE coupon_code SET state = 'HELD', redeemed_at = NULL WHERE id = 'acc_cc_1'$q$,
    '23514', 'coupon_code', NULL, ARRAY['trg_coupon_redeemed_final']),
  pg_temp.try_reject($q$UPDATE coupon_code SET state = 'REDEEMED', redeemed_at = now() WHERE id = 'acc_cc_6'$q$,
    '23514', 'coupon_code', NULL, ARRAY['trg_coupon_redeemed_final']));

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
