"""FEAT-02 customer register + OTP: the data guarantees the database keeps (plan C).

FEAT-02's reference pack enforces almost everything in PostgreSQL functions and triggers, which 0002
deliberately did not port. Plan C splits the rules by kind:

  database, both dialects (declarative)
    T14  `uq_challenge_live`        one PENDING challenge per phone
    T36  `uq_provision_inflight`    widened from 0003 to cover CUSTOMER_PROVISION too, keyed on
                                    (kind, subject_id) so a merchant and a customer never collide
    T26  customer.verified_challenge_id NOT NULL     a customer cannot exist without a challenge
    T27  `fk_customer_verified_challenge`             composite FK (verified_challenge_id,
                                    phone_e164) -> otp_challenge (id, phone_e164): the customer's
                                    phone IS the verified phone (F7 · the legacy system took it from
                                    the request body). Needs `uq_challenge_id_phone` as its target.
    T30  `uq_customer_challenge`    one challenge registers one customer

  database, PostgreSQL only (regex CHECKs and triggers; SQLite has neither `~` nor plpgsql)
    T2 T3 `ck_*_phone_e164`         E.164 only, so 0812345678 and +66812345678 cannot both exist
    T5    `ck_otp_challenge_pepper_ref`  the pepper is a kms:// reference, never the secret
    T11   `ck_wallet_kms_key_ref`   the key reference is a kms:// reference, never key material
    T24 T25  `trg_otp_challenge_guard`  phone, code_hash, pepper_ref, expires_at never change, and
                                    status only moves PENDING -> VERIFIED|EXPIRED|LOCKED,
                                    VERIFIED -> CONSUMED|EXPIRED
    T28   `trg_customer_from_verified_challenge`  the challenge must be VERIFIED at insert
    T31   `trg_customer_guard`      phone_e164, phone_verified_at and verified_challenge_id are
                                    fixed once the customer exists

  application (src, round 2): phone normalisation (T1), OTP throttle (T13 T15 T16), the verify flow
  with attempt counting and locking (T17-T21b T23 T37), session issue (T33).

  deferred: the DDL event trigger against secret-named columns (T7-T9 T12) needs superuser in
  every environment; not decided yet.

Upgrading fails if `customer` already has rows (verified_challenge_id is NOT NULL with no value to
backfill) or if existing rows break a new CHECK. Find offenders first:

    SELECT count(*) FROM customer;
    SELECT phone_e164 FROM otp_challenge WHERE status = 'PENDING'
      GROUP BY 1 HAVING count(*) > 1;
    SELECT id, phone_e164 FROM otp_challenge WHERE phone_e164 !~ '^\\+[1-9][0-9]{7,14}$';
    SELECT id, pepper_ref FROM otp_challenge WHERE pepper_ref !~ '^kms://';
    SELECT id, kms_key_ref FROM wallet WHERE kms_key_ref !~ '^kms://[A-Za-z0-9/_.:+=-]+$';
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_feat02_customer_otp"
down_revision = "0003_feat01_chain_safety"
branch_labels = None
depends_on = None

PROVISION_INFLIGHT_0003 = (
    "kind = 'MERCHANT_PROVISION' AND status NOT IN ('FINALIZED', 'CHAIN_REVERTED')"
)
PROVISION_INFLIGHT = (
    "kind IN ('MERCHANT_PROVISION', 'CUSTOMER_PROVISION')"
    " AND status NOT IN ('FINALIZED', 'CHAIN_REVERTED')"
)
CHALLENGE_LIVE = "status = 'PENDING'"
E164 = r"phone_e164 ~ '^\+[1-9][0-9]{7,14}$'"
PEPPER_REF = "pepper_ref ~ '^kms://'"
KMS_KEY_REF = "kms_key_ref ~ '^kms://[A-Za-z0-9/_.:+=-]+$'"

PG_CHECKS = (
    ("ck_customer_phone_e164", "customer", E164),
    ("ck_otp_challenge_phone_e164", "otp_challenge", E164),
    ("ck_phone_throttle_phone_e164", "phone_throttle", E164),
    ("ck_otp_challenge_pepper_ref", "otp_challenge", PEPPER_REF),
    ("ck_wallet_kms_key_ref", "wallet", KMS_KEY_REF),
)

PG_TRIGGERS_UP = r"""
CREATE FUNCTION f_otp_challenge_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.phone_e164 <> OLD.phone_e164
     OR NEW.code_hash <> OLD.code_hash
     OR NEW.pepper_ref <> OLD.pepper_ref
     OR NEW.expires_at <> OLD.expires_at THEN
    RAISE EXCEPTION 'challenge %: phone, code_hash, pepper_ref, expires_at are fixed', OLD.id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_otp_challenge_guard',
            HINT = 'F7 · the legacy system overwrote the phone from the request body';
  END IF;
  IF NEW.status <> OLD.status AND NOT (
       (OLD.status = 'PENDING'  AND NEW.status IN ('VERIFIED', 'EXPIRED', 'LOCKED'))
    OR (OLD.status = 'VERIFIED' AND NEW.status IN ('CONSUMED', 'EXPIRED'))
  ) THEN
    RAISE EXCEPTION 'challenge %: status cannot move from % to %', OLD.id, OLD.status, NEW.status
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_otp_challenge_guard';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_otp_challenge_guard
  BEFORE UPDATE ON otp_challenge
  FOR EACH ROW EXECUTE FUNCTION f_otp_challenge_guard();

CREATE FUNCTION f_customer_from_verified_challenge() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_status text;
BEGIN
  IF NEW.verified_challenge_id IS NULL THEN
    RAISE EXCEPTION 'customer % has no verified_challenge_id', NEW.id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_customer_from_verified_challenge',
            HINT = 'F7 · a customer exists only through a verified challenge';
  END IF;
  SELECT status INTO v_status FROM otp_challenge WHERE id = NEW.verified_challenge_id;
  IF v_status IS DISTINCT FROM 'VERIFIED' THEN
    RAISE EXCEPTION 'challenge % is %, only a VERIFIED challenge can register a customer',
      NEW.verified_challenge_id, coalesce(v_status, 'missing')
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_customer_from_verified_challenge',
            HINT = 'F7 · the phone must come from a verified challenge, not from the request';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_customer_from_verified_challenge
  BEFORE INSERT ON customer
  FOR EACH ROW EXECUTE FUNCTION f_customer_from_verified_challenge();

CREATE FUNCTION f_customer_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.phone_e164 <> OLD.phone_e164
     OR NEW.phone_verified_at <> OLD.phone_verified_at
     OR NEW.verified_challenge_id <> OLD.verified_challenge_id THEN
    RAISE EXCEPTION 'customer %: phone and its verification cannot change; verify the new phone',
      OLD.id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_customer_guard';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_customer_guard
  BEFORE UPDATE ON customer
  FOR EACH ROW EXECUTE FUNCTION f_customer_guard();
"""

PG_TRIGGERS_DOWN = """
DROP TRIGGER trg_customer_guard ON customer;
DROP FUNCTION f_customer_guard();
DROP TRIGGER trg_customer_from_verified_challenge ON customer;
DROP FUNCTION f_customer_from_verified_challenge();
DROP TRIGGER trg_otp_challenge_guard ON otp_challenge;
DROP FUNCTION f_otp_challenge_guard();
"""


def _partial(where: str) -> dict[str, sa.TextClause]:
    return {"postgresql_where": sa.text(where), "sqlite_where": sa.text(where)}


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def upgrade() -> None:
    # Table rebuilds first (SQLite batch mode recreates the table), then indexes, so a rebuild
    # never has to carry a partial index across.
    with op.batch_alter_table("otp_challenge") as batch:
        batch.create_unique_constraint("uq_challenge_id_phone", ["id", "phone_e164"])

    with op.batch_alter_table("customer", recreate="auto") as batch:
        batch.add_column(sa.Column("verified_challenge_id", sa.Text(), nullable=False))
        batch.create_unique_constraint("uq_customer_challenge", ["verified_challenge_id"])
        batch.create_foreign_key(
            "fk_customer_verified_challenge",
            "otp_challenge",
            ["verified_challenge_id", "phone_e164"],
            ["id", "phone_e164"],
        )

    op.create_index(
        "uq_challenge_live",
        "otp_challenge",
        ["phone_e164"],
        unique=True,
        **_partial(CHALLENGE_LIVE),
    )

    op.drop_index("uq_provision_inflight", table_name="operation")
    op.create_index(
        "uq_provision_inflight",
        "operation",
        ["kind", "subject_id"],
        unique=True,
        **_partial(PROVISION_INFLIGHT),
    )

    if _is_postgresql():
        for name, table, condition in PG_CHECKS:
            op.create_check_constraint(name, table, condition)
        op.execute(PG_TRIGGERS_UP)


def downgrade() -> None:
    if _is_postgresql():
        op.execute(PG_TRIGGERS_DOWN)
        for name, table, _ in reversed(PG_CHECKS):
            op.drop_constraint(name, table, type_="check")

    op.drop_index("uq_provision_inflight", table_name="operation")
    op.create_index(
        "uq_provision_inflight",
        "operation",
        ["subject_id"],
        unique=True,
        **_partial(PROVISION_INFLIGHT_0003),
    )

    op.drop_index("uq_challenge_live", table_name="otp_challenge")

    with op.batch_alter_table("customer") as batch:
        batch.drop_constraint("fk_customer_verified_challenge", type_="foreignkey")
        batch.drop_constraint("uq_customer_challenge", type_="unique")
        batch.drop_column("verified_challenge_id")

    with op.batch_alter_table("otp_challenge") as batch:
        batch.drop_constraint("uq_challenge_id_phone", type_="unique")
