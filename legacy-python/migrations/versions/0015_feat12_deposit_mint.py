"""FEAT-12 THB deposit and mint: the reference guarantees, with the gaps in the pack closed.

The THB token's ABI matches the pack (`mint(to, amount)`, `burn(from, amount)`, one transaction
each). The corrections are to the pack's own rules (docs/reference-findings.md has the evidence):

  1. trg_issuance_only_from_mint only checks the operation's kind, so any THB_MINT operation could
     move ISSUANCE without baht behind it, which is F4 again. trg_issuance_backed (at commit)
     requires every ISSUANCE movement to be a thb_mint of a confirmed deposit (ISSUANCE −amount,
     the depositor's account +amount) or a BURNED reversal (ISSUANCE +amount, depositor −amount).

  2. deposit_reversal.available_at_reversal was the caller's number; the outcome was checked
     against it, not against the balance. trg_reversal_outcome_matches_balance now computes the
     depositor's available THB itself and requires the recorded value to equal it.

  3. One webhook could confirm several deposits (the pack's own fixture does), and an unsigned one
     could confirm at all. uq_deposit_webhook and trg_deposit_status_transition (evidence part):
     a deposit is confirmed by its own signed charge.complete webhook.

  4. Status needs evidence: MINTED needs its thb_mint row, REVERSED a BURNED reversal; a deposit
     starts PENDING_PAYMENT; a thb_mint names its deposit's own THB_MINT operation.

Rules:

  both dialects
    T27 uq_mint_inflight   one unfinished THB_MINT per deposit
    T7c uq_deposit_webhook one deposit per confirming webhook
    T32 ix_psp_unprocessed the work queue skips webhooks whose signature failed
        uq_thb_mint_chain_event  one Mint event backs one mint
  PostgreSQL only
    T2 T3   trg_psp_webhook_immutable  only processed_at changes
    T4 T5   f_satang_to_base, trg_deposit_amount_exact (1 satang = 10^16 base units),
            ck_thb_deposit_base_uint256, ck_thb_mint_amount_uint256, ck_deposit_reversal_*_uint256
    T7b T13-T15b T23b trg_deposit_status_transition  PENDING_PAYMENT → CONFIRMED | FAILED | EXPIRED,
            CONFIRMED → MINTED, MINTED → REVERSED; amounts fixed once confirmed (checked before
            any early return, the pack's T15 bug); evidence as in 3 and 4
    T8-T12  trg_mint_requires_confirmed_deposit  CONFIRMED, exact amount, the depositor's account,
            its own THB_MINT operation; afterwards only the chain columns are filled in
    T16     trg_issuance_only_from_mint · T16b T16c trg_issuance_backed (at commit)
    T22-T25 trg_reversal_outcome_matches_balance  MINTED deposit, exact amount, a signed webhook,
            available computed here, outcome BURNED iff available ≥ amount

Upgrading fails if existing rows break a new rule (a webhook confirming two deposits, a malformed
amount, two unfinished mints of one deposit):

    SELECT webhook_id, count(*) FROM thb_deposit
     WHERE webhook_id IS NOT NULL GROUP BY 1 HAVING count(*) > 1;
"""

import sqlalchemy as sa
from alembic import op

revision = "0015_feat12_deposit_mint"
down_revision = "0014_feat11_redeem"
branch_labels = None
depends_on = None

MINT_INFLIGHT = "kind = 'THB_MINT' AND status NOT IN ('FINALIZED', 'CHAIN_REVERTED')"

PG_UP = r"""
ALTER TABLE thb_deposit
  ADD CONSTRAINT ck_thb_deposit_base_uint256 CHECK (f_is_uint256(amount_base, true));
ALTER TABLE thb_mint
  ADD CONSTRAINT ck_thb_mint_amount_uint256 CHECK (f_is_uint256(amount_base, true));
ALTER TABLE deposit_reversal
  ADD CONSTRAINT ck_deposit_reversal_amount_uint256 CHECK (f_is_uint256(amount_base, true)),
  ADD CONSTRAINT ck_deposit_reversal_available_uint256
    CHECK (f_is_uint256(available_at_reversal, false));

-- T2 T3 · a webhook is evidence: only processed_at may change
CREATE FUNCTION f_psp_webhook_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW.id, NEW.provider, NEW.provider_event_id, NEW.event_type, NEW.raw_body, NEW.signature,
      NEW.signature_ok, NEW.received_at)
     IS DISTINCT FROM
     (OLD.id, OLD.provider, OLD.provider_event_id, OLD.event_type, OLD.raw_body, OLD.signature,
      OLD.signature_ok, OLD.received_at) THEN
    RAISE EXCEPTION 'psp_webhook %: what was received cannot change, only processed_at', OLD.id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_psp_webhook_immutable';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_psp_webhook_immutable
  BEFORE UPDATE ON psp_webhook
  FOR EACH ROW EXECUTE FUNCTION f_psp_webhook_immutable();

-- T4 T5 · 1 satang = 10^16 base units; 1 baht = 10^18
CREATE FUNCTION f_satang_to_base(p_satang bigint) RETURNS numeric
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT p_satang::numeric * 10000000000000000 $$;

CREATE FUNCTION f_deposit_amount_exact() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF f_is_uint256(NEW.amount_base, true)
     AND NEW.amount_base::numeric <> f_satang_to_base(NEW.amount_satang) THEN
    RAISE EXCEPTION 'deposit %: % satang is % base units, not %',
      NEW.id, NEW.amount_satang, f_satang_to_base(NEW.amount_satang), NEW.amount_base
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_deposit_amount_exact',
            HINT = '1 baht = 100 satang = 10^18 base units';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_deposit_amount_exact
  BEFORE INSERT OR UPDATE OF amount_satang, amount_base ON thb_deposit
  FOR EACH ROW EXECUTE FUNCTION f_deposit_amount_exact();

-- T7b T13-T15b T23b · the deposit lifecycle, and the evidence each status needs
CREATE FUNCTION f_deposit_status_transition() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE w record;
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'PENDING_PAYMENT' THEN
      RAISE EXCEPTION 'deposit % must start PENDING_PAYMENT, not %', NEW.id, NEW.status
        USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_deposit_status_transition';
    END IF;
    RETURN NEW;
  END IF;
  -- before any early return: an UPDATE that leaves status alone must not change the amount
  IF OLD.status <> 'PENDING_PAYMENT'
     AND (NEW.amount_satang, NEW.amount_base, NEW.holder_kind, NEW.holder_id)
         IS DISTINCT FROM (OLD.amount_satang, OLD.amount_base, OLD.holder_kind, OLD.holder_id) THEN
    RAISE EXCEPTION 'deposit % is %: amount and depositor are fixed', OLD.id, OLD.status
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_deposit_status_transition',
            HINT = 'confirming 100 and minting 1000 is F4';
  END IF;
  IF NEW.status <> OLD.status AND NOT (
       (OLD.status = 'PENDING_PAYMENT' AND NEW.status IN ('CONFIRMED', 'FAILED', 'EXPIRED'))
    OR (OLD.status = 'CONFIRMED' AND NEW.status = 'MINTED')
    OR (OLD.status = 'MINTED' AND NEW.status = 'REVERSED')) THEN
    RAISE EXCEPTION 'deposit %: % → % is not allowed', OLD.id, OLD.status, NEW.status
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_deposit_status_transition';
  END IF;
  IF NEW.status = 'CONFIRMED' AND NEW.confirmed_at IS NULL THEN
    NEW.confirmed_at := now();
  END IF;
  IF NEW.status = 'MINTED' AND NEW.minted_at IS NULL THEN
    NEW.minted_at := now();
  END IF;

  IF NEW.webhook_id IS DISTINCT FROM OLD.webhook_id AND NEW.webhook_id IS NOT NULL THEN
    SELECT signature_ok, event_type, provider INTO w FROM psp_webhook WHERE id = NEW.webhook_id;
    IF NOT w.signature_ok OR w.event_type <> 'charge.complete' OR w.provider <> NEW.provider THEN
      RAISE EXCEPTION 'deposit %: webhook % is not a signed charge.complete of %',
        NEW.id, NEW.webhook_id, NEW.provider
        USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_deposit_evidence';
    END IF;
  END IF;
  IF NEW.status = 'MINTED' AND OLD.status <> 'MINTED'
     AND NOT EXISTS (SELECT 1 FROM thb_mint WHERE deposit_id = NEW.id) THEN
    RAISE EXCEPTION 'deposit % cannot be MINTED without its thb_mint row', NEW.id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_deposit_evidence';
  END IF;
  IF NEW.status = 'REVERSED' AND OLD.status <> 'REVERSED'
     AND NOT EXISTS (SELECT 1 FROM deposit_reversal
                      WHERE deposit_id = NEW.id AND outcome = 'BURNED') THEN
    RAISE EXCEPTION 'deposit % cannot be REVERSED without a BURNED reversal', NEW.id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_deposit_evidence',
            HINT = 'MANUAL_REVIEW leaves the deposit MINTED until a person decides';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_deposit_status_transition
  BEFORE INSERT OR UPDATE ON thb_deposit
  FOR EACH ROW EXECUTE FUNCTION f_deposit_status_transition();

-- T8-T12 · what makes one mint right
CREATE FUNCTION f_mint_requires_confirmed_deposit() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE d record; o record; v_account text;
BEGIN
  IF TG_OP = 'UPDATE' THEN
    -- the projector fills chain_event_id / tx_hash / minted_block / applied_at; nothing else moves
    IF (NEW.deposit_id, NEW.operation_id, NEW.amount_base, NEW.to_account, NEW.to_account_id)
       IS DISTINCT FROM
       (OLD.deposit_id, OLD.operation_id, OLD.amount_base, OLD.to_account, OLD.to_account_id) THEN
      RAISE EXCEPTION 'thb_mint %: what was minted, and to whom, cannot change', OLD.deposit_id
        USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_mint_requires_confirmed_deposit';
    END IF;
    RETURN NEW;
  END IF;
  SELECT status, amount_base, holder_kind, holder_id INTO d
    FROM thb_deposit WHERE id = NEW.deposit_id;
  IF NOT FOUND OR d.status <> 'CONFIRMED' THEN
    RAISE EXCEPTION 'deposit % is %: only a CONFIRMED deposit is minted',
      NEW.deposit_id, coalesce(d.status, 'missing')
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_mint_requires_confirmed_deposit',
            HINT = 'F4 · no baht confirmed by the provider, no THB';
  END IF;
  IF NEW.amount_base IS DISTINCT FROM d.amount_base THEN
    RAISE EXCEPTION 'mint of % is %, the deposit is %',
      NEW.deposit_id, NEW.amount_base, d.amount_base
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_mint_requires_confirmed_deposit';
  END IF;
  v_account := CASE d.holder_kind WHEN 'MERCHANT' THEN 'MERCHANT_THB' ELSE 'CUSTOMER_THB' END;
  IF NEW.to_account IS DISTINCT FROM v_account
     OR NEW.to_account_id IS DISTINCT FROM v_account || ':' || d.holder_id THEN
    RAISE EXCEPTION 'mint of % goes to %, the depositor is %', NEW.deposit_id, NEW.to_account_id,
      v_account || ':' || d.holder_id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_mint_requires_confirmed_deposit';
  END IF;
  SELECT kind, subject_id INTO o FROM operation WHERE id = NEW.operation_id;
  IF o.kind IS DISTINCT FROM 'THB_MINT' OR o.subject_id IS DISTINCT FROM NEW.deposit_id THEN
    RAISE EXCEPTION 'mint of % must be recorded under that deposit''s THB_MINT operation',
      NEW.deposit_id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_mint_requires_confirmed_deposit';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_mint_requires_confirmed_deposit
  BEFORE INSERT OR UPDATE ON thb_mint
  FOR EACH ROW EXECUTE FUNCTION f_mint_requires_confirmed_deposit();

-- T16 · only THB_MINT operations touch ISSUANCE (refused at once)
CREATE FUNCTION f_issuance_only_from_mint() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_kind text;
BEGIN
  IF NEW.account <> 'ISSUANCE' THEN
    RETURN NEW;
  END IF;
  SELECT kind INTO v_kind FROM operation WHERE id = NEW.operation_id;
  IF v_kind IS DISTINCT FROM 'THB_MINT' THEN
    RAISE EXCEPTION 'a % operation cannot touch ISSUANCE', v_kind
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_issuance_only_from_mint',
            HINT = 'THB enters and leaves only through FEAT-12; elsewhere it moves';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_issuance_only_from_mint
  BEFORE INSERT ON ledger_entry
  FOR EACH ROW EXECUTE FUNCTION f_issuance_only_from_mint();

-- T16b T16c · F4: every ISSUANCE movement is a mint of a confirmed deposit or a BURNED reversal
CREATE FUNCTION f_issuance_backed() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_op text := NEW.operation_id;
  v_issuance numeric;
  m record;
  r record;
  v_side numeric;
BEGIN
  SELECT coalesce(sum(amount::numeric) FILTER (WHERE account = 'ISSUANCE'), 0) INTO v_issuance
    FROM ledger_entry WHERE operation_id = v_op;
  SELECT amount_base, to_account_id INTO m FROM thb_mint WHERE operation_id = v_op;
  IF FOUND THEN
    SELECT coalesce(sum(amount::numeric), 0) INTO v_side
      FROM ledger_entry WHERE operation_id = v_op AND account_id = m.to_account_id;
    IF v_issuance = -m.amount_base::numeric AND v_side = m.amount_base::numeric THEN
      RETURN NULL;
    END IF;
    RAISE EXCEPTION 'operation % mints % to %, its ledger moves ISSUANCE % and that account %',
      v_op, m.amount_base, m.to_account_id, v_issuance, v_side
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_issuance_backed';
  END IF;
  SELECT rv.amount_base,
         CASE d.holder_kind WHEN 'MERCHANT' THEN 'MERCHANT_THB' ELSE 'CUSTOMER_THB' END
           || ':' || d.holder_id AS account_id
    INTO r
    FROM deposit_reversal rv JOIN thb_deposit d ON d.id = rv.deposit_id
   WHERE rv.operation_id = v_op AND rv.outcome = 'BURNED';
  IF FOUND THEN
    SELECT coalesce(sum(amount::numeric), 0) INTO v_side
      FROM ledger_entry WHERE operation_id = v_op AND account_id = r.account_id;
    IF v_issuance = r.amount_base::numeric AND v_side = -r.amount_base::numeric THEN
      RETURN NULL;
    END IF;
    RAISE EXCEPTION 'operation % burns % from %, its ledger moves ISSUANCE % and that account %',
      v_op, r.amount_base, r.account_id, v_issuance, v_side
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_issuance_backed';
  END IF;
  RAISE EXCEPTION 'operation % moves ISSUANCE by % with no confirmed deposit or burned reversal',
    v_op, v_issuance
    USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_issuance_backed',
          HINT = 'F4 · THB exists only for baht that arrived';
END $$;

CREATE CONSTRAINT TRIGGER trg_issuance_backed
  AFTER INSERT ON ledger_entry
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW WHEN (NEW.account = 'ISSUANCE')
  EXECUTE FUNCTION f_issuance_backed();

-- T22-T25 · the outcome follows the balance, and the balance is read here, not supplied
CREATE FUNCTION f_reversal_outcome_matches_balance() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE d record; v_ok boolean; v_available numeric;
BEGIN
  SELECT status, amount_base, holder_kind, holder_id INTO d
    FROM thb_deposit WHERE id = NEW.deposit_id;
  IF NOT FOUND OR d.status <> 'MINTED' THEN
    RAISE EXCEPTION 'deposit % is %: only a MINTED deposit is reversed', NEW.deposit_id,
      coalesce(d.status, 'missing')
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_reversal_outcome_matches_balance',
            HINT = 'a deposit that was never minted just fails';
  END IF;
  IF NEW.amount_base IS DISTINCT FROM d.amount_base THEN
    RAISE EXCEPTION 'reversal of % is %, the deposit is %',
      NEW.deposit_id, NEW.amount_base, d.amount_base
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_reversal_outcome_matches_balance';
  END IF;
  SELECT signature_ok INTO v_ok FROM psp_webhook WHERE id = NEW.webhook_id;
  IF v_ok IS NOT TRUE THEN
    RAISE EXCEPTION 'reversal of % rests on webhook %, whose signature failed',
      NEW.deposit_id, NEW.webhook_id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_reversal_outcome_matches_balance';
  END IF;
  IF NOT (f_is_uint256(NEW.available_at_reversal, false)
          AND f_is_uint256(NEW.amount_base, true)) THEN
    RETURN NEW;  -- the CHECKs refuse malformed amounts
  END IF;
  v_available := f_available_thb(
    CASE d.holder_kind WHEN 'MERCHANT' THEN 'MERCHANT_THB' ELSE 'CUSTOMER_THB' END
    || ':' || d.holder_id);
  IF NEW.available_at_reversal::numeric <> v_available THEN
    RAISE EXCEPTION 'reversal of % records % available; the depositor has %',
      NEW.deposit_id, NEW.available_at_reversal, v_available
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_reversal_outcome_matches_balance';
  END IF;
  IF (NEW.outcome = 'BURNED') <> (v_available >= NEW.amount_base::numeric) THEN
    RAISE EXCEPTION 'reversal of %: % available against %, so the outcome is %, not %',
      NEW.deposit_id, v_available, NEW.amount_base,
      CASE WHEN v_available >= NEW.amount_base::numeric THEN 'BURNED' ELSE 'MANUAL_REVIEW' END,
      NEW.outcome
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_reversal_outcome_matches_balance';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_reversal_outcome_matches_balance
  BEFORE INSERT ON deposit_reversal
  FOR EACH ROW EXECUTE FUNCTION f_reversal_outcome_matches_balance();
"""

PG_DOWN = """
DROP TRIGGER trg_reversal_outcome_matches_balance ON deposit_reversal;
DROP FUNCTION f_reversal_outcome_matches_balance();
DROP TRIGGER trg_issuance_backed ON ledger_entry;
DROP FUNCTION f_issuance_backed();
DROP TRIGGER trg_issuance_only_from_mint ON ledger_entry;
DROP FUNCTION f_issuance_only_from_mint();
DROP TRIGGER trg_mint_requires_confirmed_deposit ON thb_mint;
DROP FUNCTION f_mint_requires_confirmed_deposit();
DROP TRIGGER trg_deposit_status_transition ON thb_deposit;
DROP FUNCTION f_deposit_status_transition();
DROP TRIGGER trg_deposit_amount_exact ON thb_deposit;
DROP FUNCTION f_deposit_amount_exact();
DROP FUNCTION f_satang_to_base(bigint);
DROP TRIGGER trg_psp_webhook_immutable ON psp_webhook;
DROP FUNCTION f_psp_webhook_immutable();
ALTER TABLE deposit_reversal
  DROP CONSTRAINT ck_deposit_reversal_available_uint256,
  DROP CONSTRAINT ck_deposit_reversal_amount_uint256;
ALTER TABLE thb_mint DROP CONSTRAINT ck_thb_mint_amount_uint256;
ALTER TABLE thb_deposit DROP CONSTRAINT ck_thb_deposit_base_uint256;
"""


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def _partial(where: str) -> dict[str, sa.TextClause]:
    return {"postgresql_where": sa.text(where), "sqlite_where": sa.text(where)}


def upgrade() -> None:
    op.create_index(
        "uq_mint_inflight", "operation", ["subject_id"], unique=True, **_partial(MINT_INFLIGHT)
    )
    op.create_index(
        "uq_deposit_webhook",
        "thb_deposit",
        ["webhook_id"],
        unique=True,
        **_partial("webhook_id IS NOT NULL"),
    )
    op.create_index("uq_thb_mint_chain_event", "thb_mint", ["chain_event_id"], unique=True)
    op.create_index(
        "ix_psp_unprocessed",
        "psp_webhook",
        ["received_at"],
        **_partial("processed_at IS NULL AND signature_ok"),
    )
    if _is_postgresql():
        op.execute(PG_UP)


def downgrade() -> None:
    if _is_postgresql():
        op.execute(PG_DOWN)
    op.drop_index("ix_psp_unprocessed", table_name="psp_webhook")
    op.drop_index("uq_thb_mint_chain_event", table_name="thb_mint")
    op.drop_index("uq_deposit_webhook", table_name="thb_deposit")
    op.drop_index("uq_mint_inflight", table_name="operation")
