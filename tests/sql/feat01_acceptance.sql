-- =====================================================================
-- FEAT-01 · acceptance · ฉบับสำหรับ schema ของ blockchain-reward-platform-be
--
-- ต้นฉบับ : blockchain-reward-platform/reference/feat-01/tests/acceptance.sql
-- schema  : migrations/versions/0002_legacy_schema_port.py
--
-- รัน (Postgres ใน docker):
--   docker exec -i blockchain-reward-platform-be-postgres-1 \
--     psql -U reward -d reward < tests/sql/feat01_acceptance.sql
--
-- ผลมีสามแบบ
--   PASS   ฐานข้อมูลบังคับกฎนี้ได้จริง
--   FAIL   ฐานข้อมูลยอมให้ทำสิ่งที่ RFD ห้าม หรือถูกปฏิเสธด้วย constraint ตัวอื่น
--   ERROR  test เองรันไม่ได้ (ข้อมูลทดสอบไม่เข้ากับ schema) · ดู note
--
-- ต่างจากต้นฉบับ
--   1. address/hash เป็น hex string ('0x…') ตาม 0002 ไม่ใช่ bytea
--   2. ใส่คอลัมน์ NOT NULL ที่ 0002 ตัด default ทิ้ง (expires_at, topics, raw_block)
--   3. ข้อที่ "ต้องถูกปฏิเสธ" ตรวจด้วยว่าชน constraint บนคอลัมน์ที่ตั้งใจทดสอบจริง
--      ต้นฉบับดูแค่ SQLSTATE จึงเกิด PASS ปลอมได้ เช่น T10 จะชน uq_step_name แทน
--   4. ถ้าฐานข้อมูลยอมให้ทำ แถวนั้นถูกย้อนทิ้งทันที ข้อถัดไปจึงไม่ปนเปื้อน
--   5. ข้อมูลทดสอบขึ้นต้นด้วย acc_ เพื่อไม่ชนข้อมูลจริง
--
-- ทั้งไฟล์อยู่ใน transaction เดียวแล้ว ROLLBACK · รันซ้ำกี่รอบก็ได้ ไม่ทิ้งอะไรไว้
-- =====================================================================
\set ON_ERROR_STOP on
\pset format aligned

BEGIN;

-- ควรได้ 0002_legacy_schema_port · ถ้าไม่ใช่ ผลข้างล่างอาจไม่ตรงกับ schema ที่ไฟล์นี้อ้างอิง
SELECT version_num AS "alembic revision" FROM alembic_version;

-- ซ่อน output ระหว่างทาง ให้เหลือแค่ตารางสรุปท้ายไฟล์
\o /dev/null

CREATE TEMP TABLE result (id text, what text, verdict text, note text);

-- บันทึกผลหนึ่งข้อ
CREATE FUNCTION pg_temp.rec(p_id text, p_what text, p_verdict text, p_note text DEFAULT NULL)
RETURNS void LANGUAGE sql AS
$$ INSERT INTO result VALUES (p_id, p_what, p_verdict, p_note) $$;

-- คอลัมน์ที่ constraint หรือ unique index ชื่อนี้ครอบอยู่ (เรียงตามชื่อ)
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

-- รัน p_sql แล้วคาดว่าต้องถูกปฏิเสธ
--   p_state : '23505' unique_violation · '23514' check_violation
--   p_table, p_cols : constraint ที่ปฏิเสธต้องอยู่บนตารางนี้ และครอบคอลัมน์เหล่านี้
-- คืน NULL ถ้าผ่าน · คืน 'FAIL: …' หรือ 'ERROR: …' ถ้าไม่ผ่าน
-- ถ้า p_sql ทำสำเร็จ จะถูกย้อนทิ้งทันทีด้วย sentinel exception
CREATE FUNCTION pg_temp.try_reject(p_sql text, p_state text, p_table text, p_cols text[])
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
    RETURN 'FAIL: ฐานข้อมูลยอมให้ทำ · ไม่มี constraint ที่ห้าม';
  END IF;
  IF v_state <> p_state THEN
    RETURN format('ERROR: คาด SQLSTATE %s แต่ได้ %s · %s', p_state, v_state, v_msg);
  END IF;

  v_cols := pg_temp.cols_of(v_tab, v_con);
  IF v_tab = p_table AND p_cols <@ v_cols THEN
    RETURN NULL;
  END IF;
  RETURN format('FAIL: ถูกปฏิเสธด้วย %s.%s (%s) ซึ่งไม่ใช่กฎที่ทดสอบ',
                v_tab, v_con, array_to_string(v_cols, ','));
END $$;

-- รวมผลย่อยหลายตัวเป็นผลเดียว · ERROR ชนะ FAIL ชนะ PASS
CREATE FUNCTION pg_temp.rec_checks(p_id text, p_what text, VARIADIC p_results text[])
RETURNS void LANGUAGE plpgsql AS $$
DECLARE v_bad text[];
BEGIN
  SELECT array_agg(r) INTO v_bad FROM unnest(p_results) r WHERE r IS NOT NULL;
  v_bad := (SELECT array_agg(regexp_replace(b, '^(FAIL|ERROR): ', '')) FROM unnest(v_bad) b)
           || CASE WHEN v_bad IS NULL THEN NULL
                   WHEN EXISTS (SELECT 1 FROM unnest(v_bad) r WHERE r LIKE 'ERROR:%') THEN ARRAY['__ERROR__'] END;
  IF v_bad IS NULL THEN
    PERFORM pg_temp.rec(p_id, p_what, 'PASS');
  ELSIF '__ERROR__' = ANY (v_bad) THEN
    PERFORM pg_temp.rec(p_id, p_what, 'ERROR', array_to_string(array_remove(v_bad, '__ERROR__'), ' | '));
  ELSE
    PERFORM pg_temp.rec(p_id, p_what, 'FAIL', array_to_string(v_bad, ' | '));
  END IF;
END $$;

-- ------------------------------------------------------------ fixture
-- address 20 byte = '0x' + 40 hex = 42 ตัวอักษร · hash 32 byte = '0x' + 64 hex = 66 ตัวอักษร
INSERT INTO signer (address, key_ref)
VALUES ('0x' || repeat('ac', 20), 'kms/acc-signer-1');

-- =====================================================================
-- T1 · สมัครด้วย Idempotency-Key เดิมซ้ำ ต้องไม่เกิดร้านสองร้าน  RFD 3 R1
--      scope ตอนสมัครคือ anon:<sha256(normalized email)> เพราะยังไม่มี actor
--      0002 ตัด default ของ expires_at ทิ้ง จึงต้องใส่เอง
-- =====================================================================
DO $$
DECLARE n1 int; n2 int; sc text;
BEGIN
  sc := 'anon:' || encode(sha256('acc-owner@shop.co.th'::bytea), 'hex');

  INSERT INTO idempotency_key (id, scope, key, fingerprint, expires_at)
  VALUES ('acc_ik_1', sc, '11111111-2222-3333-4444-555555555555',
          '0x' || encode(sha256('body'::bytea), 'hex'), now() + interval '72 hours')
  ON CONFLICT (scope, key) DO NOTHING;
  GET DIAGNOSTICS n1 = ROW_COUNT;

  INSERT INTO idempotency_key (id, scope, key, fingerprint, expires_at)
  VALUES ('acc_ik_2', sc, '11111111-2222-3333-4444-555555555555',
          '0x' || encode(sha256('body'::bytea), 'hex'), now() + interval '72 hours')
  ON CONFLICT (scope, key) DO NOTHING;
  GET DIAGNOSTICS n2 = ROW_COUNT;

  PERFORM pg_temp.rec('T1', 'กดสมัครซ้ำด้วย key เดิม → แถวที่สองได้ 0 rows ไม่ throw',
                      CASE WHEN n1 = 1 AND n2 = 0 THEN 'PASS' ELSE 'FAIL' END,
                      format('แถวแรก %s · แถวที่สอง %s', n1, n2));
EXCEPTION WHEN OTHERS THEN
  PERFORM pg_temp.rec('T1', 'กดสมัครซ้ำด้วย key เดิม → แถวที่สองได้ 0 rows ไม่ throw',
                      'ERROR', SQLSTATE || ' · ' || SQLERRM);
END $$;

-- =====================================================================
-- T2 · อีเมลเดียวกันที่พิมพ์ต่างกัน สมัครซ้ำไม่ได้          RFD 8 §3.3
--      "  Owner@Shop.CO.TH " กับ "owner@shop.co.th" คือคนเดียวกัน
-- =====================================================================
INSERT INTO merchant (id, name) VALUES ('acc_mch_1', 'ร้านทดสอบ');
INSERT INTO merchant_admin (id, merchant_id, email, email_normalized, password_hash, display_name)
VALUES ('acc_adm_1', 'acc_mch_1', 'Acc-Owner@Shop.CO.TH', lower(btrim('  Acc-Owner@Shop.CO.TH ')),
        '$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHQ$aaaa', 'เจ้าของร้าน');
INSERT INTO merchant (id, name) VALUES ('acc_mch_2', 'ร้านซ้ำ');

SELECT pg_temp.rec_checks('T2', 'อีเมลเดียวกันที่พิมพ์ตัวใหญ่/มีช่องว่าง → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO merchant_admin (id, merchant_id, email, email_normalized, password_hash, display_name)
    VALUES ('acc_adm_2', 'acc_mch_2', 'acc-owner@shop.co.th', 'acc-owner@shop.co.th',
            '$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHQ$bbbb', 'คนอื่น')
  $q$, '23505', 'merchant_admin', ARRAY['email_normalized']));

-- =====================================================================
-- T3 · ตาราง wallet ต้องไม่มีคอลัมน์ที่เก็บ private key         RFD 4 L6
--      ข้อนี้ตรวจโครงสร้างตาราง ไม่ใช่ตรวจข้อมูล
-- =====================================================================
DO $$
DECLARE bad text[];
BEGIN
  SELECT array_agg(column_name::text) INTO bad
  FROM information_schema.columns
  WHERE table_schema = 'public' AND table_name = 'wallet'
    AND (column_name ~* '(private|secret|mnemonic|seed|encrypted|cipher)'
         OR column_name ~* '^(pk|priv|sk)$');
  PERFORM pg_temp.rec('T3', 'ตาราง wallet ไม่มีคอลัมน์ที่เก็บ private key เลย',
                      CASE WHEN bad IS NULL THEN 'PASS' ELSE 'FAIL' END,
                      CASE WHEN bad IS NULL THEN NULL
                           ELSE 'คอลัมน์ต้องสงสัย: ' || array_to_string(bad, ',') END);
END $$;

-- =====================================================================
-- T4 · password ต้องเป็น argon2id เท่านั้น                   RFD 8 D7
--      bcrypt ($2a$/$2b$) และ plaintext เข้าไม่ได้
-- =====================================================================
INSERT INTO merchant (id, name) VALUES ('acc_mch_t4', 'ร้าน T4');

SELECT pg_temp.rec_checks('T4', 'password ที่เป็น bcrypt หรือ plaintext เข้าฐานข้อมูลไม่ได้',
  pg_temp.try_reject($q$
    INSERT INTO merchant_admin (id, merchant_id, email, email_normalized, password_hash, display_name)
    VALUES ('acc_adm_t4a', 'acc_mch_t4', 'acc-a@b.co', 'acc-a@b.co', '$2b$10$abcdefghijklmnopqrstuv', 'x')
  $q$, '23514', 'merchant_admin', ARRAY['password_hash']),
  pg_temp.try_reject($q$
    INSERT INTO merchant_admin (id, merchant_id, email, email_normalized, password_hash, display_name)
    VALUES ('acc_adm_t4b', 'acc_mch_t4', 'acc-c@d.co', 'acc-c@d.co', 'hunter2', 'x')
  $q$, '23514', 'merchant_admin', ARRAY['password_hash']));

-- =====================================================================
-- T5 · ร้านเป็น ACTIVE โดยไม่มี activated_at ไม่ได้          RFD 8 §3.4
--      ACTIVE ต้องมาจาก projector หลัง provision จบจริงเท่านั้น
-- =====================================================================
SELECT pg_temp.rec_checks('T5', 'ตั้งร้านเป็น ACTIVE โดยไม่มี activated_at → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO merchant (id, name, status) VALUES ('acc_mch_t5', 'ร้าน T5', 'ACTIVE')
  $q$, '23514', 'merchant', ARRAY['activated_at', 'status']));

-- =====================================================================
-- T6 · ร้านหนึ่งมีงาน provision ค้างได้ทีละงานเดียว
--      ต้นฉบับใช้ uq_provision_inflight · partial unique บน subject_id
-- =====================================================================
INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
VALUES ('acc_op_1', 'anon:acc', 'MERCHANT_PROVISION', 'acc_mch_1', '{}');
INSERT INTO outbox (id, operation_id, topic, payload)
VALUES ('acc_obx_1', 'acc_op_1', 'merchant.provision.v1', '{}');

SELECT pg_temp.rec_checks('T6', 'สั่ง provision ร้านเดิมซ้อนกัน → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO operation (id, actor_scope, kind, subject_id, payload)
    VALUES ('acc_op_2', 'anon:acc', 'MERCHANT_PROVISION', 'acc_mch_1', '{}')
  $q$, '23505', 'operation', ARRAY['subject_id']));

-- =====================================================================
-- T7 · topic ของ outbox ต้องมีเวอร์ชันในชื่อ                 RFD 7 D9
--      ต้นฉบับใช้ CHECK (topic ~ '\.v[0-9]+$')
-- =====================================================================
SELECT pg_temp.rec_checks('T7', 'topic ที่ไม่มีเวอร์ชันต่อท้าย → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO outbox (id, operation_id, topic, payload)
    VALUES ('acc_obx_bad', 'acc_op_1', 'merchant.provision', '{}')
  $q$, '23514', 'outbox', ARRAY['topic']));

-- =====================================================================
-- T8 · ห้าม broadcast ก่อน raw_tx ลงตาราง                    RFD 2 R2
-- =====================================================================
INSERT INTO operation_step (id, operation_id, step_index, name)
VALUES ('acc_st_1', 'acc_op_1', 1, 'whitelistMerchant');

SELECT pg_temp.rec_checks('T8', 'เปลี่ยน step เป็น SUBMITTED โดยไม่มี raw_tx → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    UPDATE operation_step SET status = 'SUBMITTED' WHERE id = 'acc_st_1'
  $q$, '23514', 'operation_step', ARRAY['raw_tx', 'status']));

-- =====================================================================
-- T9 · nonce ของ signer ตัวหนึ่งถูกใช้ซ้ำไม่ได้               RFD 4 §3.6
--      ต้นฉบับใช้ uq_signer_nonce · partial unique (signer_address, nonce)
-- =====================================================================
UPDATE operation_step
   SET raw_tx = '\x02f8', signer_address = '0x' || repeat('ac', 20),
       nonce = 41, status = 'SUBMITTED', tx_hash = '0x' || repeat('ab', 32)
 WHERE id = 'acc_st_1';

SELECT pg_temp.rec_checks('T9', 'ใช้ nonce เดิมของ signer ตัวเดิมซ้ำ → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO operation_step (id, operation_id, step_index, name, raw_tx,
                                signer_address, nonce, status, tx_hash)
    VALUES ('acc_st_2', 'acc_op_1', 2, 'setApprovalForAll', '\x02f8',
            '0x' || repeat('ac', 20), 41, 'SUBMITTED', '0x' || repeat('cd', 32))
  $q$, '23505', 'operation_step', ARRAY['nonce', 'signer_address']));

-- =====================================================================
-- T10 · ส่ง raw_tx ไบต์เดิมซ้ำ → ไม่เกิดแถวซ้ำ                RFD 2 D4
--       ต้นฉบับใช้ uq_step_txhash · partial unique บน tx_hash
--       ใช้ชื่อ step ใหม่ ไม่งั้นชน uq_step_name แล้วได้ PASS ปลอม
-- =====================================================================
SELECT pg_temp.rec_checks('T10', 'ส่ง raw_tx ไบต์เดิมซ้ำ → tx_hash ซ้ำถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO operation_step (id, operation_id, step_index, name, raw_tx, status, tx_hash)
    VALUES ('acc_st_3', 'acc_op_1', 3, 'accDuplicateTx', '\x02f8', 'SUBMITTED',
            '0x' || repeat('ab', 32))
  $q$, '23505', 'operation_step', ARRAY['tx_hash']));

-- =====================================================================
-- T11 · wallet หนึ่งใบต่อหนึ่งเจ้าของ · address ซ้ำไม่ได้
-- =====================================================================
INSERT INTO wallet (id, address, key_ref, owner_kind, owner_id)
VALUES ('acc_wal_1', '0x' || repeat('a1', 20), 'kms/acc-m1', 'MERCHANT', 'acc_mch_1');

SELECT pg_temp.rec_checks('T11', 'ร้านเดิมมี wallet ใบที่สองไม่ได้ · address ซ้ำไม่ได้',
  pg_temp.try_reject($q$
    INSERT INTO wallet (id, address, key_ref, owner_kind, owner_id)
    VALUES ('acc_wal_2', '0x' || repeat('a2', 20), 'kms/acc-m1b', 'MERCHANT', 'acc_mch_1')
  $q$, '23505', 'wallet', ARRAY['owner_id', 'owner_kind']),
  pg_temp.try_reject($q$
    INSERT INTO wallet (id, address, key_ref, owner_kind, owner_id)
    VALUES ('acc_wal_3', '0x' || repeat('a1', 20), 'kms/acc-other', 'MERCHANT', 'acc_mch_t4')
  $q$, '23505', 'wallet', ARRAY['address']));

-- =====================================================================
-- T12 · invariant · operation ทุกแถวต้องมี outbox คู่กัน      RFD 2 D1
--       ตรวจทั้งตาราง รวมข้อมูลจริง · FAIL ข้อนี้อาจมาจากข้อมูลเก่า ดู note
-- =====================================================================
DO $$
DECLARE orphan int; sample text;
BEGIN
  SELECT count(*), string_agg(o.id, ',' ORDER BY o.id)
    INTO orphan, sample
  FROM operation o LEFT JOIN outbox b ON b.operation_id = o.id
  WHERE b.id IS NULL;
  PERFORM pg_temp.rec('T12', 'ไม่มี operation ที่ไม่มีใครมาทำ (orphan = 0)',
                      CASE WHEN orphan = 0 THEN 'PASS' ELSE 'FAIL' END,
                      CASE WHEN orphan = 0 THEN NULL
                           ELSE format('orphan %s แถว: %s', orphan, left(sample, 200)) END);
END $$;

-- =====================================================================
-- T13 · invariant · ไม่มีร้าน ACTIVE ที่ยังไม่มี wallet
--       ตรวจทั้งตาราง รวมข้อมูลจริง
-- =====================================================================
DO $$
DECLARE bad int; sample text;
BEGIN
  SELECT count(*), string_agg(m.id, ',' ORDER BY m.id)
    INTO bad, sample
  FROM merchant m
  LEFT JOIN wallet w ON w.owner_kind = 'MERCHANT' AND w.owner_id = m.id
  WHERE m.status = 'ACTIVE' AND w.id IS NULL;
  PERFORM pg_temp.rec('T13', 'ไม่มีร้านที่ ACTIVE แต่ยังไม่มี wallet',
                      CASE WHEN bad = 0 THEN 'PASS' ELSE 'FAIL' END,
                      CASE WHEN bad = 0 THEN NULL
                           ELSE format('%s ร้าน: %s', bad, left(sample, 200)) END);
END $$;

-- =====================================================================
-- T14 · re-index ช่วงเดิมซ้ำได้                              RFD 5 D2
--       0002 มี FK chain_event.block_hash → raw_block จึงต้องมี block ก่อน
--       และ topics เป็น NOT NULL ไม่มี default
-- =====================================================================
DO $$
DECLARE n1 int; n2 int;
BEGIN
  INSERT INTO raw_block (block_hash, block_number, parent_hash, "timestamp")
  VALUES ('0x' || repeat('bb', 32), 1001, '0x' || repeat('ba', 32), now());

  INSERT INTO chain_event (id, block_number, block_hash, tx_hash, log_index, address,
                           topic0, topics, data, block_timestamp)
  VALUES ('acc_ce_1', 1001, '0x' || repeat('bb', 32), '0x' || repeat('ab', 32), 0,
          '0x' || repeat('aa', 20), '0x' || repeat('00', 32), '[]', '\x00', now())
  ON CONFLICT (tx_hash, log_index) DO NOTHING;
  GET DIAGNOSTICS n1 = ROW_COUNT;

  INSERT INTO chain_event (id, block_number, block_hash, tx_hash, log_index, address,
                           topic0, topics, data, block_timestamp)
  VALUES ('acc_ce_2', 1001, '0x' || repeat('bb', 32), '0x' || repeat('ab', 32), 0,
          '0x' || repeat('aa', 20), '0x' || repeat('00', 32), '[]', '\x00', now())
  ON CONFLICT (tx_hash, log_index) DO NOTHING;
  GET DIAGNOSTICS n2 = ROW_COUNT;

  PERFORM pg_temp.rec('T14', 'อ่าน log ช่วงเดิมซ้ำ → chain_event ไม่เกิดแถวซ้ำ',
                      CASE WHEN n1 = 1 AND n2 = 0 THEN 'PASS' ELSE 'FAIL' END,
                      format('แถวแรก %s · แถวที่สอง %s', n1, n2));
EXCEPTION WHEN OTHERS THEN
  PERFORM pg_temp.rec('T14', 'อ่าน log ช่วงเดิมซ้ำ → chain_event ไม่เกิดแถวซ้ำ',
                      'ERROR', SQLSTATE || ' · ' || SQLERRM);
END $$;

-- ------------------------------------------------------------- สรุป
\o
SELECT id AS "#", what AS "ข้อรับประกัน", verdict AS "ผล", coalesce(note, '') AS "หมายเหตุ"
FROM result
ORDER BY lpad(substring(id FROM '[0-9]+'), 3, '0'), id;

SELECT count(*) FILTER (WHERE verdict = 'PASS')  AS "ผ่าน",
       count(*) FILTER (WHERE verdict = 'FAIL')  AS "ไม่ผ่าน",
       count(*) FILTER (WHERE verdict = 'ERROR') AS "test รันไม่ได้"
FROM result;

-- ไม่ commit · ฐานข้อมูลกลับไปเหมือนก่อนรัน
ROLLBACK;
