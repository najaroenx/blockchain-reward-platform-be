-- =====================================================================
-- FEAT-13 · Operation Status + Webhook · acceptance สำหรับ blockchain-reward-platform-be
--
-- ต้นฉบับ : blockchain-reward-platform/reference/feat-13/tests/acceptance.sql (41 ข้อ)
-- schema  : migrations 0002 → 0016_feat13_operation_status
--
-- รัน (Postgres ใน docker):
--   docker exec -i blockchain-reward-platform-be-postgres-1 \
--     psql -U reward -d reward < tests/sql/feat13_acceptance.sql
--
-- ผล  PASS ฐานข้อมูลบังคับกฎนี้ได้จริง · FAIL ไม่ได้ หรือถูกปฏิเสธด้วยกฎตัวอื่น
--     ERROR test เองรันไม่ได้ · ดูหมายเหตุ
--
-- ต่างจากต้นฉบับ · หลักฐานอยู่ใน docs/reference-findings.md
--   1. ฐานข้อมูลเขียน operation_event เองทุกครั้งที่สถานะเปลี่ยน · เขียน/แก้/ลบ event ตรงๆ ไม่ได้
--      T1-T8 จึงพิสูจน์ว่าประวัติครบและต่อเนื่องเพราะเขียนเองไม่ได้ ไม่ใช่เพราะถูกตรวจตอน commit
--      ผู้สั่งและ metadata มาจาก set_config('app.operation_actor' / 'app.operation_event_metadata')
--   2. T11b เพิ่ม · งานใหม่ต้องเริ่มที่ PENDING · T13b · แก้หรือลบประวัติไม่ได้
--   3. T14 เพิ่ม ftp:// · T16 เพิ่ม [""] · event_types เป็น JSON (0002)
--   4. T17b เพิ่ม · ส่ง webhook ของงานร้าน A ไปที่ endpoint ของร้าน B ไม่ได้
--      T17c · endpoint ที่ PAUSED หรือไม่ได้สมัคร event นั้น ไม่ตั้งคิวให้
--   5. T20b เพิ่ม · delivery_seq ข้ามลำดับไม่ได้ (ต้นฉบับปล่อยผ่านเมื่อไม่มีลำดับก่อนหน้า)
--   6. T29b เพิ่ม · แก้ตัวนับ attempt โดยไม่มีแถว attempt ก็ไม่ผ่าน · T29c attempt แก้/ลบไม่ได้
--   7. ข้อมูลทดสอบขึ้นต้นด้วย acc_ · ข้อ "ต้องถูกปฏิเสธ" ตรวจว่าชนกฎที่ตั้งใจจริง
--
-- ทั้งไฟล์อยู่ใน transaction เดียวแล้ว ROLLBACK · รันซ้ำกี่รอบก็ได้
-- =====================================================================
\set ON_ERROR_STOP on
\pset format aligned

BEGIN;

-- ควรได้ 0016_feat13_operation_status ขึ้นไป
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


-- เดินสถานะตามลำดับ · แต่ละขั้นเป็น UPDATE หนึ่งครั้ง
CREATE FUNCTION pg_temp.advance(p_op text, VARIADIC p_statuses text[]) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE s text;
BEGIN
  FOREACH s IN ARRAY p_statuses LOOP
    UPDATE operation SET status = s, finalized_at = CASE WHEN s = 'FINALIZED' THEN now() END WHERE id = p_op;
  END LOOP;
END $$;

CREATE FUNCTION pg_temp.events(p_op text) RETURNS text LANGUAGE sql STABLE AS $$
  SELECT string_agg(seq || ':' || coalesce(from_status, '-') || '>' || to_status, ' ' ORDER BY seq)
    FROM operation_event WHERE operation_id = p_op
$$;

CREATE FUNCTION pg_temp.new_op(p_id text, p_scope text DEFAULT 'merchant:acc_mch_a') RETURNS void
LANGUAGE sql AS $$
  INSERT INTO operation (id, actor_scope, kind, subject_id, payload) VALUES (p_id, p_scope, 'POINT_TRANSFER', 'acc_x', '{}');
  INSERT INTO outbox (id, operation_id, topic, payload) VALUES (p_id || '_o', p_id, 'point.transfer.v1', '{}');
$$;

-- ------------------------------------------------------------ fixture
INSERT INTO merchant (id, name, status, activated_at) VALUES
  ('acc_mch_a', 'ร้าน A', 'ACTIVE', now()), ('acc_mch_b', 'ร้าน B', 'ACTIVE', now());
INSERT INTO webhook_endpoint (id, merchant_id, url, secret_ref, event_types, status) VALUES
  ('acc_ep_a1', 'acc_mch_a', 'https://a.example/hook', 'kms://webhook/acc_mch_a/v1', '["operation.status"]', 'ACTIVE'),
  ('acc_ep_a2', 'acc_mch_a', 'https://a2.example/hook', 'kms://webhook/acc_mch_a/v2', '["operation.status"]', 'ACTIVE'),
  ('acc_ep_b',  'acc_mch_b', 'https://b.example/hook', 'kms://webhook/acc_mch_b/v1', '["operation.status"]', 'ACTIVE'),
  ('acc_ep_p',  'acc_mch_a', 'https://p.example/hook', 'kms://webhook/acc_mch_a/v3', '["operation.status"]', 'PAUSED'),
  ('acc_ep_o',  'acc_mch_a', 'https://o.example/hook', 'kms://webhook/acc_mch_a/v4', '["listing.sold"]', 'ACTIVE');

SELECT pg_temp.new_op('acc_op_1');
-- งานที่มีประวัติ 3 แถวไว้ทดสอบ webhook · PENDING → SUBMITTED → CONFIRMED
SELECT pg_temp.new_op('acc_op_w');
SELECT pg_temp.advance('acc_op_w', 'SUBMITTED', 'CONFIRMED');

CREATE FUNCTION pg_temp.delivery(p_id text, p_ep text, p_op text, p_event int, p_seq int,
                                 p_replay_of text DEFAULT NULL, p_type text DEFAULT 'operation.status') RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT format($q$INSERT INTO webhook_delivery (id, endpoint_id, operation_id, event_seq, delivery_seq, event_type,
                                                 payload, signature, signed_at, replay_of)
                   VALUES (%L, %L, %L, %s, %s, %L, '{"event": %s}', %L, now(), %L)$q$,
                p_id, p_ep, p_op, p_event, p_seq, p_type, p_event, 'sha256=' || md5(p_id), p_replay_of)
$$;

-- =====================================================================
-- T1 · งานใหม่มี event แถวแรก from_status = NULL → PENDING (ฐานข้อมูลเขียนเอง)
-- T2-T5 · เขียน event ตรงๆ ไม่ได้ ไม่ว่าจะเป็นแถวแรกซ้ำ ข้าม seq หรือ from_status ที่โกหก
-- T6 · เปลี่ยนสถานะแล้ว event เกิดเองทุกครั้ง · ไม่มีทางเปลี่ยนเงียบๆ
-- T7 · UPDATE ที่สถานะไม่เปลี่ยน ไม่เกิด event · เขียน event โดยสถานะไม่เปลี่ยนไม่ได้
-- T8 · ผู้สั่งและ metadata ไปถึง event ผ่าน set_config
-- =====================================================================
DO $$
DECLARE v text;
BEGIN
  v := pg_temp.events('acc_op_1');
  PERFORM pg_temp.rec('T1', 'งานใหม่มี event แถวแรก (seq 1 · from NULL → PENDING) โดยไม่ต้องเขียนเอง',
                      CASE WHEN v = '1:->PENDING' THEN 'PASS' ELSE 'FAIL' END, coalesce(v, 'ไม่มี event'));
END $$;

CREATE FUNCTION pg_temp.event_row(p_id text, p_seq int, p_from text, p_to text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT format($q$INSERT INTO operation_event (id, operation_id, seq, from_status, to_status, metadata)
                   VALUES (%L, 'acc_op_1', %s, %L, %L, '{}')$q$, p_id, p_seq, p_from, p_to)
$$;

SELECT pg_temp.rec_checks('T2', 'เขียน event แถวที่สองเอง → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.event_row('acc_ev_2', 2, 'PENDING', 'SUBMITTED'),
    '23514', 'operation_event', NULL, ARRAY['trg_operation_event_guard']));
SELECT pg_temp.rec_checks('T3', 'เขียน event seq 1 ซ้ำ → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.event_row('acc_ev_3', 1, NULL, 'PENDING'),
    '23514', 'operation_event', NULL, ARRAY['trg_operation_event_guard']));
SELECT pg_temp.rec_checks('T4', 'เขียน event ข้าม seq → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.event_row('acc_ev_4', 5, 'PENDING', 'SUBMITTED'),
    '23514', 'operation_event', NULL, ARRAY['trg_operation_event_guard']));
SELECT pg_temp.rec_checks('T5', 'เขียน event ที่ from_status ไม่ตรงกับความจริง → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.event_row('acc_ev_5', 2, 'CONFIRMED', 'FINALIZED'),
    '23514', 'operation_event', NULL, ARRAY['trg_operation_event_guard']));

DO $$
DECLARE r text; v text;
BEGIN
  r := pg_temp.try_ok($q$UPDATE operation SET status = 'SUBMITTED' WHERE id = 'acc_op_1'$q$);
  v := pg_temp.events('acc_op_1');
  PERFORM pg_temp.rec_checks('T6', 'เปลี่ยนสถานะ → event เกิดเองทุกครั้ง (ไม่มีทางเปลี่ยนเงียบๆ)', r,
    CASE WHEN v = '1:->PENDING 2:PENDING>SUBMITTED' THEN NULL ELSE 'FAIL: ' || coalesce(v, 'ไม่มี') END);
END $$;

DO $$
DECLARE r text; v text;
BEGIN
  r := pg_temp.try_ok($q$UPDATE operation SET error_note = 'note' WHERE id = 'acc_op_1'$q$);
  v := pg_temp.events('acc_op_1');
  PERFORM pg_temp.rec_checks('T7', 'UPDATE ที่สถานะไม่เปลี่ยน → ไม่เกิด event · เขียน event ลอยๆ → ถูกปฏิเสธ', r,
    CASE WHEN v = '1:->PENDING 2:PENDING>SUBMITTED' THEN NULL ELSE 'FAIL: ' || coalesce(v, 'ไม่มี') END,
    pg_temp.try_reject(pg_temp.event_row('acc_ev_7', 3, 'SUBMITTED', 'SUBMITTED'),
      '23514', 'operation_event', NULL, ARRAY['trg_operation_event_guard']));
END $$;

DO $$
DECLARE r text; v_actor text; v_by text;
BEGIN
  r := pg_temp.try_ok($q$
    SELECT set_config('app.operation_actor', 'MERCHANT_ADMIN', true),
           set_config('app.operation_event_metadata', '{"by": "acc_admin_9"}', true);
    UPDATE operation SET status = 'CONFIRMED' WHERE id = 'acc_op_1';
    SELECT set_config('app.operation_actor', '', true), set_config('app.operation_event_metadata', '', true);
  $q$);
  SELECT actor, metadata ->> 'by' INTO v_actor, v_by FROM operation_event WHERE operation_id = 'acc_op_1' AND seq = 3;
  PERFORM pg_temp.rec_checks('T8', 'เปลี่ยนสถานะพร้อมบอกผู้สั่ง → event ได้ actor และ metadata ครบ', r,
    CASE WHEN v_actor = 'MERCHANT_ADMIN' AND v_by = 'acc_admin_9' THEN NULL
         ELSE format('FAIL: actor %s · by %s', v_actor, v_by) END);
END $$;

-- =====================================================================
-- T9  · เส้นทางปกติ PENDING → SUBMITTED → CONFIRMED → FINALIZED
-- T10 · FINALIZED เป็นปลายทาง · T11 · PENDING ข้ามไป FINALIZED ไม่ได้ · T11b · งานใหม่ต้องเริ่ม PENDING
-- T12 · reorg · CONFIRMED → ORPHANED → SUBMITTED → CONFIRMED → FINALIZED
-- T13 · seq เดินหน้าเสมอแม้สถานะถอย · from_status ต่อกันทุกแถว
-- T13b · แก้หรือลบประวัติไม่ได้
-- =====================================================================
SELECT pg_temp.new_op('acc_op_9');
SELECT pg_temp.rec_checks('T9', 'เส้นทางปกติเดินได้ครบ PENDING → SUBMITTED → CONFIRMED → FINALIZED',
  pg_temp.try_ok($q$SELECT pg_temp.advance('acc_op_9', 'SUBMITTED', 'CONFIRMED', 'FINALIZED')$q$));
SELECT pg_temp.rec_checks('T10', 'FINALIZED → SUBMITTED / ORPHANED → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE operation SET status = 'SUBMITTED', finalized_at = NULL WHERE id = 'acc_op_9'$q$,
    '23514', 'operation', NULL, ARRAY['trg_operation_status_transition']),
  pg_temp.try_reject($q$UPDATE operation SET status = 'ORPHANED', finalized_at = NULL WHERE id = 'acc_op_9'$q$,
    '23514', 'operation', NULL, ARRAY['trg_operation_status_transition']));

SELECT pg_temp.new_op('acc_op_11');
SELECT pg_temp.rec_checks('T11', 'PENDING ข้ามไป FINALIZED / CHAIN_REVERTED → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE operation SET status = 'FINALIZED', finalized_at = now() WHERE id = 'acc_op_11'$q$,
    '23514', 'operation', NULL, ARRAY['trg_operation_status_transition']),
  pg_temp.try_reject($q$UPDATE operation SET status = 'CHAIN_REVERTED' WHERE id = 'acc_op_11'$q$,
    '23514', 'operation', NULL, ARRAY['trg_operation_status_transition']));
SELECT pg_temp.rec_checks('T11b', 'สร้างงานใหม่ที่สถานะ FINALIZED ทันที → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO operation (id, actor_scope, kind, status, payload, finalized_at)
                        VALUES ('acc_op_born', 'merchant:acc_mch_a', 'POINT_TRANSFER', 'FINALIZED', '{}', now())$q$,
    '23514', 'operation', NULL, ARRAY['trg_operation_status_transition']));

SELECT pg_temp.new_op('acc_op_12');
SELECT pg_temp.rec_checks('T12', 'reorg · CONFIRMED → ORPHANED → SUBMITTED → CONFIRMED → FINALIZED ได้',
  pg_temp.try_ok($q$SELECT pg_temp.advance('acc_op_12', 'SUBMITTED', 'CONFIRMED', 'ORPHANED', 'SUBMITTED',
                                            'CONFIRMED', 'FINALIZED')$q$));

DO $$
DECLARE v text; bad int;
BEGIN
  v := pg_temp.events('acc_op_12');
  SELECT count(*) INTO bad FROM operation_event e
    LEFT JOIN operation_event p ON p.operation_id = e.operation_id AND p.seq = e.seq - 1
   WHERE e.operation_id = 'acc_op_12' AND e.seq > 1 AND (p.id IS NULL OR p.to_status IS DISTINCT FROM e.from_status);
  PERFORM pg_temp.rec('T13', 'seq 1-7 ต่อกัน แม้สถานะถอย (CONFIRMED → ORPHANED) · from_status ตรงกับแถวก่อนทุกแถว',
    CASE WHEN v = '1:->PENDING 2:PENDING>SUBMITTED 3:SUBMITTED>CONFIRMED 4:CONFIRMED>ORPHANED '
                  '5:ORPHANED>SUBMITTED 6:SUBMITTED>CONFIRMED 7:CONFIRMED>FINALIZED' AND bad = 0
         THEN 'PASS' ELSE 'FAIL' END, v);
END $$;

SELECT pg_temp.rec_checks('T13b', 'แก้หรือลบ event → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE operation_event SET to_status = 'FINALIZED' WHERE operation_id = 'acc_op_1' AND seq = 2$q$,
    '23514', 'operation_event', NULL, ARRAY['trg_operation_event_guard']),
  pg_temp.try_reject($q$DELETE FROM operation_event WHERE operation_id = 'acc_op_1' AND seq = 2$q$,
    '23514', 'operation_event', NULL, ARRAY['trg_operation_event_guard']));

-- =====================================================================
-- T14 · endpoint ต้องเป็น https · T15 · L6 · secret_ref ต้องเป็น kms:// ไม่ใช่ค่าความลับ
-- T16 · event_types ต้องเป็นรายการชื่อที่ไม่ว่าง
-- =====================================================================
CREATE FUNCTION pg_temp.endpoint(p_id text, p_url text, p_secret text, p_types text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT format($q$INSERT INTO webhook_endpoint (id, merchant_id, url, secret_ref, event_types)
                   VALUES (%L, 'acc_mch_a', %L, %L, %L)$q$, p_id, p_url, p_secret, p_types)
$$;

SELECT pg_temp.rec_checks('T14', 'endpoint ที่เป็น http:// หรือ ftp:// → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.endpoint('acc_ep_x1', 'http://a.example/hook', 'kms://w/a', '["operation.status"]'),
    '23514', 'webhook_endpoint', NULL, ARRAY['ck_https_only', 'ck_webhook_endpoint_url_https']),
  pg_temp.try_reject(pg_temp.endpoint('acc_ep_x2', 'ftp://a.example/hook', 'kms://w/a', '["operation.status"]'),
    '23514', 'webhook_endpoint', NULL, ARRAY['ck_webhook_endpoint_url_https']));
SELECT pg_temp.rec_checks('T15', 'secret_ref เป็นค่าความลับดิบ หรือ kms:// ที่ตามด้วยของแปลก → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.endpoint('acc_ep_x3', 'https://a.example/h', 'whsec_9f8e7d6c5b4a', '["operation.status"]'),
    '23514', 'webhook_endpoint', NULL, ARRAY['ck_webhook_endpoint_secret_ref']),
  pg_temp.try_reject(pg_temp.endpoint('acc_ep_x4', 'https://a.example/h', 'kms://' || repeat('a', 300), '["operation.status"]'),
    '23514', 'webhook_endpoint', NULL, ARRAY['ck_webhook_endpoint_secret_ref']));
SELECT pg_temp.rec_checks('T16', 'event_types [] / [null] / [""] / ไม่ใช่รายการ → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.endpoint('acc_ep_x5', 'https://a.example/h', 'kms://w/a', '[]'),
    '23514', 'webhook_endpoint', NULL, ARRAY['ck_webhook_endpoint_event_types']),
  pg_temp.try_reject(pg_temp.endpoint('acc_ep_x6', 'https://a.example/h', 'kms://w/a', '[null]'),
    '23514', 'webhook_endpoint', NULL, ARRAY['ck_webhook_endpoint_event_types']),
  pg_temp.try_reject(pg_temp.endpoint('acc_ep_x7', 'https://a.example/h', 'kms://w/a', '[""]'),
    '23514', 'webhook_endpoint', NULL, ARRAY['ck_webhook_endpoint_event_types']),
  pg_temp.try_reject(pg_temp.endpoint('acc_ep_x8', 'https://a.example/h', 'kms://w/a', '"operation.status"'),
    '23514', 'webhook_endpoint', NULL, ARRAY['ck_webhook_endpoint_event_types']));

-- =====================================================================
-- T17  · ส่ง webhook เรื่อง event ที่ไม่มีอยู่ → ถูกปฏิเสธ
-- T17b · ส่งงานของร้าน A ไปที่ endpoint ของร้าน B → ถูกปฏิเสธ
-- T17c · endpoint ที่ PAUSED หรือไม่ได้สมัคร event ชนิดนั้น → ไม่ตั้งคิว
-- T18  · event เดียวส่งครั้งแรกไป endpoint เดิมซ้ำ → ถูกปฏิเสธ
-- T19  · event เดียวไปหลาย endpoint ได้
-- T20  · delivery_seq ซ้ำ → ถูกปฏิเสธ · T20b · ข้าม delivery_seq → ถูกปฏิเสธ
-- =====================================================================
SELECT pg_temp.rec_checks('T17', 'ส่ง webhook ของ event seq 9 ที่ไม่มีอยู่ → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.delivery('acc_dl_x', 'acc_ep_a1', 'acc_op_w', 9, 1),
    '23514', 'webhook_delivery', NULL, ARRAY['trg_delivery_admissible']));
SELECT pg_temp.rec_checks('T17b', 'ส่งงานของร้าน A ไป endpoint ของร้าน B → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.delivery('acc_dl_b', 'acc_ep_b', 'acc_op_w', 1, 1),
    '23514', 'webhook_delivery', NULL, ARRAY['trg_delivery_admissible']));
SELECT pg_temp.rec_checks('T17c', 'endpoint ที่ PAUSED / ไม่ได้สมัคร event ชนิดนี้ → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.delivery('acc_dl_p', 'acc_ep_p', 'acc_op_w', 1, 1),
    '23514', 'webhook_delivery', NULL, ARRAY['trg_delivery_admissible']),
  pg_temp.try_reject(pg_temp.delivery('acc_dl_o', 'acc_ep_o', 'acc_op_w', 1, 1),
    '23514', 'webhook_delivery', NULL, ARRAY['trg_delivery_admissible']));

SELECT pg_temp.try_ok(pg_temp.delivery('acc_dl_1', 'acc_ep_a1', 'acc_op_w', 1, 1));
SELECT pg_temp.try_ok(pg_temp.delivery('acc_dl_2', 'acc_ep_a1', 'acc_op_w', 2, 2));
SELECT pg_temp.try_ok(pg_temp.delivery('acc_dl_3', 'acc_ep_a1', 'acc_op_w', 3, 3));

SELECT pg_temp.rec_checks('T18', 'event seq 1 ส่งครั้งแรกไป endpoint เดิมซ้ำ → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.delivery('acc_dl_dup', 'acc_ep_a1', 'acc_op_w', 1, 4),
    '23505', 'webhook_delivery', ARRAY['endpoint_id', 'event_seq', 'operation_id'], ARRAY['uq_delivery_event']));
SELECT pg_temp.rec_checks('T19', 'event เดียวกันไป endpoint ที่สองของร้านเดียวกันได้',
  pg_temp.try_ok(pg_temp.delivery('acc_dl_a2', 'acc_ep_a2', 'acc_op_w', 1, 1)));
SELECT pg_temp.rec_checks('T20', 'delivery_seq ซ้ำต่อ endpoint และงานเดียวกัน → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.delivery('acc_dl_s', 'acc_ep_a2', 'acc_op_w', 2, 1),
    '23505', 'webhook_delivery', ARRAY['delivery_seq', 'endpoint_id', 'operation_id'], ARRAY['uq_delivery_seq']));
SELECT pg_temp.rec_checks('T20b', 'delivery_seq ข้ามจาก 1 ไป 3 → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.delivery('acc_dl_g', 'acc_ep_a2', 'acc_op_w', 3, 3),
    '23514', 'webhook_delivery', NULL, ARRAY['trg_delivery_order_gate']));

-- =====================================================================
-- T21 · ยิงลำดับ 2 ก่อนลำดับ 1 จบ → ถูกปฏิเสธ · T22 · ลำดับ 1 SENT แล้ว ลำดับ 2 ยิงได้
-- T23 · ลำดับ 2 DEAD ไม่บล็อกลำดับ 3 · T24 · คิวของ endpoint หนึ่งไม่บล็อก endpoint อื่น
-- =====================================================================
SELECT pg_temp.rec_checks('T21', 'ยิงลำดับ 2 ขณะลำดับ 1 ยัง PENDING → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE webhook_delivery SET status = 'IN_FLIGHT', claimed_at = now() WHERE id = 'acc_dl_2'$q$,
    '23514', 'webhook_delivery', NULL, ARRAY['trg_delivery_order_gate']));

DO $$
DECLARE r text[];
BEGIN
  r[1] := pg_temp.try_ok($q$
    UPDATE webhook_delivery SET status = 'IN_FLIGHT', claimed_at = now() WHERE id = 'acc_dl_1';
    INSERT INTO webhook_delivery_attempt (id, delivery_id, attempt, started_at, finished_at, response_status)
    VALUES ('acc_at_1', 'acc_dl_1', 1, now(), now(), 200);
    UPDATE webhook_delivery SET status = 'SENT', sent_at = now(), attempt = 1 WHERE id = 'acc_dl_1';
  $q$);
  r[2] := pg_temp.try_ok($q$UPDATE webhook_delivery SET status = 'IN_FLIGHT', claimed_at = now() WHERE id = 'acc_dl_2'$q$);
  PERFORM pg_temp.rec_checks('T22', 'ลำดับ 1 SENT แล้ว ลำดับ 2 ยิงได้', VARIADIC r);
END $$;

DO $$
DECLARE r text[];
BEGIN
  r[1] := pg_temp.try_ok($q$
    INSERT INTO webhook_delivery_attempt (id, delivery_id, attempt, started_at, finished_at, error_note)
    VALUES ('acc_at_2', 'acc_dl_2', 1, now(), now(), 'timeout');
    UPDATE webhook_delivery SET status = 'DEAD', dead_at = now(), attempt = 1 WHERE id = 'acc_dl_2';
  $q$);
  r[2] := pg_temp.try_ok($q$UPDATE webhook_delivery SET status = 'IN_FLIGHT', claimed_at = now() WHERE id = 'acc_dl_3'$q$);
  PERFORM pg_temp.rec_checks('T23', 'ลำดับ 2 DEAD → ลำดับ 3 ยังยิงได้', VARIADIC r);
END $$;

SELECT pg_temp.rec_checks('T24', 'endpoint a2 ยิงลำดับ 1 ของตัวเองได้ แม้ a1 ยังมีงานค้าง',
  pg_temp.try_ok($q$UPDATE webhook_delivery SET status = 'IN_FLIGHT', claimed_at = now() WHERE id = 'acc_dl_a2'$q$));

-- =====================================================================
-- T24b · ส่งซ้ำแถว DEAD ได้ · เป็นแถวใหม่ชี้กลับของเดิม
-- T24c · ส่งซ้ำแถวที่ยังไม่ DEAD → ถูกปฏิเสธ · T24d · ส่งซ้ำแต่อ้าง event อื่น → ถูกปฏิเสธ
-- T24e · ส่งครั้งแรกของ event เดิมซ้ำ (replay_of NULL) → ยังถูกปฏิเสธ
-- =====================================================================
SELECT pg_temp.rec_checks('T24b', 'ส่งซ้ำ delivery ที่ DEAD เป็นแถวใหม่ (ลำดับถัดไป) ได้',
  pg_temp.try_ok(pg_temp.delivery('acc_dl_r', 'acc_ep_a1', 'acc_op_w', 2, 4, 'acc_dl_2')));
SELECT pg_temp.rec_checks('T24c', 'ส่งซ้ำ delivery ที่ SENT → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.delivery('acc_dl_r2', 'acc_ep_a1', 'acc_op_w', 1, 5, 'acc_dl_1'),
    '23514', 'webhook_delivery', NULL, ARRAY['trg_replay_valid']));
SELECT pg_temp.rec_checks('T24d', 'ส่งซ้ำ delivery ที่ DEAD แต่อ้าง event อื่น → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.delivery('acc_dl_r3', 'acc_ep_a1', 'acc_op_w', 3, 5, 'acc_dl_2'),
    '23514', 'webhook_delivery', NULL, ARRAY['trg_replay_valid']));
SELECT pg_temp.rec_checks('T24e', 'ส่ง event seq 2 ครั้งแรกซ้ำโดยไม่บอกว่าเป็นการส่งซ้ำ → ถูกปฏิเสธ',
  pg_temp.try_reject(pg_temp.delivery('acc_dl_r4', 'acc_ep_a1', 'acc_op_w', 2, 5),
    '23505', 'webhook_delivery', ARRAY['endpoint_id', 'event_seq', 'operation_id'], ARRAY['uq_delivery_event']));

-- =====================================================================
-- T25 · delivery ที่ SENT แล้วแก้ไม่ได้ · T26 · แก้ payload / ลายเซ็นของ delivery ที่ยังไม่ส่งไม่ได้
-- =====================================================================
SELECT pg_temp.rec_checks('T25', 'แก้ delivery ที่ SENT / DEAD → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE webhook_delivery SET next_attempt_at = now() WHERE id = 'acc_dl_1'$q$,
    '23514', 'webhook_delivery', NULL, ARRAY['trg_delivery_immutable']),
  pg_temp.try_reject($q$UPDATE webhook_delivery SET status = 'PENDING', dead_at = NULL WHERE id = 'acc_dl_2'$q$,
    '23514', 'webhook_delivery', NULL, ARRAY['trg_delivery_immutable']));
SELECT pg_temp.rec_checks('T26', 'แก้ payload / signature ของ delivery ที่ยังไม่ส่ง → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE webhook_delivery SET payload = '{"event": 99}' WHERE id = 'acc_dl_r'$q$,
    '23514', 'webhook_delivery', NULL, ARRAY['trg_delivery_immutable']),
  pg_temp.try_reject($q$UPDATE webhook_delivery SET signature = 'sha256=x' WHERE id = 'acc_dl_r'$q$,
    '23514', 'webhook_delivery', NULL, ARRAY['trg_delivery_immutable']));

-- =====================================================================
-- T27 T28 · attempt ต้องมีคำตอบหรือ error อย่างใดอย่างหนึ่งพอดี
-- T29 · เพิ่มแถว attempt โดยไม่ขยับตัวนับ → ไม่ผ่านตอน commit
-- T29b · ขยับตัวนับโดยไม่มีแถว attempt → ไม่ผ่านตอน commit (ต้นฉบับไม่ตรวจทางนี้)
-- T29c · attempt แก้หรือลบไม่ได้
-- T30 · backoff ทวีคูณ 1 · 2 · 4 · 8 นาที · เพดาน 60
-- =====================================================================
SELECT pg_temp.rec_checks('T27', 'attempt ที่ไม่มีทั้งคำตอบและ error → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO webhook_delivery_attempt (id, delivery_id, attempt, started_at, finished_at)
                        VALUES ('acc_at_x', 'acc_dl_r', 1, now(), now())$q$,
    '23514', 'webhook_delivery_attempt', NULL, ARRAY['ck_attempt_outcome']));
SELECT pg_temp.rec_checks('T28', 'attempt ที่มีทั้งคำตอบและ error → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO webhook_delivery_attempt (id, delivery_id, attempt, started_at, finished_at,
                                                              response_status, error_note)
                        VALUES ('acc_at_y', 'acc_dl_r', 1, now(), now(), 500, 'x')$q$,
    '23514', 'webhook_delivery_attempt', NULL, ARRAY['ck_attempt_outcome']));

SELECT pg_temp.try_ok('SET CONSTRAINTS ALL IMMEDIATE');
SELECT pg_temp.try_ok('SET CONSTRAINTS ALL DEFERRED');

SELECT pg_temp.rec_checks('T29', 'เพิ่ม attempt โดยตัวนับยังเป็น 0 → ไม่ผ่าน',
  pg_temp.try_reject($q$
    INSERT INTO webhook_delivery_attempt (id, delivery_id, attempt, started_at, finished_at, response_status)
    VALUES ('acc_at_z', 'acc_dl_r', 1, now(), now(), 503);
    SET CONSTRAINTS trg_attempt_count_matches IMMEDIATE;
  $q$, '23514', 'webhook_delivery_attempt', NULL, ARRAY['trg_attempt_count_matches']));
SELECT pg_temp.try_ok('SET CONSTRAINTS trg_attempt_count_matches DEFERRED');
SELECT pg_temp.rec_checks('T29b', 'ขยับตัวนับเป็น 2 โดยไม่มีแถว attempt → ไม่ผ่าน',
  pg_temp.try_reject($q$
    UPDATE webhook_delivery SET attempt = 2 WHERE id = 'acc_dl_r';
    SET CONSTRAINTS trg_attempt_count_matches IMMEDIATE;
  $q$, '23514', 'webhook_delivery', NULL, ARRAY['trg_attempt_count_matches']));
SELECT pg_temp.try_ok('SET CONSTRAINTS trg_attempt_count_matches DEFERRED');
SELECT pg_temp.rec_checks('T29c', 'แก้หรือลบ attempt → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE webhook_delivery_attempt SET error_note = 'edited' WHERE id = 'acc_at_2'$q$,
    '23514', 'webhook_delivery_attempt', NULL, ARRAY['trg_attempt_append_only']),
  pg_temp.try_reject($q$DELETE FROM webhook_delivery_attempt WHERE id = 'acc_at_2'$q$,
    '23514', 'webhook_delivery_attempt', NULL, ARRAY['trg_attempt_append_only']));

DO $$
DECLARE v text;
BEGIN
  SELECT string_agg(extract(epoch FROM f_backoff(n))::int / 60 || '', ',' ORDER BY n) INTO v
    FROM generate_series(1, 8) n;
  PERFORM pg_temp.rec('T30', 'backoff 1,2,4,8,16,32,60,60 นาที', CASE WHEN v = '1,2,4,8,16,32,60,60' THEN 'PASS' ELSE 'FAIL' END, v);
EXCEPTION WHEN OTHERS THEN
  PERFORM pg_temp.rec('T30', 'backoff 1,2,4,8,16,32,60,60 นาที', 'ERROR', SQLSTATE || ' · ' || SQLERRM);
END $$;

-- =====================================================================
-- T31 · Group E · ไม่มี foreign key จากตาราง webhook ไปหา chain_event (สร้างใหม่จาก chain ไม่ได้)
-- T32 · ค่าที่เก็บเป็นสิ่งที่ chain ไม่รู้ · attempt มีคำตอบของร้านและเวลาที่ใช้
-- T33 · คิวงานหยิบเฉพาะที่ถึงเวลาและยัง PENDING
-- T34 · IN_FLIGHT ต้องมี claimed_at · T35 · SENT ต้องมี sent_at · DEAD ต้องมี dead_at
-- T36 · outbox topic ต้องมีเวอร์ชัน
-- =====================================================================
DO $$
DECLARE n int;
BEGIN
  SELECT count(*) INTO n FROM pg_constraint
   WHERE contype = 'f' AND confrelid = 'chain_event'::regclass
     AND conrelid IN ('webhook_endpoint'::regclass, 'webhook_delivery'::regclass,
                      'webhook_delivery_attempt'::regclass);
  PERFORM pg_temp.rec('T31', 'ไม่มี FK จาก webhook_endpoint / delivery / attempt ไปหา chain_event',
                      CASE WHEN n = 0 THEN 'PASS' ELSE 'FAIL' END, format('%s FK', n));
END $$;

DO $$
DECLARE n int;
BEGIN
  SELECT count(*) INTO n FROM information_schema.columns
   WHERE table_schema = current_schema() AND table_name = 'webhook_delivery_attempt'
     AND column_name IN ('response_status', 'response_body', 'started_at', 'finished_at', 'error_note');
  PERFORM pg_temp.rec('T32', 'attempt เก็บคำตอบของร้านและเวลา · สิ่งที่ chain ไม่มีทางรู้',
                      CASE WHEN n = 5 THEN 'PASS' ELSE 'FAIL' END, format('%s/5 คอลัมน์', n));
END $$;

DO $$
DECLARE v text;
BEGIN
  SELECT string_agg(id, ',' ORDER BY id) INTO v FROM webhook_delivery
   WHERE status = 'PENDING' AND next_attempt_at <= now() AND id LIKE 'acc_%';
  PERFORM pg_temp.rec('T33', 'คิวหยิบเฉพาะแถว PENDING ที่ถึงเวลา (acc_dl_r)',
                      CASE WHEN v = 'acc_dl_r' THEN 'PASS' ELSE 'FAIL' END, v);
END $$;

SELECT pg_temp.rec_checks('T34', 'IN_FLIGHT โดยไม่มี claimed_at → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE webhook_delivery SET status = 'IN_FLIGHT' WHERE id = 'acc_dl_r'$q$,
    '23514', 'webhook_delivery', NULL, ARRAY['ck_delivery_claim']));
SELECT pg_temp.rec_checks('T35', 'SENT โดยไม่มี sent_at / DEAD โดยไม่มี dead_at → ถูกปฏิเสธ',
  pg_temp.try_reject($q$UPDATE webhook_delivery SET status = 'SENT' WHERE id = 'acc_dl_3'$q$,
    '23514', 'webhook_delivery', NULL, ARRAY['ck_delivery_sent']),
  pg_temp.try_reject($q$UPDATE webhook_delivery SET status = 'DEAD' WHERE id = 'acc_dl_3'$q$,
    '23514', 'webhook_delivery', NULL, ARRAY['ck_delivery_dead']));
SELECT pg_temp.rec_checks('T36', 'outbox topic ไม่มี .vN → ถูกปฏิเสธ',
  pg_temp.try_reject($q$INSERT INTO outbox (id, operation_id, topic, payload) VALUES ('acc_obx_bad', 'acc_op_1', 'op.status', '{}')$q$,
    '23514', 'outbox', NULL, ARRAY['ck_outbox_topic_version']));

-- =====================================================================
-- T37 · ปิดท้าย · ตรวจตอน commit ผ่าน · ทุก operation (ทั้งฐาน) สถานะตรงกับ event แถวสุดท้าย
-- =====================================================================
DO $$
DECLARE r text; v_bad text;
BEGIN
  r := pg_temp.try_ok('SET CONSTRAINTS ALL IMMEDIATE');
  SELECT string_agg(o.id, ',') INTO v_bad FROM operation o
   WHERE o.status IS DISTINCT FROM (SELECT e.to_status FROM operation_event e
                                     WHERE e.operation_id = o.id ORDER BY e.seq DESC LIMIT 1);
  PERFORM pg_temp.rec_checks('T37', 'ทุก operation สถานะตรงกับ event แถวสุดท้าย · commit ผ่าน', r,
    CASE WHEN v_bad IS NULL THEN NULL ELSE 'FAIL: ' || left(v_bad, 200) END);
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
