-- =====================================================================
-- FEAT-14 · Indexer + Projector · acceptance สำหรับ blockchain-reward-platform-be
--
-- ต้นฉบับ : blockchain-reward-platform/reference/feat-14/tests/acceptance.sql (36 ข้อ)
-- schema  : migrations 0002 → 0017_feat14_indexer
--
-- รัน (Postgres ใน docker):
--   docker exec -i blockchain-reward-platform-be-postgres-1 \
--     psql -U reward -d reward < tests/sql/feat14_acceptance.sql
--
-- ผล  PASS ฐานข้อมูลบังคับกฎนี้ได้จริง · FAIL ไม่ได้ หรือถูกปฏิเสธด้วยกฎตัวอื่น
--     ERROR test เองรันไม่ได้ · ดูหมายเหตุ
--
-- ต่างจากต้นฉบับ · หลักฐานอยู่ใน docs/reference-findings.md
--   1. event ตาม contract จริง · Coupon เป็น ERC-1155 (TransferSingle/Batch บอกชนิดกับจำนวน ไม่บอกใบ)
--      coupon_holding จึงเป็นจำนวนต่อ (ชนิดคูปอง, ผู้ถือ) · ใบที่ถูกใช้มาจาก CouponRedeemed.redeemCodes
--      ลง coupon_redemption · coupon_code.state ยังเป็นของงาน redeem (FEAT-11) projector ไม่เขียนทับ
--      ที่อยู่ → ผู้ถือผ่าน wallet · ที่อยู่ศูนย์ = mint/burn · ที่อยู่อื่น (marketplace) = EXTERNAL
--   2. lease ระบุตัว process ผ่าน set_config('app.indexer_owner') · T4b T4c T4d เพิ่ม
--   3. ถอย cursor ได้เฉพาะ reorg ที่บันทึก ROLLED_BACK ในธุรกรรมนี้ · T6b · app.reorg_in_progress ใช้ไม่ได้แล้ว
--   4. T10b · บล็อกแก้/ลบ/คืนชีพไม่ได้ · T15b T15c · event ต้องตรงกับบล็อกของมันและบล็อกต้องยังอยู่
--      T15d · event แก้/ลบไม่ได้ · T27b · final ต้องลึกพอ และตั้งแล้วถอนไม่ได้
--   5. T29b · N ถูกปรับขึ้น · reorg ตื้นกว่า N แต่กิน event ที่ final → MANUAL_REVIEW
--      (ต้นฉบับ f_handle_reorg เลือก MANUAL_REVIEW แต่ trigger ของมันเองปฏิเสธ)
--      T31b · reorg_log ต้องตรงกับที่ฐานข้อมูลเห็น และแก้/ลบไม่ได้ · T31c · คนไม่ถือ lease จัดการ reorg ไม่ได้
--   6. T11b · บล็อกที่เกิน cursor ก็ไม่ผ่านตอน commit เหมือน event
--   7. บล็อกเริ่มที่ 20 (ความสูงที่ contract เริ่ม) · T8 คือบล็อกแรกที่ไม่มีตัวก่อนหน้า
--   8. ข้อมูลทดสอบขึ้นต้นด้วย acc_ · ข้อ "ต้องถูกปฏิเสธ" ตรวจว่าชนกฎที่ตั้งใจจริง
--
-- ทั้งไฟล์อยู่ใน transaction เดียวแล้ว ROLLBACK · รันซ้ำกี่รอบก็ได้
-- =====================================================================
\set ON_ERROR_STOP on
\pset format aligned

BEGIN;

-- ควรได้ 0017_feat14_indexer ขึ้นไป
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


-- hash ของสายหลักและของสายที่แยกออกไป · ค่ารูปแบบเดียวกับของจริง (0x + 64 hex)
CREATE FUNCTION pg_temp.bh(n bigint) RETURNS text LANGUAGE sql IMMUTABLE AS
$$ SELECT '0x' || encode(sha256(convert_to('acc-block-' || n, 'UTF8')), 'hex') $$;
CREATE FUNCTION pg_temp.fh(n bigint) RETURNS text LANGUAGE sql IMMUTABLE AS
$$ SELECT '0x' || encode(sha256(convert_to('acc-fork-' || n, 'UTF8')), 'hex') $$;
CREATE FUNCTION pg_temp.th(s text) RETURNS text LANGUAGE sql IMMUTABLE AS
$$ SELECT '0x' || encode(sha256(convert_to('acc-tx-' || s, 'UTF8')), 'hex') $$;
CREATE FUNCTION pg_temp.addr(s text) RETURNS text LANGUAGE sql IMMUTABLE AS
$$ SELECT '0x' || left(encode(sha256(convert_to('acc-addr-' || s, 'UTF8')), 'hex'), 40) $$;
CREATE FUNCTION pg_temp.zero() RETURNS text LANGUAGE sql IMMUTABLE AS $$ SELECT '0x' || repeat('0', 40) $$;
CREATE FUNCTION pg_temp.bt(n bigint) RETURNS timestamptz LANGUAGE sql IMMUTABLE AS
$$ SELECT timestamptz '2026-01-01 00:00:00+00' + n * interval '12 seconds' $$;

-- อ่านบล็อกหนึ่งบล็อกบนสายหลัก แล้วขยับ cursor ในธุรกรรมเดียวกัน (L8)
CREATE FUNCTION pg_temp.read_block(n bigint) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO raw_block (block_number, block_hash, parent_hash, "timestamp", tx_count)
  VALUES (n, pg_temp.bh(n), pg_temp.bh(n - 1), pg_temp.bt(n), 1);
  UPDATE indexer_cursor SET last_block = n, last_block_hash = pg_temp.bh(n), updated_at = now()
   WHERE id = 'main';
END $$;

-- event หนึ่งแถวจากบล็อกที่ยังอยู่บนสายหลัก · args ที่ decoder เขียน
CREATE FUNCTION pg_temp.ev(p_id text, p_block bigint, p_log int, p_name text, p_address text,
                           p_args jsonb) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT format($q$INSERT INTO chain_event (id, block_number, block_hash, tx_hash, log_index, address,
                                            topic0, topics, data, block_timestamp, event_name, args)
                   SELECT %L, block_number, block_hash, %L, %s, %L, %L, '[]', '\x00', "timestamp", %L, %L::json
                     FROM raw_block WHERE block_number = %s AND orphaned_at IS NULL$q$,
                p_id, pg_temp.th(p_id), p_log, p_address, pg_temp.th('topic-' || p_name), p_name,
                p_args::text, p_block)
$$;

CREATE FUNCTION pg_temp.coupon_tx(p_from text, p_to text, p_ids text[], p_values text[]) RETURNS jsonb
LANGUAGE sql IMMUTABLE AS $$
  SELECT jsonb_build_object('from', p_from, 'to', p_to, 'ids', to_jsonb(p_ids), 'values', to_jsonb(p_values))
$$;
CREATE FUNCTION pg_temp.point_tx(p_from text, p_to text, p_value text) RETURNS jsonb
LANGUAGE sql IMMUTABLE AS $$ SELECT jsonb_build_object('from', p_from, 'to', p_to, 'value', p_value) $$;

CREATE FUNCTION pg_temp.as_owner(p_owner text, p_sql text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT format('SELECT set_config(''app.indexer_owner'', %L, true); %s; SELECT set_config(''app.indexer_owner'', ''acc_idx_1'', true)',
                p_owner, p_sql)
$$;

CREATE FUNCTION pg_temp.cursor_at() RETURNS bigint LANGUAGE sql STABLE AS
$$ SELECT last_block FROM indexer_cursor WHERE id = 'main' $$;

-- ------------------------------------------------------------ fixture
INSERT INTO merchant (id, name, status, activated_at) VALUES ('acc_mch_a', 'ร้านทดสอบ', 'ACTIVE', now());
INSERT INTO wallet (id, address, key_ref, owner_kind, owner_id) VALUES
  ('acc_wal_m',  pg_temp.addr('m'),  'kms/acc-m',  'MERCHANT', 'acc_mch_a'),
  ('acc_wal_c1', pg_temp.addr('c1'), 'kms/acc-c1', 'CUSTOMER', 'acc_cus_1'),
  ('acc_wal_c2', pg_temp.addr('c2'), 'kms/acc-c2', 'CUSTOMER', 'acc_cus_2'),
  ('acc_wal_c3', pg_temp.addr('c3'), 'kms/acc-c3', 'CUSTOMER', 'acc_cus_3');

INSERT INTO point_type (id, merchant_id, name, name_normalized, symbol, business_model, lot_policy) VALUES
  ('acc_pt_1', 'acc_mch_a', 'แต้มทดสอบ', 'แต้มทดสอบ', 'ACCPT', 2, 'FEFO_ONLY');
INSERT INTO point_type_chain (point_type_id, contract_address, deployed_block, deployed_tx_hash,
                              business_model, lot_policy, configured_block, configured_tx_hash) VALUES
  ('acc_pt_1', pg_temp.addr('point'), 10, pg_temp.th('pt-deploy'), 2, 'FEFO_ONLY', 11, pg_temp.th('pt-config'));

INSERT INTO coupon_type (id, merchant_id, name, name_normalized, face_value, total_supply, status,
                         expires_at, live_at, created_at, starts_at) VALUES
  ('acc_ct_1', 'acc_mch_a', 'คูปอง 100', 'คูปอง 100', '100000000000000000000', 100, 'LIVE',
   now() + interval '180 days', now(), now(), now() - interval '1 day'),
  ('acc_ct_2', 'acc_mch_a', 'คูปอง 50', 'คูปอง 50', '50000000000000000000', 100, 'LIVE',
   now() + interval '180 days', now(), now(), now() - interval '1 day');
INSERT INTO coupon_type_chain (coupon_type_id, contract_address, chain_type_id, deployed_block, deployed_tx_hash) VALUES
  ('acc_ct_1', pg_temp.addr('coupon'), '7', 10, pg_temp.th('ct1-deploy')),
  ('acc_ct_2', pg_temp.addr('coupon'), '8', 10, pg_temp.th('ct2-deploy'));
INSERT INTO coupon_code (id, coupon_type_id, code_hash, code_ref, state, holder_kind, holder_id, held_at)
SELECT 'acc_cc_' || g, 'acc_ct_1', pg_temp.h('CODE-' || g), pg_temp.h('acc_cc_' || g),
       'HELD', 'MERCHANT', 'acc_mch_a', now()
  FROM generate_series(1, 5) g;
INSERT INTO coupon_code (id, coupon_type_id, code_hash, code_ref, state)
VALUES ('acc_cc_x', 'acc_ct_2', pg_temp.h('CODE-X'), pg_temp.h('acc_cc_x'), 'ISSUED');

INSERT INTO finality_config (id, depth_blocks, effective_from, note) VALUES
  ('acc_fin_v1', 12, now() - interval '30 days', 'ค่าตั้งต้น · ยังไม่ยืนยันกับทีม chain'),
  ('acc_fin_v2',  6, now() + interval '30 days', 'ลดลงในอนาคตถ้า chain นิ่งพอ');
INSERT INTO indexer_cursor (id, last_block) VALUES ('main', 0);
SELECT set_config('app.indexer_owner', 'acc_idx_1', true);

-- =====================================================================
-- T1 · cursor มีแถวเดียว · id ต้องเป็น main
-- T2 · ขยับ cursor โดยไม่มี lease → ถูกปฏิเสธ
-- =====================================================================
SELECT pg_temp.rec_checks('T1', 'cursor แถวที่สอง → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO indexer_cursor (id, last_block) VALUES ('second', 0)$q$,
    '23514', 'indexer_cursor', NULL, ARRAY['ck_cursor_id']));

INSERT INTO raw_block (block_number, block_hash, parent_hash, "timestamp", tx_count)
VALUES (20, pg_temp.bh(20), pg_temp.bh(19), pg_temp.bt(20), 1);
SELECT pg_temp.rec_checks('T2', 'L8 · ขยับ cursor โดยไม่มีใครถือ lease → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE indexer_cursor SET last_block = 20, last_block_hash = pg_temp.bh(20) WHERE id = 'main'$q$,
    '23514', 'indexer_cursor', NULL, ARRAY['trg_cursor_needs_lease']));

-- acc_idx_1 ขอ lease
SELECT pg_temp.try_ok($q$UPDATE indexer_cursor SET lease_owner = 'acc_idx_1', lease_until = now() + interval '1 hour'
                          WHERE id = 'main'$q$);

-- =====================================================================
-- T3  · lease หมดอายุแล้วขยับ → ถูกปฏิเสธ
-- T4  · เปลี่ยนเจ้าของ lease พร้อมขยับในสเตตเมนต์เดียว → ถูกปฏิเสธ
-- T4b · process อื่นขยับ cursor ขณะที่ lease ยังเป็นของ acc_idx_1 → ถูกปฏิเสธ
-- T4c · process อื่นแย่ง lease ที่ยังไม่หมดอายุ → ถูกปฏิเสธ
-- T4d · lease หมดอายุแล้ว process อื่นรับต่อได้ · คืน lease ได้โดยเจ้าของ
-- T5  · ถือ lease ที่ยังไม่หมดอายุ · ขยับได้
-- =====================================================================
SELECT pg_temp.rec_checks('T3', 'lease หมดอายุแล้วขยับ cursor → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    UPDATE indexer_cursor SET lease_until = now() - interval '1 second' WHERE id = 'main';
    UPDATE indexer_cursor SET last_block = 20, last_block_hash = pg_temp.bh(20) WHERE id = 'main';
  $q$, '23514', 'indexer_cursor', NULL, ARRAY['trg_cursor_needs_lease']));
SELECT pg_temp.rec_checks('T4', 'เปลี่ยนเจ้าของ lease พร้อมขยับ cursor → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.as_owner('acc_idx_2', $q$
    UPDATE indexer_cursor SET lease_owner = 'acc_idx_2', last_block = 20, last_block_hash = pg_temp.bh(20)
     WHERE id = 'main'$q$),
    '23514', 'indexer_cursor', NULL, ARRAY['trg_cursor_needs_lease']));
SELECT pg_temp.rec_checks('T4b', 'process ที่ไม่ได้ถือ lease ขยับ cursor → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.as_owner('acc_idx_2', $q$
    UPDATE indexer_cursor SET last_block = 20, last_block_hash = pg_temp.bh(20) WHERE id = 'main'$q$),
    '23514', 'indexer_cursor', NULL, ARRAY['trg_cursor_needs_lease']));
SELECT pg_temp.rec_checks('T4c', 'แย่ง lease ที่ยังไม่หมดอายุ → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.as_owner('acc_idx_2', $q$
    UPDATE indexer_cursor SET lease_owner = 'acc_idx_2', lease_until = now() + interval '1 hour'
     WHERE id = 'main'$q$),
    '23514', 'indexer_cursor', NULL, ARRAY['trg_cursor_lease']));

DO $$
DECLARE r text[]; v text;
BEGIN
  r[1] := pg_temp.try_ok($q$UPDATE indexer_cursor SET lease_until = now() - interval '1 second' WHERE id = 'main'$q$);
  r[2] := pg_temp.try_ok(pg_temp.as_owner('acc_idx_2', $q$
    UPDATE indexer_cursor SET lease_owner = 'acc_idx_2', lease_until = now() + interval '1 hour' WHERE id = 'main';
    UPDATE indexer_cursor SET lease_owner = NULL, lease_until = NULL WHERE id = 'main'$q$));
  r[3] := pg_temp.try_ok($q$UPDATE indexer_cursor SET lease_owner = 'acc_idx_1', lease_until = now() + interval '1 hour'
                             WHERE id = 'main'$q$);
  SELECT lease_owner INTO v FROM indexer_cursor WHERE id = 'main';
  r[4] := CASE WHEN v = 'acc_idx_1' THEN NULL ELSE 'FAIL: lease อยู่กับ ' || coalesce(v, 'ไม่มีใคร') END;
  PERFORM pg_temp.rec_checks('T4d', 'lease หมดอายุ → process อื่นรับต่อได้ · เจ้าของคืน lease ได้', VARIADIC r);
END $$;

DO $$
DECLARE r text[];
BEGIN
  r[1] := pg_temp.try_ok($q$UPDATE indexer_cursor SET last_block = 20, last_block_hash = pg_temp.bh(20) WHERE id = 'main'$q$);
  r[2] := pg_temp.try_ok('SELECT pg_temp.read_block(21)');
  r[3] := CASE WHEN pg_temp.cursor_at() = 21 THEN NULL ELSE 'FAIL: cursor อยู่ที่ ' || pg_temp.cursor_at() END;
  PERFORM pg_temp.rec_checks('T5', 'ถือ lease อยู่ · ขยับ cursor ได้ (20 → 21)', VARIADIC r);
END $$;

-- =====================================================================
-- T6  · cursor ถอยหลังนอกเหตุ reorg → ถูกปฏิเสธ
-- T6b · ตั้ง app.reorg_in_progress แบบต้นฉบับ ก็ยังถอยไม่ได้
-- =====================================================================
SELECT pg_temp.rec_checks('T6', 'cursor ถอยจาก 21 ไป 20 โดยไม่มี reorg → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE indexer_cursor SET last_block = 20, last_block_hash = pg_temp.bh(20) WHERE id = 'main'$q$,
    '23514', 'indexer_cursor', NULL, ARRAY['trg_cursor_rewind']));
SELECT pg_temp.rec_checks('T6b', 'ตั้ง app.reorg_in_progress = true แล้วถอย cursor → ยังถูกปฏิเสธ',
  pg_temp.try_reject($q$
    SELECT set_config('app.reorg_in_progress', 'true', true);
    UPDATE indexer_cursor SET last_block = 20, last_block_hash = pg_temp.bh(20) WHERE id = 'main';
  $q$, '23514', 'indexer_cursor', NULL, ARRAY['trg_cursor_rewind']));

-- =====================================================================
-- T7  · RFD 5 · parent_hash ไม่ตรงกับบล็อกก่อนหน้า → ถูกปฏิเสธ
-- T8  · บล็อกแรก (20) ไม่มีตัวก่อนหน้าในฐานข้อมูล · รับได้
-- T9  · block_hash ซ้ำ → ถูกปฏิเสธ · T10 · บล็อกที่เป็น parent ของตัวเอง → ถูกปฏิเสธ
-- T10b · แก้ / ลบ / คืนชีพบล็อก → ถูกปฏิเสธ
-- =====================================================================
SELECT pg_temp.rec_checks('T7', 'RFD 5 · บล็อก 22 ชี้ parent ที่ไม่ใช่บล็อก 21 → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO raw_block (block_number, block_hash, parent_hash, "timestamp")
                        VALUES (22, pg_temp.bh(22), pg_temp.fh(21), pg_temp.bt(22))$q$,
    '23514', 'raw_block', NULL, ARRAY['trg_block_links_to_parent']));

DO $$
BEGIN
  PERFORM pg_temp.rec('T8', 'บล็อกแรก (20) ไม่มีตัวก่อนหน้า · รับได้ ไม่มีอะไรให้เทียบ',
    CASE WHEN EXISTS (SELECT 1 FROM raw_block WHERE block_hash = pg_temp.bh(20))
          AND NOT EXISTS (SELECT 1 FROM raw_block WHERE block_hash = pg_temp.bh(19))
         THEN 'PASS' ELSE 'FAIL' END);
END $$;

SELECT pg_temp.rec_checks('T9', 'block_hash ซ้ำ → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO raw_block (block_number, block_hash, parent_hash, "timestamp", orphaned_at)
                        VALUES (21, pg_temp.bh(21), pg_temp.bh(20), pg_temp.bt(21), now())$q$,
    '23505', 'raw_block', ARRAY['block_hash'], NULL));
SELECT pg_temp.rec_checks('T10', 'บล็อกที่เป็น parent ของตัวเอง → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO raw_block (block_number, block_hash, parent_hash, "timestamp")
                        VALUES (22, pg_temp.fh(22), pg_temp.fh(22), pg_temp.bt(22))$q$,
    '23514', 'raw_block', NULL, ARRAY['ck_not_own_parent']));
SELECT pg_temp.rec_checks('T10b', 'แก้ parent / ลบ / orphan แล้วคืนชีพบล็อก → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE raw_block SET parent_hash = pg_temp.fh(19) WHERE block_hash = pg_temp.bh(20)$q$,
    '23514', 'raw_block', NULL, ARRAY['trg_raw_block_guard']),
  pg_temp.try_reject($q$DELETE FROM raw_block WHERE block_hash = pg_temp.bh(21)$q$,
    '23514', 'raw_block', NULL, ARRAY['trg_raw_block_guard']),
  pg_temp.try_reject($q$UPDATE raw_block SET orphaned_at = now() WHERE block_hash = pg_temp.bh(21);
                        UPDATE raw_block SET orphaned_at = NULL WHERE block_hash = pg_temp.bh(21)$q$,
    '23514', 'raw_block', NULL, ARRAY['trg_raw_block_guard']));

-- =====================================================================
-- T11  · L8 · event ที่บล็อกเกิน cursor → ไม่ผ่านตอน commit
-- T11b · บล็อกที่เกิน cursor → ไม่ผ่านตอน commit เช่นกัน
-- T12  · อ่านบล็อก + event + ขยับ cursor ในธุรกรรมเดียว → ผ่าน
-- =====================================================================
SELECT pg_temp.rec_checks('T11', 'L8 · event ที่บล็อก 22 แต่ cursor อยู่ 21 → ไม่ผ่าน',
  pg_temp.try_reject($q$
    INSERT INTO raw_block (block_number, block_hash, parent_hash, "timestamp")
    VALUES (22, pg_temp.bh(22), pg_temp.bh(21), pg_temp.bt(22));
    $q$ || pg_temp.ev('acc_ce_ahead', 22, 0, 'UNKNOWN', pg_temp.addr('x'), NULL) || $q$;
    SET CONSTRAINTS trg_event_consistent IMMEDIATE;
  $q$, '23514', 'chain_event', NULL, ARRAY['trg_event_within_cursor']));
SELECT pg_temp.try_ok('SET CONSTRAINTS trg_event_consistent DEFERRED');
SELECT pg_temp.rec_checks('T11b', 'บล็อก 22 แต่ cursor อยู่ 21 → ไม่ผ่าน',
  pg_temp.try_reject($q$
    INSERT INTO raw_block (block_number, block_hash, parent_hash, "timestamp")
    VALUES (22, pg_temp.bh(22), pg_temp.bh(21), pg_temp.bt(22));
    SET CONSTRAINTS trg_block_consistent IMMEDIATE;
  $q$, '23514', 'raw_block', NULL, ARRAY['trg_block_within_cursor']));
SELECT pg_temp.try_ok('SET CONSTRAINTS trg_block_consistent DEFERRED');

SELECT pg_temp.rec_checks('T12', 'L8 · บล็อก + event + ขยับ cursor ในธุรกรรมเดียว → ผ่านตอน commit',
  pg_temp.try_ok('SELECT pg_temp.read_block(22); ' || pg_temp.ev('acc_ce_22', 22, 0, 'UNKNOWN', pg_temp.addr('x'), NULL)
                 || '; SET CONSTRAINTS ALL IMMEDIATE'));
SELECT pg_temp.try_ok('SET CONSTRAINTS ALL DEFERRED');

-- =====================================================================
-- T13 · อ่าน log เดิมซ้ำ → ไม่เกิดแถวซ้ำ · T14 · รู้ชนิดแต่ไม่มี args → ถูกปฏิเสธ
-- T15 · event อ้างบล็อกที่ไม่มี → ถูกปฏิเสธ
-- T15b · event ที่ block_number / เวลา ไม่ตรงกับบล็อกของมัน → ถูกปฏิเสธ
-- T15d · แก้สิ่งที่ chain บอก หรือลบ event → ถูกปฏิเสธ
-- =====================================================================
SELECT pg_temp.rec_checks('T13', 'อ่าน log เดิมซ้ำ (tx_hash, log_index เดิม) → ไม่เกิดแถวซ้ำ',
  pg_temp.try_reject($q$INSERT INTO chain_event (id, block_number, block_hash, tx_hash, log_index, address, topic0,
                                                 topics, data, block_timestamp)
                        SELECT 'acc_ce_22_again', block_number, block_hash, tx_hash, log_index, address, topic0,
                               topics, data, block_timestamp FROM chain_event WHERE id = 'acc_ce_22'$q$,
    '23505', 'chain_event', ARRAY['log_index', 'tx_hash'], NULL));
SELECT pg_temp.rec_checks('T14', 'event ที่บอกว่ารู้จักชนิดแต่ไม่มี args → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.ev('acc_ce_noargs', 22, 1, 'PointTransfer', pg_temp.addr('point'), NULL),
    '23514', 'chain_event', NULL, ARRAY['ck_known_has_args']));
SELECT pg_temp.rec_checks('T15', 'event ที่อ้างบล็อกที่ไม่มีอยู่ → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO chain_event (id, block_number, block_hash, tx_hash, log_index, address, topic0,
                                                 topics, data, block_timestamp)
                        VALUES ('acc_ce_ghost', 21, pg_temp.fh(999), pg_temp.th('ghost'), 0, pg_temp.addr('x'),
                                pg_temp.th('t'), '[]', '\x00', pg_temp.bt(21))$q$,
    '23503', 'chain_event', ARRAY['block_hash'], NULL));
SELECT pg_temp.rec_checks('T15b', 'event อ้างบล็อก 22 แต่ใส่เลขบล็อก 21 / เวลาอื่น → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO chain_event (id, block_number, block_hash, tx_hash, log_index, address, topic0,
                                                 topics, data, block_timestamp)
                        VALUES ('acc_ce_lie1', 21, pg_temp.bh(22), pg_temp.th('lie1'), 0, pg_temp.addr('x'),
                                pg_temp.th('t'), '[]', '\x00', pg_temp.bt(22))$q$,
    '23514', 'chain_event', NULL, ARRAY['trg_event_matches_block']),
  pg_temp.try_reject($q$INSERT INTO chain_event (id, block_number, block_hash, tx_hash, log_index, address, topic0,
                                                 topics, data, block_timestamp)
                        VALUES ('acc_ce_lie2', 22, pg_temp.bh(22), pg_temp.th('lie2'), 0, pg_temp.addr('x'),
                                pg_temp.th('t'), '[]', '\x00', now())$q$,
    '23514', 'chain_event', NULL, ARRAY['trg_event_matches_block']));
SELECT pg_temp.rec_checks('T15d', 'แก้ data / ที่อยู่ ของ event หรือลบ event → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE chain_event SET data = '\x01' WHERE id = 'acc_ce_22'$q$,
    '23514', 'chain_event', NULL, ARRAY['trg_chain_event_guard']),
  pg_temp.try_reject($q$UPDATE chain_event SET address = pg_temp.addr('y') WHERE id = 'acc_ce_22'$q$,
    '23514', 'chain_event', NULL, ARRAY['trg_chain_event_guard']),
  pg_temp.try_reject($q$DELETE FROM chain_event WHERE id = 'acc_ce_22'$q$,
    '23514', 'chain_event', NULL, ARRAY['trg_chain_event_guard']));

-- ---------------------------------------------------------------------
-- ฉากสำหรับ apply และ replay · event ตาม contract จริง
--   23  mint คูปองชนิด 7 ×5 และชนิด 8 ×2 ให้ร้าน (TransferBatch จากที่อยู่ศูนย์)
--   24  ร้านโอนชนิด 7 ×2 ให้ cus_1 · ร้านลงขายชนิด 7 ×1 (ไปอยู่ที่ marketplace)
--   25  point mint 1000 ให้ cus_1 · cus_1 โอน 300 ให้ cus_2
--   26  cus_1 ใช้คูปองชนิด 7 ×1 · burn (ไปที่อยู่ศูนย์) + CouponRedeemed(code_ref ของ acc_cc_1)
--   27  event ชนิดที่ยังไม่รู้จัก
-- ---------------------------------------------------------------------
SELECT pg_temp.try_ok('SELECT pg_temp.read_block(g) FROM generate_series(23, 30) g');
SELECT pg_temp.try_ok(pg_temp.ev('acc_ce_c1', 23, 0, 'CouponTransfer', pg_temp.addr('coupon'),
  pg_temp.coupon_tx(pg_temp.zero(), pg_temp.addr('m'), ARRAY['7', '8'], ARRAY['5', '2'])));
SELECT pg_temp.try_ok(pg_temp.ev('acc_ce_c2', 24, 0, 'CouponTransfer', pg_temp.addr('coupon'),
  pg_temp.coupon_tx(pg_temp.addr('m'), pg_temp.addr('c1'), ARRAY['7'], ARRAY['2'])));
SELECT pg_temp.try_ok(pg_temp.ev('acc_ce_c3', 24, 1, 'CouponTransfer', pg_temp.addr('coupon'),
  pg_temp.coupon_tx(pg_temp.addr('m'), pg_temp.addr('marketplace'), ARRAY['7'], ARRAY['1'])));
SELECT pg_temp.try_ok(pg_temp.ev('acc_ce_p1', 25, 0, 'PointTransfer', pg_temp.addr('point'),
  pg_temp.point_tx(pg_temp.zero(), pg_temp.addr('c1'), '1000')));
SELECT pg_temp.try_ok(pg_temp.ev('acc_ce_p2', 25, 1, 'PointTransfer', pg_temp.addr('point'),
  pg_temp.point_tx(pg_temp.addr('c1'), pg_temp.addr('c2'), '300')));
SELECT pg_temp.try_ok(pg_temp.ev('acc_ce_c4', 26, 0, 'CouponTransfer', pg_temp.addr('coupon'),
  pg_temp.coupon_tx(pg_temp.addr('c1'), pg_temp.zero(), ARRAY['7'], ARRAY['1'])));
SELECT pg_temp.try_ok(pg_temp.ev('acc_ce_r1', 26, 1, 'CouponRedeemed', pg_temp.addr('coupon'),
  jsonb_build_object('typeId', '7', 'owner', pg_temp.addr('c1'), 'amount', '1',
                     'redeemCodes', jsonb_build_array(pg_temp.h('acc_cc_1')))));
SELECT pg_temp.try_ok(pg_temp.ev('acc_ce_u1', 27, 0, 'UNKNOWN', pg_temp.addr('x'), NULL));

CREATE FUNCTION pg_temp.holding(p_type text, p_kind text, p_id text) RETURNS text LANGUAGE sql STABLE AS
$$ SELECT quantity FROM coupon_holding WHERE coupon_type_id = p_type AND holder_kind = p_kind AND holder_id = p_id $$;
CREATE FUNCTION pg_temp.points(p_id text) RETURNS text LANGUAGE sql STABLE AS
$$ SELECT amount FROM point_balance WHERE point_type_id = 'acc_pt_1' AND holder_id = p_id $$;

-- =====================================================================
-- T16 · apply ตามลำดับ · คูปองเป็นจำนวนต่อผู้ถือ · point 700 / 300 · ใบที่ใช้ลง coupon_redemption
-- T17 · apply ซ้ำไม่นับสอง · T18 · event ชนิดที่ไม่รู้จัก apply แล้วแต่ไม่แตะ projection
-- =====================================================================
DO $$
DECLARE r text; v text;
BEGIN
  r := pg_temp.try_ok('SELECT f_apply_pending()');
  v := concat_ws(' ', 'ร้าน7=' || pg_temp.holding('acc_ct_1', 'MERCHANT', 'acc_mch_a'),
                      'ร้าน8=' || pg_temp.holding('acc_ct_2', 'MERCHANT', 'acc_mch_a'),
                      'cus1=' || pg_temp.holding('acc_ct_1', 'CUSTOMER', 'acc_cus_1'),
                      'ตลาด=' || pg_temp.holding('acc_ct_1', 'EXTERNAL', pg_temp.addr('marketplace')),
                      'pt1=' || pg_temp.points('acc_cus_1'), 'pt2=' || pg_temp.points('acc_cus_2'));
  BEGIN
    v := v || ' ใช้=' || coalesce((SELECT redeemer_id FROM coupon_redemption WHERE coupon_code_id = 'acc_cc_1'), '-');
  EXCEPTION WHEN undefined_table THEN v := v || ' ใช้=(ไม่มีตาราง coupon_redemption)';
  END;
  PERFORM pg_temp.rec_checks('T16', 'apply ครบ · ร้าน 2+2 · cus_1 1 · marketplace 1 · point 700/300 · acc_cc_1 ใช้โดย cus_1', r,
    CASE WHEN v = 'ร้าน7=2 ร้าน8=2 cus1=1 ตลาด=1 pt1=700 pt2=300 ใช้=acc_cus_1' THEN NULL ELSE 'FAIL: ' || v END);
END $$;

DO $$
DECLARE r text[]; v_again boolean; n int;
BEGIN
  BEGIN
    v_again := f_apply_event('acc_ce_p1');
    n := f_apply_pending();
    r[1] := CASE WHEN v_again = false AND n = 0 AND pg_temp.points('acc_cus_1') = '700' THEN NULL
                 ELSE format('FAIL: apply ซ้ำได้ %s · คิว %s · ยอด %s', v_again, n, pg_temp.points('acc_cus_1')) END;
  EXCEPTION WHEN OTHERS THEN r[1] := 'ERROR: ' || SQLSTATE || ' · ' || SQLERRM;
  END;
  PERFORM pg_temp.rec_checks('T17', 'apply ซ้ำไม่นับสอง · ยอด cus_1 ยัง 700', VARIADIC r);
END $$;

DO $$
DECLARE v timestamptz;
BEGIN
  SELECT applied_at INTO v FROM chain_event WHERE id = 'acc_ce_u1';
  PERFORM pg_temp.rec('T18', 'event ชนิดที่ไม่รู้จัก · apply แล้ว ไม่ค้างคิว ไม่แตะ projection',
    CASE WHEN v IS NOT NULL AND (SELECT count(*) FROM coupon_holding) = 4 THEN 'PASS' ELSE 'FAIL' END,
    format('applied %s · holding %s แถว', v IS NOT NULL, (SELECT count(*) FROM coupon_holding)));
END $$;

SELECT pg_temp.rec_checks('T16b', 'ที่อยู่ wallet เก็บตัวพิมพ์เล็กแบบเดียว (projector หาเจ้าของจากที่อยู่) · ตัวใหญ่ → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO wallet (id, address, key_ref, owner_kind, owner_id)
                        VALUES ('acc_wal_up', '0x' || repeat('AB', 20), 'kms/acc-up', 'CUSTOMER', 'acc_cus_9')$q$,
    '23514', 'wallet', NULL, ARRAY['ck_wallet_address_lower']));

-- =====================================================================
-- T19  · event พูดถึงชนิดคูปอง / ชนิด point ที่ระบบไม่รู้จัก → ถูกปฏิเสธตอน apply
-- T19b · CouponRedeemed อ้าง code_ref ที่ไม่มี หรือเป็นของชนิดอื่น → ถูกปฏิเสธตอน apply
-- =====================================================================
SELECT pg_temp.rec_checks('T19', 'คูปอง typeId 99 / point จาก contract ที่ไม่รู้จัก → ถูกปฏิเสธตอน apply',
  pg_temp.try_reject(pg_temp.ev('acc_ce_bad1', 27, 1, 'CouponTransfer', pg_temp.addr('coupon'),
      pg_temp.coupon_tx(pg_temp.zero(), pg_temp.addr('c3'), ARRAY['99'], ARRAY['1']))
    || $q$; SELECT f_apply_event('acc_ce_bad1')$q$,
    '23514', 'chain_event', NULL, ARRAY['apply_unknown_coupon_type']),
  pg_temp.try_reject(pg_temp.ev('acc_ce_bad2', 27, 2, 'PointTransfer', pg_temp.addr('other-token'),
      pg_temp.point_tx(pg_temp.zero(), pg_temp.addr('c3'), '1'))
    || $q$; SELECT f_apply_event('acc_ce_bad2')$q$,
    '23514', 'chain_event', NULL, ARRAY['apply_unknown_point_type']));
SELECT pg_temp.rec_checks('T19b', 'redeemCodes ที่ไม่มีในระบบ / เป็นของชนิด 8 แต่อ้างชนิด 7 → ถูกปฏิเสธตอน apply',
  pg_temp.try_reject(pg_temp.ev('acc_ce_bad3', 27, 3, 'CouponRedeemed', pg_temp.addr('coupon'),
      jsonb_build_object('typeId', '7', 'owner', pg_temp.addr('c1'), 'amount', '1',
                         'redeemCodes', jsonb_build_array(pg_temp.h('ไม่มีจริง'))))
    || $q$; SELECT f_apply_event('acc_ce_bad3')$q$,
    '23514', 'chain_event', NULL, ARRAY['apply_unknown_code']),
  pg_temp.try_reject(pg_temp.ev('acc_ce_bad4', 27, 4, 'CouponRedeemed', pg_temp.addr('coupon'),
      jsonb_build_object('typeId', '7', 'owner', pg_temp.addr('c1'), 'amount', '1',
                         'redeemCodes', jsonb_build_array(pg_temp.h('acc_cc_x'))))
    || $q$; SELECT f_apply_event('acc_ce_bad4')$q$,
    '23514', 'chain_event', NULL, ARRAY['apply_unknown_code']));

-- =====================================================================
-- =====  T20 ถึง T23 · RFD 6 §3.2 · ลบ projection แล้ว replay ใหม่ ต้องได้ค่าเดิมเป๊ะ  =====
-- เทียบทุกคอลัมน์ทุกแถวด้วย EXCEPT สองทิศทาง
-- =====================================================================
CREATE TEMP TABLE acc_snap_holding AS SELECT * FROM coupon_holding;
CREATE TEMP TABLE acc_snap_balance AS SELECT * FROM point_balance;
CREATE TEMP TABLE acc_snap_code    AS SELECT id, state, holder_kind, holder_id, redeemed_at FROM coupon_code;
CREATE TEMP TABLE acc_snap_meta    AS SELECT count(*) AS applied FROM chain_event WHERE applied_at IS NOT NULL;
DO $$
BEGIN
  EXECUTE 'CREATE TEMP TABLE acc_snap_redemption AS SELECT * FROM coupon_redemption';
EXCEPTION WHEN undefined_table THEN
  CREATE TEMP TABLE acc_snap_redemption (none text);
END $$;

CREATE TEMP TABLE acc_replay AS SELECT pg_temp.try_ok($$SELECT f_replay_projections()$$) AS replay_result;

CREATE FUNCTION pg_temp.diff(p_now text, p_snap text) RETURNS int LANGUAGE plpgsql AS $$
DECLARE n int;
BEGIN
  EXECUTE format('SELECT count(*) FROM ((SELECT * FROM %s EXCEPT SELECT * FROM %s)
                                         UNION ALL (SELECT * FROM %s EXCEPT SELECT * FROM %s)) d',
                 p_now, p_snap, p_snap, p_now) INTO n;
  RETURN n;
END $$;

DO $$
DECLARE r text;
BEGIN
  r := (SELECT replay_result FROM acc_replay);
  PERFORM pg_temp.rec_checks('T20', 'RFD 6 · ลบแล้ว replay · coupon_holding เหมือนเดิมทุกคอลัมน์ทุกแถว', r,
    CASE WHEN pg_temp.diff('coupon_holding', 'acc_snap_holding') = 0 AND (SELECT count(*) FROM acc_snap_holding) = 4
         THEN NULL ELSE format('FAIL: ต่าง %s แถว', pg_temp.diff('coupon_holding', 'acc_snap_holding')) END);
  PERFORM pg_temp.rec_checks('T21', 'RFD 6 · ลบแล้ว replay · point_balance เหมือนเดิมทุกคอลัมน์ทุกแถว', r,
    CASE WHEN pg_temp.diff('point_balance', 'acc_snap_balance') = 0 AND (SELECT count(*) FROM acc_snap_balance) = 2
         THEN NULL ELSE format('FAIL: ต่าง %s แถว', pg_temp.diff('point_balance', 'acc_snap_balance')) END);
END $$;

DO $$
DECLARE r text; v_code int; v_red int;
BEGIN
  r := (SELECT replay_result FROM acc_replay);
  SELECT count(*) INTO v_code FROM ((SELECT id, state, holder_kind, holder_id, redeemed_at FROM coupon_code
                                     EXCEPT SELECT * FROM acc_snap_code)) d;
  BEGIN
    v_red := pg_temp.diff('coupon_redemption', 'acc_snap_redemption');
    IF (SELECT count(*) FROM acc_snap_redemption) <> 1 THEN v_red := -1; END IF;
  EXCEPTION WHEN OTHERS THEN v_red := -1;
  END;
  PERFORM pg_temp.rec_checks('T22', 'RFD 6 · coupon_redemption เหมือนเดิม · replay ไม่แตะ coupon_code.state ของงาน redeem', r,
    CASE WHEN v_code = 0 AND v_red = 0 THEN NULL
         ELSE format('FAIL: coupon_code ต่าง %s · coupon_redemption %s', v_code,
                     CASE v_red WHEN -1 THEN 'ไม่มี/ไม่ครบ' ELSE 'ต่าง ' || v_red END) END);
END $$;

DO $$
DECLARE v_cols text; v_bad int; v_applied int;
BEGIN
  SELECT string_agg(table_name || '.' || column_name, ', ') INTO v_cols
    FROM information_schema.columns
   WHERE table_schema = current_schema()
     AND table_name IN ('coupon_holding', 'point_balance', 'coupon_redemption')
     AND (column_default ILIKE '%now()%' OR column_default ILIKE '%current_timestamp%'
          OR column_name IN ('applied_at', 'updated_at'));
  SELECT count(*) INTO v_bad FROM coupon_holding h
    JOIN chain_event e ON e.block_number = h.as_of_block AND e.orphaned_at IS NULL
   WHERE h.as_of_time <> e.block_timestamp;
  SELECT count(*) INTO v_applied FROM chain_event WHERE applied_at IS NOT NULL;
  PERFORM pg_temp.rec('T23', 'projection ไม่มีคอลัมน์เวลาที่ apply · as_of_time มาจากบล็อก · จำนวน event เท่าเดิม',
    CASE WHEN v_cols IS NULL AND v_bad = 0 AND v_applied = (SELECT applied FROM acc_snap_meta) THEN 'PASS' ELSE 'FAIL' END,
    concat_ws(' · ', 'คอลัมน์: ' || v_cols, CASE WHEN v_bad > 0 THEN v_bad || ' แถวเวลาไม่ตรงบล็อก' END));
END $$;

-- ---------------------------------------------------------------------
-- อ่านต่อถึงบล็อก 45 ให้ event ชุดแรกลึกเกิน N = 12 (45 - 12 = 33)
-- ---------------------------------------------------------------------
SELECT pg_temp.try_ok('SELECT pg_temp.read_block(g) FROM generate_series(31, 45) g');

-- =====================================================================
-- T24 · reorg ลึก 1 (ตื้นกว่า N · ไม่กิน event ที่ final) → ROLLED_BACK
-- T25 · หลัง reorg projection กลับไปเหมือน snapshot
-- T15c · event ใหม่บนบล็อกที่ orphan แล้ว → ถูกปฏิเสธ
-- T26 · บล็อกใหม่บนสายที่ชนะ · ความสูง 45 มีสองใบ
-- =====================================================================
SELECT pg_temp.try_ok(pg_temp.ev('acc_ce_c5', 45, 0, 'CouponTransfer', pg_temp.addr('coupon'),
  pg_temp.coupon_tx(pg_temp.addr('m'), pg_temp.addr('c3'), ARRAY['7'], ARRAY['1'])) || '; SELECT f_apply_pending()');

DO $$
DECLARE r text; v_id text; v_out text; v_depth int; v_n int; v_h text; v_m text;
BEGIN
  BEGIN
    v_id := f_handle_reorg(45, 'acc_idx_1');
    SELECT outcome, depth INTO v_out, v_depth FROM reorg_log WHERE id = v_id;
    SELECT count(*) INTO v_n FROM chain_event WHERE block_number >= 45 AND orphaned_at IS NOT NULL;
    v_h := pg_temp.holding('acc_ct_1', 'CUSTOMER', 'acc_cus_3');
    v_m := pg_temp.holding('acc_ct_1', 'MERCHANT', 'acc_mch_a');
    r := CASE WHEN v_out = 'ROLLED_BACK' AND v_depth = 1 AND pg_temp.cursor_at() = 44 AND v_n = 1
                   AND v_h IS NULL AND v_m = '2' THEN NULL
              ELSE format('FAIL: %s ลึก %s · cursor %s · orphan %s · cus_3 %s · ร้าน %s',
                          v_out, v_depth, pg_temp.cursor_at(), v_n, v_h, v_m) END;
  EXCEPTION WHEN OTHERS THEN r := 'ERROR: ' || SQLSTATE || ' · ' || SQLERRM;
  END;
  PERFORM pg_temp.rec_checks('T24', 'reorg ลึก 1 → ROLLED_BACK · cursor ถอยไป 44 · event หลุด · projection ย้อน', r);
END $$;

SELECT pg_temp.rec_checks('T25', 'หลัง reorg · projection กลับไปเหมือน snapshot ก่อนบล็อกที่หลุด',
  CASE WHEN pg_temp.diff('coupon_holding', 'acc_snap_holding') = 0
             AND pg_temp.diff('point_balance', 'acc_snap_balance') = 0 THEN NULL
       ELSE format('FAIL: holding ต่าง %s · balance ต่าง %s', pg_temp.diff('coupon_holding', 'acc_snap_holding'),
                   pg_temp.diff('point_balance', 'acc_snap_balance')) END);

SELECT pg_temp.rec_checks('T15c', 'event ใหม่บนบล็อก 45 ที่ orphan แล้ว → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO chain_event (id, block_number, block_hash, tx_hash, log_index, address, topic0,
                                                 topics, data, block_timestamp)
                        VALUES ('acc_ce_dead', 45, pg_temp.bh(45), pg_temp.th('dead'), 9, pg_temp.addr('x'),
                                pg_temp.th('t'), '[]', '\x00', pg_temp.bt(45))$q$,
    '23514', 'chain_event', NULL, ARRAY['trg_event_matches_block']));

DO $$
DECLARE r text; n int;
BEGIN
  r := pg_temp.try_ok($q$
    INSERT INTO raw_block (block_hash, block_number, parent_hash, "timestamp")
    VALUES (pg_temp.fh(45), 45, pg_temp.bh(44), pg_temp.bt(45) + interval '1 second');
    UPDATE indexer_cursor SET last_block = 45, last_block_hash = pg_temp.fh(45) WHERE id = 'main';
  $q$);
  SELECT count(*) INTO n FROM raw_block WHERE block_number = 45;
  PERFORM pg_temp.rec_checks('T26', 'สายใหม่เขียนได้ · ความสูง 45 มีสองใบ · tip ชี้ใบใหม่', r,
    CASE WHEN n = 2 AND (SELECT last_block_hash FROM indexer_cursor) = pg_temp.fh(45) THEN NULL
         ELSE format('FAIL: ความสูง 45 มี %s ใบ', n) END);
END $$;

SELECT pg_temp.rec_checks('T26b', 'บล็อกบนสายหลักใบที่สองที่ความสูง 45 (ใบเดิมยังไม่ orphan) → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO raw_block (block_hash, block_number, parent_hash, "timestamp")
                        VALUES (pg_temp.fh(4545), 45, pg_temp.bh(44), pg_temp.bt(45))$q$,
    '23505', 'raw_block', ARRAY['block_number'], NULL));

-- =====================================================================
-- T27  · event ที่ final แล้ว orphan ไม่ได้
-- T27b · ตั้ง final ให้ event ที่ยังไม่ลึกพอ หรือถอน final → ถูกปฏิเสธ
-- T28  · f_mark_finalized ทำ final เฉพาะบล็อกที่ลึกกว่า N
-- =====================================================================
CREATE TEMP TABLE acc_marked AS SELECT pg_temp.try_ok($$SELECT f_mark_finalized()$$) AS mark_result;
SELECT pg_temp.try_ok(pg_temp.ev('acc_ce_c6', 45, 0, 'CouponTransfer', pg_temp.addr('coupon'),
  pg_temp.coupon_tx(pg_temp.addr('m'), pg_temp.addr('c2'), ARRAY['7'], ARRAY['1'])) || '; SELECT f_apply_pending()');

DO $$
DECLARE r text;
BEGIN
  IF (SELECT finalized_at FROM chain_event WHERE id = 'acc_ce_c1') IS NULL THEN
    r := 'FAIL: ฉากผิด · acc_ce_c1 (บล็อก 23) ยังไม่ final · ' || coalesce((SELECT mark_result FROM acc_marked), '');
  ELSE
    r := pg_temp.try_reject($q$UPDATE chain_event SET applied_at = NULL, orphaned_at = now() WHERE id = 'acc_ce_c1'$q$,
      '23514', 'chain_event', NULL, ARRAY['trg_final_not_orphanable']);
  END IF;
  PERFORM pg_temp.rec_checks('T27', 'event ที่ final แล้ว orphan ไม่ได้', r);
END $$;

SELECT pg_temp.rec_checks('T27b', 'ตั้ง final ให้ event ที่บล็อก 45 (ยังไม่ลึกพอ) / ถอน final → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE chain_event SET finalized_at = now() WHERE id = 'acc_ce_c6'$q$,
    '23514', 'chain_event', NULL, ARRAY['trg_event_finality']),
  pg_temp.try_reject($q$UPDATE chain_event SET finalized_at = NULL WHERE id = 'acc_ce_c1'$q$,
    '23514', 'chain_event', NULL, ARRAY['trg_event_finality']));

DO $$
DECLARE v_max bigint; v_min bigint;
BEGIN
  SELECT max(block_number) INTO v_max FROM chain_event WHERE finalized_at IS NOT NULL AND orphaned_at IS NULL;
  SELECT min(block_number) INTO v_min FROM chain_event
   WHERE finalized_at IS NULL AND orphaned_at IS NULL AND applied_at IS NOT NULL;
  PERFORM pg_temp.rec('T28', 'final เฉพาะบล็อกที่ลึกกว่า N · tip 45 · N 12 → ถึง 33',
    CASE WHEN v_max IS NOT NULL AND v_max <= 33 AND (v_min IS NULL OR v_min > 33) THEN 'PASS' ELSE 'FAIL' END,
    format('final ถึง %s · ยังไม่ final ตั้งแต่ %s', v_max, v_min));
END $$;

-- =====================================================================
-- T29  · reorg ที่กิน event final (ลึกเกิน N ด้วย) → MANUAL_REVIEW · ไม่แตะอะไรเลย
-- T29b · N ถูกปรับขึ้นเป็น 40 · reorg ลึก 22 ตื้นกว่า N แต่กิน event final → MANUAL_REVIEW
-- =====================================================================
DO $$
DECLARE r text; v_id text; v_out text; v_note text; v_cur bigint := pg_temp.cursor_at(); v_orph int;
BEGIN
  BEGIN
    v_id := f_handle_reorg(23, 'acc_idx_1');
    SELECT outcome, note INTO v_out, v_note FROM reorg_log WHERE id = v_id;
    SELECT count(*) INTO v_orph FROM chain_event WHERE block_number BETWEEN 23 AND 44 AND orphaned_at IS NOT NULL;
    r := CASE WHEN v_out = 'MANUAL_REVIEW' AND v_orph = 0 AND pg_temp.cursor_at() = v_cur AND v_note IS NOT NULL
              THEN NULL ELSE format('FAIL: %s · orphan %s · cursor %s', v_out, v_orph, pg_temp.cursor_at()) END;
  EXCEPTION WHEN OTHERS THEN r := 'ERROR: ' || SQLSTATE || ' · ' || SQLERRM;
  END;
  PERFORM pg_temp.rec_checks('T29', 'reorg ที่กิน event final → MANUAL_REVIEW · ไม่ orphan ไม่ถอย cursor', r);
END $$;

DO $$
DECLARE r text; v_id text; v_out text; v_depth int; v_n int;
BEGIN
  INSERT INTO finality_config (id, depth_blocks, effective_from, note)
  VALUES ('acc_fin_up', 40, now() - interval '1 day', 'ปรับขึ้นชั่วคราว');
  BEGIN
    v_id := f_handle_reorg(24, 'acc_idx_1');
    SELECT outcome, depth, finality_depth INTO v_out, v_depth, v_n FROM reorg_log WHERE id = v_id;
    r := CASE WHEN v_out = 'MANUAL_REVIEW' AND v_depth = 22 AND v_n = 40 THEN NULL
              ELSE format('FAIL: %s ลึก %s N %s', v_out, v_depth, v_n) END;
  EXCEPTION WHEN OTHERS THEN r := 'FAIL: ' || SQLSTATE || ' · ' || SQLERRM;
  END;
  DELETE FROM finality_config WHERE id = 'acc_fin_up';
  PERFORM pg_temp.rec_checks('T29b', 'N 40 · reorg ลึก 22 แต่กิน event final → MANUAL_REVIEW (ต้นฉบับ error)', r);
END $$;

-- =====================================================================
-- T30  · outcome ไม่ตรงกับความลึกจริง → ถูกปฏิเสธ (ทั้งสองทิศ)
-- T31  · depth ไม่ตรงกับ old_tip - fork_block + 1 → ถูกปฏิเสธ
-- T31b · old_tip / จำนวน event ที่ไม่ตรงกับที่ฐานข้อมูลเห็น → ถูกปฏิเสธ · แก้/ลบ reorg_log → ถูกปฏิเสธ
-- T31c · process ที่ไม่ได้ถือ lease จัดการ reorg → ถูกปฏิเสธ
-- =====================================================================
CREATE FUNCTION pg_temp.reorg_row(p_id text, p_fork bigint, p_tip bigint, p_depth int, p_n int,
                                  p_events int, p_outcome text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT format($q$INSERT INTO reorg_log (id, fork_block, old_tip, depth, finality_depth, orphaned_events, outcome)
                   VALUES (%L, %s, %s, %s, %s, %s, %L)$q$, p_id, p_fork, p_tip, p_depth, p_n, p_events, p_outcome)
$$;
CREATE FUNCTION pg_temp.live_from(p_fork bigint) RETURNS int LANGUAGE sql STABLE AS
$$ SELECT count(*)::int FROM chain_event WHERE block_number >= p_fork AND orphaned_at IS NULL $$;

SELECT pg_temp.rec_checks('T30', 'ลึก 2 (ตื้นกว่า N) อ้าง MANUAL_REVIEW / ลึก 16 อ้าง ROLLED_BACK → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.reorg_row('acc_rg_bad1', 44, 45, 2, 12, pg_temp.live_from(44), 'MANUAL_REVIEW'),
    '23514', 'reorg_log', NULL, ARRAY['trg_reorg_outcome_matches_depth']),
  pg_temp.try_reject(pg_temp.reorg_row('acc_rg_bad2', 30, 45, 16, 12, pg_temp.live_from(30), 'ROLLED_BACK'),
    '23514', 'reorg_log', NULL, ARRAY['trg_reorg_outcome_matches_depth']));
SELECT pg_temp.rec_checks('T31', 'depth 99 ไม่ตรงกับช่วงบล็อก 44-45 → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.reorg_row('acc_rg_bad3', 44, 45, 99, 12, pg_temp.live_from(44), 'ROLLED_BACK'),
    '23514', 'reorg_log', NULL, ARRAY['ck_depth_matches']));
SELECT pg_temp.rec_checks('T31b', 'old_tip / จำนวน event / N ที่ไม่ตรงความจริง → ถูกปฏิเสธ · แก้ / ลบ reorg_log → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.reorg_row('acc_rg_bad4', 44, 50, 7, 12, pg_temp.live_from(44), 'ROLLED_BACK'),
    '23514', 'reorg_log', NULL, ARRAY['trg_reorg_log_facts']),
  pg_temp.try_reject(pg_temp.reorg_row('acc_rg_bad5', 44, 45, 2, 12, pg_temp.live_from(44) + 5, 'ROLLED_BACK'),
    '23514', 'reorg_log', NULL, ARRAY['trg_reorg_log_facts']),
  pg_temp.try_reject(pg_temp.reorg_row('acc_rg_bad6', 44, 45, 2, 1, pg_temp.live_from(44), 'MANUAL_REVIEW'),
    '23514', 'reorg_log', NULL, ARRAY['trg_reorg_log_facts']),
  pg_temp.try_reject($q$UPDATE reorg_log SET outcome = 'ROLLED_BACK' WHERE fork_block = 23$q$,
    '23514', 'reorg_log', NULL, ARRAY['trg_reorg_log_append_only']),
  pg_temp.try_reject($q$DELETE FROM reorg_log WHERE fork_block = 23$q$,
    '23514', 'reorg_log', NULL, ARRAY['trg_reorg_log_append_only']));
SELECT pg_temp.rec_checks('T31c', 'acc_idx_2 (ไม่ได้ถือ lease) สั่งจัดการ reorg → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.as_owner('acc_idx_2', $q$SELECT f_handle_reorg(45, 'acc_idx_2')$q$),
    '23514', 'indexer_cursor', NULL, ARRAY['trg_cursor_needs_lease']));

-- =====================================================================
-- T32 · N อิงเวลาที่ถาม · T33 · event ที่ orphan แล้วทำเครื่องหมาย applied ไม่ได้
-- T34 · จ่าย point จากคนที่ไม่มียอด → ถูกปฏิเสธ · T35 · จ่ายเกินยอด → ถูกปฏิเสธ
-- =====================================================================
DO $$
DECLARE r text;
BEGIN
  BEGIN
    r := CASE WHEN f_finality_depth(now()) = 12 AND f_finality_depth(now() + interval '60 days') = 6 THEN NULL
              ELSE format('FAIL: วันนี้ %s · อนาคต %s', f_finality_depth(now()), f_finality_depth(now() + interval '60 days')) END;
  EXCEPTION WHEN OTHERS THEN r := 'ERROR: ' || SQLSTATE || ' · ' || SQLERRM;
  END;
  PERFORM pg_temp.rec_checks('T32', 'N อิงเวลา · วันนี้ 12 · อนาคต 6', r);
END $$;

SELECT pg_temp.rec_checks('T33', 'event ที่ orphan แล้ว ทำเครื่องหมาย applied ไม่ได้',
  pg_temp.try_reject($q$UPDATE chain_event SET applied_at = now() WHERE id = 'acc_ce_c5'$q$,
    '23514', 'chain_event', NULL, ARRAY['ck_orphan_not_applied']));

SELECT pg_temp.rec_checks('T34', 'จ่าย point จาก cus_3 ที่ไม่มียอด → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.ev('acc_ce_nobal', 45, 5, 'PointTransfer', pg_temp.addr('point'),
      pg_temp.point_tx(pg_temp.addr('c3'), pg_temp.addr('c1'), '50'))
    || $q$; SELECT f_apply_event('acc_ce_nobal')$q$,
    '23514', 'chain_event', NULL, ARRAY['apply_insufficient_balance']));
SELECT pg_temp.rec_checks('T35', 'cus_2 มี 300 จ่าย 99999 → ถูกปฏิเสธ · คูปองก็เช่นกัน · ยอดติดลบตรงๆ ก็ไม่ได้',
  pg_temp.try_reject(pg_temp.ev('acc_ce_neg', 45, 6, 'PointTransfer', pg_temp.addr('point'),
      pg_temp.point_tx(pg_temp.addr('c2'), pg_temp.addr('c3'), '99999'))
    || $q$; SELECT f_apply_event('acc_ce_neg')$q$,
    '23514', 'chain_event', NULL, ARRAY['apply_insufficient_balance']),
  pg_temp.try_reject(pg_temp.ev('acc_ce_negc', 45, 7, 'CouponTransfer', pg_temp.addr('coupon'),
      pg_temp.coupon_tx(pg_temp.addr('c1'), pg_temp.addr('c3'), ARRAY['7'], ARRAY['5']))
    || $q$; SELECT f_apply_event('acc_ce_negc')$q$,
    '23514', 'chain_event', NULL, ARRAY['apply_insufficient_balance']),
  pg_temp.try_reject($q$UPDATE point_balance SET amount = '-1' WHERE holder_id = 'acc_cus_2'$q$,
    '23514', 'point_balance', NULL, ARRAY['ck_point_balance_amount_uint256']));

-- =====================================================================
-- T36b · cursor ชี้บล็อกที่ไม่มีอยู่บนสายหลัก → ไม่ผ่านตอน commit
-- =====================================================================
SELECT pg_temp.rec_checks('T36b', 'cursor ขยับไป 46 แต่ hash ไม่ใช่บล็อก 46 ที่อ่านมา → ไม่ผ่าน',
  pg_temp.try_reject($q$
    INSERT INTO raw_block (block_hash, block_number, parent_hash, "timestamp")
    VALUES (pg_temp.bh(46), 46, pg_temp.fh(45), pg_temp.bt(46));
    UPDATE indexer_cursor SET last_block = 46, last_block_hash = pg_temp.fh(46) WHERE id = 'main';
    SET CONSTRAINTS trg_cursor_consistent IMMEDIATE;
  $q$, '23514', 'indexer_cursor', NULL, ARRAY['trg_cursor_on_live_block']));
SELECT pg_temp.try_ok('SET CONSTRAINTS trg_cursor_consistent DEFERRED');

-- =====================================================================
-- T36 · ปิดท้าย · ตรวจตอน commit ผ่าน · ไม่มี event / บล็อกเกิน cursor · สายโซ่ไม่ขาด
-- =====================================================================
DO $$
DECLARE r text; v_ahead int; v_broken int;
BEGIN
  r := pg_temp.try_ok('SET CONSTRAINTS ALL IMMEDIATE');
  SELECT count(*) INTO v_ahead FROM chain_event WHERE orphaned_at IS NULL AND block_number > pg_temp.cursor_at();
  SELECT count(*) INTO v_broken FROM raw_block b
    JOIN raw_block p ON p.block_number = b.block_number - 1 AND p.orphaned_at IS NULL
   WHERE b.orphaned_at IS NULL AND b.parent_hash <> p.block_hash;
  PERFORM pg_temp.rec_checks('T36', 'ปิดท้าย · commit ผ่าน · ไม่มี event เกิน cursor · สายโซ่บล็อกไม่ขาด', r,
    CASE WHEN v_ahead = 0 AND v_broken = 0 THEN NULL ELSE format('FAIL: เกิน cursor %s · ขาด %s', v_ahead, v_broken) END);
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
