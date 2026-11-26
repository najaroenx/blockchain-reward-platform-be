-- =====================================================================
-- FEAT-03 · Auth (login · token refresh) · acceptance สำหรับ blockchain-reward-platform-be
--
-- ต้นฉบับ : blockchain-reward-platform/reference/feat-03/tests/acceptance.sql (15 ข้อ)
-- schema  : migrations 0002 → 0005_feat03_revoke_reason
--
-- รัน (Postgres ใน docker):
--   docker exec -i blockchain-reward-platform-be-postgres-1 \
--     psql -U reward -d reward < tests/sql/feat03_acceptance.sql
--
-- ผล  PASS ฐานข้อมูลบังคับกฎนี้ได้จริง · FAIL ไม่ได้ หรือถูกปฏิเสธด้วยกฎตัวอื่น
--     ERROR test เองรันไม่ได้ · ดูหมายเหตุ
--
-- ต่างจากต้นฉบับ
--   1. hash เป็น hex string ('0x…') ตาม 0002
--   2. customer สร้างผ่าน challenge ที่ VERIFIED (FEAT-02 F7 · migration 0004)
--   3. ข้อ "ต้องถูกปฏิเสธ" ตรวจว่าชนกฎที่ตั้งใจทดสอบจริง และย้อนแถวทิ้งถ้าไม่ถูกห้าม
--      T5 ต้นฉบับยอมรับ check_violation ใดก็ได้ · ที่นี่แยกว่าชน ck_one_subject
--   4. เพิ่ม T4b · revoked_reason ต้องเป็นค่าที่รู้จัก (migration 0005)
--   5. T14 T15 ตรวจทั้งตาราง รวมข้อมูลจริง · FAIL อาจมาจากข้อมูลเก่า ดูหมายเหตุ
--   6. ข้อมูลทดสอบขึ้นต้นด้วย acc_
--
-- ทั้งไฟล์อยู่ใน transaction เดียวแล้ว ROLLBACK · รันซ้ำกี่รอบก็ได้
-- =====================================================================
\set ON_ERROR_STOP on
\pset format aligned

BEGIN;

-- ควรได้ 0005_feat03_revoke_reason ขึ้นไป
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

-- ------------------------------------------------------------ fixture
INSERT INTO merchant (id, name) VALUES ('acc_mch_1', 'ร้านทดสอบ');
INSERT INTO merchant_admin (id, merchant_id, email, email_normalized, password_hash, display_name)
VALUES ('acc_adm_1', 'acc_mch_1', 'acc-owner@shop.co.th', 'acc-owner@shop.co.th',
        '$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHQ$aaaa', 'เจ้าของร้าน');

-- ลูกค้ามีได้ผ่าน challenge ที่ยืนยันแล้วเท่านั้น (FEAT-02 F7)
INSERT INTO otp_challenge (id, phone_e164, code_hash, pepper_ref, created_at, expires_at)
VALUES ('acc_ch_1', '+66819993001', pg_temp.h('acc_ch_1:123456'), 'kms://otp-pepper/v1',
        now() - interval '1 minute', now() + interval '4 minutes');
UPDATE otp_challenge SET status = 'VERIFIED', verified_at = now(), attempts = 1
 WHERE id = 'acc_ch_1';
INSERT INTO customer (id, phone_e164, phone_verified_at, verified_challenge_id, tel_hash)
VALUES ('acc_cus_1', '+66819993001', now(), 'acc_ch_1', sha256('0819993001'));

-- session ใบแรกจากการ login
INSERT INTO auth_session (id, subject_kind, merchant_admin_id, family_id,
                          refresh_token_hash, expires_at)
VALUES ('acc_ses_1', 'MERCHANT_ADMIN', 'acc_adm_1', 'acc_fam_1', pg_temp.h('acc-tok-1'),
        now() + interval '30 days');

-- =====================================================================
-- T1 · refresh token หนึ่งใบมีได้แถวเดียว
-- =====================================================================
SELECT pg_temp.rec_checks('T1', 'refresh token ค่าเดียวกันเกิดสองแถวไม่ได้',
  pg_temp.try_reject($q$
    INSERT INTO auth_session (id, subject_kind, merchant_admin_id, family_id,
                              refresh_token_hash, expires_at)
    VALUES ('acc_ses_dup', 'MERCHANT_ADMIN', 'acc_adm_1', 'acc_fam_x', pg_temp.h('acc-tok-1'),
            now() + interval '30 days')
  $q$, '23505', 'auth_session', ARRAY['refresh_token_hash']));

-- =====================================================================
-- T2 · token หนึ่งใบหมุนได้ครั้งเดียว · นี่คือการกันการใช้ซ้ำ
--      เอาใบที่หมุนไปแล้วมาหมุนอีก → ลูกใบที่สอง INSERT ไม่ผ่าน
-- =====================================================================
UPDATE auth_session SET used_at = now(), revoked_at = now(), revoked_reason = 'ROTATED'
 WHERE id = 'acc_ses_1';
INSERT INTO auth_session (id, subject_kind, merchant_admin_id, family_id,
                          refresh_token_hash, parent_id, expires_at)
VALUES ('acc_ses_2', 'MERCHANT_ADMIN', 'acc_adm_1', 'acc_fam_1', pg_temp.h('acc-tok-2'),
        'acc_ses_1', now() + interval '30 days');

SELECT pg_temp.rec_checks('T2', 'หมุน refresh token ใบเดิมซ้ำ → ถูกฐานข้อมูลปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO auth_session (id, subject_kind, merchant_admin_id, family_id,
                              refresh_token_hash, parent_id, expires_at)
    VALUES ('acc_ses_evil', 'MERCHANT_ADMIN', 'acc_adm_1', 'acc_fam_1', pg_temp.h('acc-tok-evil'),
            'acc_ses_1', now() + interval '30 days')
  $q$, '23505', 'auth_session', ARRAY['parent_id']));

-- =====================================================================
-- T3 · เพิกถอนทั้งสายได้ด้วยคำสั่งเดียว · ไม่มีใบไหนรอด
-- =====================================================================
DO $$
DECLARE live int;
BEGIN
  UPDATE auth_session SET revoked_at = now(), revoked_reason = 'REUSE_DETECTED'
   WHERE family_id = 'acc_fam_1' AND revoked_at IS NULL;
  SELECT count(*) INTO live FROM auth_session
   WHERE family_id = 'acc_fam_1' AND revoked_at IS NULL;
  PERFORM pg_temp.rec('T3', 'เพิกถอนทั้ง family → ไม่เหลือ session ที่ยังใช้ได้เลย',
                      CASE WHEN live = 0 THEN 'PASS' ELSE 'FAIL' END);
END $$;

-- =====================================================================
-- T4 · เพิกถอนโดยไม่บอกเหตุผลไม่ได้
-- T4b · เหตุผลต้องเป็นค่าที่รู้จัก (เพิ่ม · migration 0005)
-- =====================================================================
SELECT pg_temp.rec_checks('T4', 'ตั้ง revoked_at โดยไม่ใส่ revoked_reason → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO auth_session (id, subject_kind, customer_id, family_id,
                              refresh_token_hash, expires_at, revoked_at)
    VALUES ('acc_ses_t4', 'CUSTOMER', 'acc_cus_1', 'acc_fam_t4', pg_temp.h('acc-tok-t4'),
            now() + interval '30 days', now())
  $q$, '23514', 'auth_session', ARRAY['revoked_at', 'revoked_reason']));

SELECT pg_temp.rec_checks('T4b', 'revoked_reason ที่ไม่รู้จัก → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO auth_session (id, subject_kind, customer_id, family_id,
                              refresh_token_hash, expires_at, revoked_at, revoked_reason)
    VALUES ('acc_ses_t4b', 'CUSTOMER', 'acc_cus_1', 'acc_fam_t4b', pg_temp.h('acc-tok-t4b'),
            now() + interval '30 days', now(), 'BECAUSE')
  $q$, '23514', 'auth_session', ARRAY['revoked_reason'],
       ARRAY['ck_auth_session_revoked_reason_enum']));

-- =====================================================================
-- T5 · session ต้องมีเจ้าของคนเดียว ไม่ใช่ทั้งคู่ ไม่ใช่ไม่มีเลย
-- =====================================================================
SELECT pg_temp.rec_checks('T5', 'session ที่มีเจ้าของสองคน หรือไม่มีเจ้าของเลย → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO auth_session (id, subject_kind, merchant_admin_id, customer_id,
                              family_id, refresh_token_hash, expires_at)
    VALUES ('acc_ses_t5a', 'MERCHANT_ADMIN', 'acc_adm_1', 'acc_cus_1', 'acc_fam_t5a',
            pg_temp.h('acc-tok-t5a'), now() + interval '30 days')
  $q$, '23514', 'auth_session', NULL, ARRAY['ck_one_subject']),
  pg_temp.try_reject($q$
    INSERT INTO auth_session (id, subject_kind, family_id, refresh_token_hash, expires_at)
    VALUES ('acc_ses_t5b', 'MERCHANT_ADMIN', 'acc_fam_t5b', pg_temp.h('acc-tok-t5b'),
            now() + interval '30 days')
  $q$, '23514', 'auth_session', NULL, ARRAY['ck_one_subject', 'ck_subject_matches']));

-- =====================================================================
-- T6 · subject_kind ต้องตรงกับคอลัมน์ที่มีค่า
--      บอกว่าเป็น CUSTOMER แต่ใส่ merchant_admin_id ไม่ได้
-- =====================================================================
SELECT pg_temp.rec_checks('T6', 'subject_kind ไม่ตรงกับคอลัมน์ที่มีค่า → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO auth_session (id, subject_kind, merchant_admin_id, family_id,
                              refresh_token_hash, expires_at)
    VALUES ('acc_ses_t6', 'CUSTOMER', 'acc_adm_1', 'acc_fam_t6', pg_temp.h('acc-tok-t6'),
            now() + interval '30 days')
  $q$, '23514', 'auth_session', NULL, ARRAY['ck_subject_matches']));

-- =====================================================================
-- T7 · ตาราง auth_session ต้องไม่มีคอลัมน์ที่เก็บ token แบบ plaintext
-- =====================================================================
DO $$
DECLARE bad text;
BEGIN
  SELECT string_agg(column_name::text, ',') INTO bad FROM information_schema.columns
   WHERE table_schema = 'public' AND table_name = 'auth_session'
     AND column_name ~* '(token|secret|password)' AND column_name NOT LIKE '%\_hash';
  PERFORM pg_temp.rec('T7', 'auth_session ไม่มีคอลัมน์เก็บ token แบบ plaintext เลย',
    CASE WHEN bad IS NULL THEN 'PASS' ELSE 'FAIL' END,
    CASE WHEN bad IS NOT NULL THEN 'คอลัมน์ต้องสงสัย: ' || bad END);
END $$;

-- =====================================================================
-- T8 · ไม่มีตารางไหนในระบบนี้เก็บ access token (RFD 16 §3.4)
-- =====================================================================
DO $$
DECLARE bad text;
BEGIN
  SELECT string_agg(table_name || '.' || column_name, ',') INTO bad
    FROM information_schema.columns
   WHERE table_schema = 'public' AND column_name ~* '(access_token|jwt|bearer)';
  PERFORM pg_temp.rec('T8', 'ไม่มีตารางไหนเก็บ access token',
    CASE WHEN bad IS NULL THEN 'PASS' ELSE 'FAIL' END,
    CASE WHEN bad IS NOT NULL THEN 'คอลัมน์ต้องสงสัย: ' || bad END);
END $$;

-- =====================================================================
-- T9 · วันหมดอายุต้องอยู่หลังวันสร้าง
-- =====================================================================
SELECT pg_temp.rec_checks('T9', 'session ที่หมดอายุก่อนถูกสร้าง → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO auth_session (id, subject_kind, customer_id, family_id,
                              refresh_token_hash, created_at, expires_at)
    VALUES ('acc_ses_t9', 'CUSTOMER', 'acc_cus_1', 'acc_fam_t9', pg_temp.h('acc-tok-t9'),
            now(), now() - interval '1 day')
  $q$, '23514', 'auth_session', ARRAY['created_at', 'expires_at']));

-- =====================================================================
-- T10 · password ต้องเป็น argon2id เท่านั้น                   RFD 8 D7
-- =====================================================================
SELECT pg_temp.rec_checks('T10', 'password ที่เป็น bcrypt หรือ plaintext เข้าฐานข้อมูลไม่ได้',
  pg_temp.try_reject($q$
    INSERT INTO merchant_admin (id, merchant_id, email, email_normalized, password_hash, display_name)
    VALUES ('acc_adm_t10a', 'acc_mch_1', 'acc-a@b.co', 'acc-a@b.co', '$2b$10$abcdefghijklmnopqrstuv', 'x')
  $q$, '23514', 'merchant_admin', ARRAY['password_hash']),
  pg_temp.try_reject($q$
    INSERT INTO merchant_admin (id, merchant_id, email, email_normalized, password_hash, display_name)
    VALUES ('acc_adm_t10b', 'acc_mch_1', 'acc-c@d.co', 'acc-c@d.co', 'hunter2', 'x')
  $q$, '23514', 'merchant_admin', ARRAY['password_hash']));

-- =====================================================================
-- T11 · บันทึก login ที่สำเร็จต้องไม่มีรหัสความล้มเหลว
-- =====================================================================
INSERT INTO login_attempt (id, identifier_hash, ip_hash, succeeded)
VALUES ('acc_la_1', pg_temp.h('acc-owner@shop.co.th'), pg_temp.h('1.2.3.4'), true);

SELECT pg_temp.rec_checks('T11', 'บันทึก login สำเร็จพร้อมรหัสความล้มเหลว → ถูกปฏิเสธ',
  pg_temp.try_reject($q$
    INSERT INTO login_attempt (id, identifier_hash, ip_hash, succeeded, failure_code)
    VALUES ('acc_la_bad', pg_temp.h('acc-owner@shop.co.th'), pg_temp.h('1.2.3.4'), true,
            'BAD_PASSWORD')
  $q$, '23514', 'login_attempt', ARRAY['failure_code', 'succeeded']));

-- =====================================================================
-- T12 · ตาราง login_attempt ไม่เก็บอีเมลหรือ IP แบบอ่านได้
-- =====================================================================
DO $$
DECLARE bad text;
BEGIN
  SELECT string_agg(column_name::text, ',') INTO bad FROM information_schema.columns
   WHERE table_schema = 'public' AND table_name = 'login_attempt'
     AND column_name ~* '(email|ip|identifier|tel|phone)' AND column_name NOT LIKE '%\_hash';
  PERFORM pg_temp.rec('T12', 'login_attempt เก็บแต่ hash ไม่เก็บอีเมลหรือ IP ตรงๆ',
    CASE WHEN bad IS NULL THEN 'PASS' ELSE 'FAIL' END,
    CASE WHEN bad IS NOT NULL THEN 'คอลัมน์ต้องสงสัย: ' || bad END);
END $$;

-- =====================================================================
-- T13 · นับจำนวนครั้งที่พลาดเพื่อล็อกบัญชี
-- =====================================================================
DO $$
DECLARE n int;
BEGIN
  INSERT INTO login_attempt (id, identifier_hash, ip_hash, succeeded, failure_code)
  SELECT 'acc_la_f' || g, pg_temp.h('acc-victim@shop.co.th'), pg_temp.h('9.9.9.9'), false,
         'BAD_PASSWORD'
    FROM generate_series(1, 5) g;
  SELECT count(*) INTO n FROM login_attempt
   WHERE identifier_hash = pg_temp.h('acc-victim@shop.co.th')
     AND NOT succeeded AND created_at > now() - interval '15 minutes';
  INSERT INTO login_lockout (identifier_hash, locked_until, attempt_count)
  VALUES (pg_temp.h('acc-victim@shop.co.th'), now() + interval '15 minutes', n)
  ON CONFLICT (identifier_hash) DO UPDATE
    SET locked_until = excluded.locked_until,
        attempt_count = login_lockout.attempt_count + 1, updated_at = now();
  PERFORM pg_temp.rec('T13', 'นับครั้งที่พลาดใน 15 นาทีแล้วล็อกบัญชีได้ (นับได้ 5)',
                      CASE WHEN n = 5 THEN 'PASS' ELSE 'FAIL' END, format('นับได้ %s', n));
EXCEPTION WHEN OTHERS THEN
  PERFORM pg_temp.rec('T13', 'นับครั้งที่พลาดใน 15 นาทีแล้วล็อกบัญชีได้ (นับได้ 5)',
                      'ERROR', SQLSTATE || ' · ' || SQLERRM);
END $$;

-- =====================================================================
-- T14 · invariant · ไม่มี session ที่ยังใช้ได้ของผู้ใช้ที่ถูกปิดบัญชี
--       ตรวจทั้งตาราง · ในโค้ด refresh เพิกถอนทั้ง family เมื่อบัญชีถูกปิด
-- =====================================================================
INSERT INTO auth_session (id, subject_kind, merchant_admin_id, family_id,
                          refresh_token_hash, expires_at)
VALUES ('acc_ses_t14', 'MERCHANT_ADMIN', 'acc_adm_1', 'acc_fam_t14', pg_temp.h('acc-tok-t14'),
        now() + interval '30 days');
UPDATE merchant_admin SET disabled_at = now() WHERE id = 'acc_adm_1';
UPDATE auth_session SET revoked_at = now(), revoked_reason = 'ADMIN_REVOKED'
 WHERE merchant_admin_id = 'acc_adm_1' AND revoked_at IS NULL;

DO $$
DECLARE bad int; sample text;
BEGIN
  SELECT count(*), string_agg(s.id, ',' ORDER BY s.id) INTO bad, sample
    FROM auth_session s JOIN merchant_admin a ON a.id = s.merchant_admin_id
   WHERE a.disabled_at IS NOT NULL AND s.revoked_at IS NULL AND s.expires_at > now();
  PERFORM pg_temp.rec('T14', 'ไม่มี session ที่ยังใช้ได้ของบัญชีที่ถูกปิด',
    CASE WHEN bad = 0 THEN 'PASS' ELSE 'FAIL' END,
    CASE WHEN bad > 0 THEN format('%s session: %s', bad, left(sample, 200)) END);
END $$;

-- =====================================================================
-- T15 · invariant · ไม่มีใบลูกที่ active ในสายที่ถูกเพิกถอนเพราะใช้ซ้ำ
-- =====================================================================
DO $$
DECLARE bad int; sample text;
BEGIN
  SELECT count(*), string_agg(child.id, ',' ORDER BY child.id) INTO bad, sample
    FROM auth_session child JOIN auth_session parent ON parent.id = child.parent_id
   WHERE parent.revoked_reason = 'REUSE_DETECTED' AND child.revoked_at IS NULL;
  PERFORM pg_temp.rec('T15', 'สายที่เจอการใช้ซ้ำ ไม่มีใบลูกที่ยังใช้ได้เหลืออยู่',
    CASE WHEN bad = 0 THEN 'PASS' ELSE 'FAIL' END,
    CASE WHEN bad > 0 THEN format('%s session: %s', bad, left(sample, 200)) END);
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
