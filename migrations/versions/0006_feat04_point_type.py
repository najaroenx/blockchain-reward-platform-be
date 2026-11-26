"""FEAT-04 create point type: the reference guarantees, aligned to the delivered point contract.

Two corrections to reference/feat-04 come first (docs/reference-findings.md has the evidence):

  1. The contract is NewPointToken v3 (reference/point-contract/point-smart-contract.html), not the
     epoch/ERC-7818 token the pack assumed. `deployPointToken(name, symbol)` takes no decimals
     (always 18), and expiry is one of four business models chosen once per token through
     `configureBusiness`, announced by `BusinessConfigured(uint8 business, bytes config)`:
       1 A  one program window       config = (uint256 startDate, uint256 expireDate)
       2 B  per-lot dates, FEFO only  config = (uint8 0)
       3 C  per-lot dates, user picks config = (uint8 1)
       4 D  duration from delivery    config = (uint8 policy)  0 FEFO_ONLY · 1 USER_SELECT
     No event carries genesis_at / epoch_seconds / validity_epochs, so the NOT NULL epoch columns of
     point_type_chain could never be filled. They are replaced by the business configuration, which
     the merchant requests on point_type and the projector records from the event on
     point_type_chain. The schema holds all four models; which one the business uses
     (reference/point-contract/CHOOSE-BUSINESS.md) is a per-token choice and not needed here.
     Lot dates (B/C/D) arrive later through LotCreated and belong to the balance model (BE-0512).

  2. The reference trigger f_point_chain_immutable forgot validity_epochs, so the one value that
     moves every expiry date could be rewritten. Here every chain-sourced column is fixed once set.

Then the reference rules that 0002 left out:

  both dialects
    T5  uq_point_symbol           (merchant_id, symbol)              WHERE status <> 'FAILED'
    T6  uq_point_name             (merchant_id, name_normalized)     WHERE status <> 'FAILED'
        with ck_point_type_name_normalized: name_normalized = lower(trim(name)). A stored column,
        the way merchant_admin.email_normalized works, instead of the reference's expression
        index: PostgreSQL rewrites trim() as TRIM(BOTH FROM ...) and SQLite cannot reflect
        expression indexes, so `alembic check` could never compare that index on either dialect.
        Write it as lower(trim(:name)) in SQL, not in Python, so it always satisfies the CHECK.
    T7  a FAILED deploy releases its name and symbol (the WHERE above)
    T8  uq_point_deploy_inflight  one unfinished POINT_TYPE_CREATE per point type
    T4  ck_point_type_decimals_fixed  decimals = 18 (was 0-30: the contract has no other value)
    T11 ck_point_type_business_config / ck_point_chain_business_config  a complete, consistent
        configuration for the chosen model
  PostgreSQL only
    T3  ck_point_type_symbol_format  ^[A-Z][A-Z0-9]{1,10}$
    T2  trg_point_type_merchant_active  the merchant must be ACTIVE (f_merchant_must_be_active is
        shared: FEAT-07 attaches it to coupon_type)
    T9  trg_point_chain_immutable  a chain-sourced column, once set, never changes; the
        configuration columns may go from NULL to a value once (configure is its own transaction)

The constraint names match the reference: the API maps 23505 on uq_point_symbol / uq_point_name to
409 SYMBOL_TAKEN / NAME_TAKEN.

Upgrading fails if point_type has rows (business_model is NOT NULL with nothing to backfill) or a
row breaks a new rule. point_type_chain epoch values are dropped: they cannot describe a v3 token.

    SELECT count(*) FROM point_type;
    SELECT count(*) FROM point_type_chain;
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_feat04_point_type"
down_revision = "0005_feat03_revoke_reason"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
HASH = sa.String(66)

NOT_FAILED = "status <> 'FAILED'"
DEPLOY_INFLIGHT = "kind = 'POINT_TYPE_CREATE' AND status NOT IN ('FINALIZED', 'CHAIN_REVERTED')"
LOT_POLICY = "lot_policy IS NULL OR lot_policy IN ('FEFO_ONLY', 'USER_SELECT')"
BUSINESS_CONFIG = (
    "(business_model = 1 AND program_start_at IS NOT NULL"
    " AND program_expire_at > program_start_at AND lot_policy IS NULL)"
    " OR (business_model = 2 AND program_start_at IS NULL AND program_expire_at IS NULL"
    " AND lot_policy = 'FEFO_ONLY')"
    " OR (business_model = 3 AND program_start_at IS NULL AND program_expire_at IS NULL"
    " AND lot_policy = 'USER_SELECT')"
    " OR (business_model = 4 AND program_start_at IS NULL AND program_expire_at IS NULL"
    " AND lot_policy IS NOT NULL)"
)
CHAIN_CONFIGURED = (
    "(business_model IS NULL) = (configured_block IS NULL)"
    " AND (configured_block IS NULL) = (configured_tx_hash IS NULL)"
)
SYMBOL_FORMAT = "symbol ~ '^[A-Z][A-Z0-9]{1,10}$'"

PG_UP = r"""
CREATE FUNCTION f_merchant_must_be_active() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_status text;
BEGIN
  SELECT status INTO v_status FROM merchant WHERE id = NEW.merchant_id;
  IF v_status IS DISTINCT FROM 'ACTIVE' THEN
    RAISE EXCEPTION 'merchant % is %, only an ACTIVE merchant can create %',
      NEW.merchant_id, coalesce(v_status, 'missing'), TG_TABLE_NAME
      USING ERRCODE = 'check_violation', CONSTRAINT = TG_NAME,
            HINT = 'FEAT-01 §3.4 · ACTIVE comes from the projector once provisioning is final';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_point_type_merchant_active
  BEFORE INSERT ON point_type
  FOR EACH ROW EXECUTE FUNCTION f_merchant_must_be_active();

CREATE FUNCTION f_point_chain_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.point_type_id IS DISTINCT FROM OLD.point_type_id
     OR NEW.contract_address IS DISTINCT FROM OLD.contract_address
     OR NEW.deployed_block IS DISTINCT FROM OLD.deployed_block
     OR NEW.deployed_tx_hash IS DISTINCT FROM OLD.deployed_tx_hash
     OR (OLD.business_model IS NOT NULL AND (
            NEW.business_model IS DISTINCT FROM OLD.business_model
         OR NEW.program_start_at IS DISTINCT FROM OLD.program_start_at
         OR NEW.program_expire_at IS DISTINCT FROM OLD.program_expire_at
         OR NEW.lot_policy IS DISTINCT FROM OLD.lot_policy
         OR NEW.configured_block IS DISTINCT FROM OLD.configured_block
         OR NEW.configured_tx_hash IS DISTINCT FROM OLD.configured_tx_hash)) THEN
    RAISE EXCEPTION 'point_type_chain %: values read from the chain cannot change',
      OLD.point_type_id
      USING ERRCODE = 'check_violation', CONSTRAINT = 'trg_point_chain_immutable',
            HINT = 'a new deployment is a new point_type row, not an edit of this one';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_point_chain_immutable
  BEFORE UPDATE ON point_type_chain
  FOR EACH ROW EXECUTE FUNCTION f_point_chain_immutable();
"""

PG_DOWN = """
DROP TRIGGER trg_point_chain_immutable ON point_type_chain;
DROP FUNCTION f_point_chain_immutable();
DROP TRIGGER trg_point_type_merchant_active ON point_type;
DROP FUNCTION f_merchant_must_be_active();
"""


def _partial(where: str) -> dict[str, sa.TextClause]:
    return {"postgresql_where": sa.text(where), "sqlite_where": sa.text(where)}


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def _business_columns() -> list[sa.Column[object]]:
    return [
        sa.Column("program_start_at", TS, nullable=True),
        sa.Column("program_expire_at", TS, nullable=True),
        sa.Column("lot_policy", sa.String(20), nullable=True),
    ]


def upgrade() -> None:
    with op.batch_alter_table("point_type") as batch:
        batch.drop_constraint("ck_point_type_decimals_range", type_="check")
        batch.create_check_constraint("ck_point_type_decimals_fixed", "decimals = 18")
        batch.alter_column("decimals", server_default="18", existing_type=sa.SmallInteger())
        batch.add_column(sa.Column("name_normalized", sa.Text(), nullable=False))
        batch.create_check_constraint(
            "ck_point_type_name_normalized", "name_normalized = lower(trim(name))"
        )
        batch.add_column(sa.Column("business_model", sa.SmallInteger(), nullable=False))
        for column in _business_columns():
            batch.add_column(column)
        batch.create_check_constraint("ck_point_type_lot_policy_enum", LOT_POLICY)
        batch.create_check_constraint("ck_point_type_business_config", BUSINESS_CONFIG)

    with op.batch_alter_table("point_type_chain") as batch:
        batch.drop_constraint("ck_point_chain_epoch_pos", type_="check")
        batch.drop_constraint("ck_point_chain_validity_pos", type_="check")
        batch.drop_column("genesis_at")
        batch.drop_column("epoch_seconds")
        batch.drop_column("validity_epochs")
        batch.add_column(sa.Column("business_model", sa.SmallInteger(), nullable=True))
        for column in _business_columns():
            batch.add_column(column)
        batch.add_column(sa.Column("configured_block", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("configured_tx_hash", HASH, nullable=True))
        batch.create_check_constraint("ck_point_chain_lot_policy_enum", LOT_POLICY)
        batch.create_check_constraint(
            "ck_point_chain_business_config", f"business_model IS NULL OR ({BUSINESS_CONFIG})"
        )
        batch.create_check_constraint("ck_point_chain_configured", CHAIN_CONFIGURED)
        batch.create_check_constraint(
            "ck_point_chain_configured_after_deploy",
            "configured_block IS NULL OR configured_block >= deployed_block",
        )
        batch.create_check_constraint(
            "ck_point_chain_configured_tx_len",
            "configured_tx_hash IS NULL OR length(configured_tx_hash) = 66",
        )

    op.create_index(
        "uq_point_symbol",
        "point_type",
        ["merchant_id", "symbol"],
        unique=True,
        **_partial(NOT_FAILED),
    )
    op.create_index(
        "uq_point_name",
        "point_type",
        ["merchant_id", "name_normalized"],
        unique=True,
        **_partial(NOT_FAILED),
    )
    op.create_index(
        "uq_point_deploy_inflight",
        "operation",
        ["subject_id"],
        unique=True,
        **_partial(DEPLOY_INFLIGHT),
    )

    if _is_postgresql():
        op.create_check_constraint("ck_point_type_symbol_format", "point_type", SYMBOL_FORMAT)
        op.execute(PG_UP)


def downgrade() -> None:
    # Restores the 0005 shape. Needs empty point_type and point_type_chain: a v3 configuration
    # has no epoch equivalent to convert back to.
    if _is_postgresql():
        op.execute(PG_DOWN)
        op.drop_constraint("ck_point_type_symbol_format", "point_type", type_="check")

    op.drop_index("uq_point_deploy_inflight", table_name="operation")
    op.drop_index("uq_point_name", table_name="point_type")
    op.drop_index("uq_point_symbol", table_name="point_type")

    with op.batch_alter_table("point_type_chain") as batch:
        for name in (
            "ck_point_chain_configured_tx_len",
            "ck_point_chain_configured_after_deploy",
            "ck_point_chain_configured",
            "ck_point_chain_business_config",
            "ck_point_chain_lot_policy_enum",
        ):
            batch.drop_constraint(name, type_="check")
        for column in (
            "configured_tx_hash",
            "configured_block",
            "lot_policy",
            "program_expire_at",
            "program_start_at",
            "business_model",
        ):
            batch.drop_column(column)
        batch.add_column(sa.Column("genesis_at", TS, nullable=False))
        batch.add_column(sa.Column("epoch_seconds", sa.Integer(), nullable=False))
        batch.add_column(sa.Column("validity_epochs", sa.Integer(), nullable=False))
        batch.create_check_constraint("ck_point_chain_epoch_pos", "epoch_seconds > 0")
        batch.create_check_constraint("ck_point_chain_validity_pos", "validity_epochs > 0")

    with op.batch_alter_table("point_type") as batch:
        for name in (
            "ck_point_type_business_config",
            "ck_point_type_lot_policy_enum",
            "ck_point_type_name_normalized",
            "ck_point_type_decimals_fixed",
        ):
            batch.drop_constraint(name, type_="check")
        for column in (
            "lot_policy",
            "program_expire_at",
            "program_start_at",
            "business_model",
            "name_normalized",
        ):
            batch.drop_column(column)
        batch.alter_column("decimals", server_default=None, existing_type=sa.SmallInteger())
        batch.create_check_constraint("ck_point_type_decimals_range", "decimals BETWEEN 0 AND 30")
