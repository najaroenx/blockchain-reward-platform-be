"""Restore the four FEAT-01 chain-safety constraints that 0002 deliberately left out.

0002 ported the legacy schema as a structural baseline and dropped every partial unique index and
every regex CHECK (see its docstring). The FEAT-01 acceptance suite
(tests/sql/feat01_acceptance.sql, derived from reference/feat-01/tests/acceptance.sql) showed
that four of those losses leave guarantees
that application code alone cannot keep under concurrency or retries, because each one protects an
on-chain side effect that cannot be undone:

  - T6  `uq_provision_inflight`   one unfinished MERCHANT_PROVISION per merchant, so a retried or
                                  double-submitted signup cannot whitelist the same merchant twice.
  - T7  `ck_outbox_topic_version` outbox topics carry a `.vN` suffix (RFD 7 D9), so consumers can
                                  tell payload versions apart. PostgreSQL only: SQLite has no regex
                                  operator, and SQLite cannot add a CHECK without rebuilding the
                                  table. Tests run on SQLite, production on PostgreSQL.
  - T9  `uq_signer_nonce`         a signer never reuses a nonce (RFD 4 §3.6); two steps sharing one
                                  would replace each other in the mempool.
  - T10 `uq_step_txhash`          one transaction hash maps to one step (RFD 2 D4), so the projector
                                  cannot apply a chain event twice.

The three indexes are partial, matching the reference DDL, and use the same predicate on both
dialects (SQLite supports partial indexes). src/marketplace/infrastructure/legacy_schema.py declares
the same objects so `alembic check` stays clean.

Upgrading a database that already holds rows violating one of these fails on that statement; fix
the data first. The SQL below finds offenders:

    SELECT subject_id FROM operation WHERE kind = 'MERCHANT_PROVISION'
      AND status NOT IN ('FINALIZED','CHAIN_REVERTED') GROUP BY 1 HAVING count(*) > 1;
    SELECT id, topic FROM outbox WHERE topic !~ '\\.v[0-9]+$';
    SELECT signer_address, nonce FROM operation_step
      WHERE signer_address IS NOT NULL AND nonce IS NOT NULL GROUP BY 1, 2 HAVING count(*) > 1;
    SELECT tx_hash FROM operation_step WHERE tx_hash IS NOT NULL GROUP BY 1 HAVING count(*) > 1;
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_feat01_chain_safety"
down_revision = "0002_legacy_schema_port"
branch_labels = None
depends_on = None

PROVISION_INFLIGHT = "kind = 'MERCHANT_PROVISION' AND status NOT IN ('FINALIZED', 'CHAIN_REVERTED')"
NONCE_ASSIGNED = "signer_address IS NOT NULL AND nonce IS NOT NULL"
TXHASH_ASSIGNED = "tx_hash IS NOT NULL"
TOPIC_VERSIONED = r"topic ~ '\.v[0-9]+$'"


def _partial(where: str) -> dict[str, sa.TextClause]:
    return {"postgresql_where": sa.text(where), "sqlite_where": sa.text(where)}


def upgrade() -> None:
    op.create_index(
        "uq_provision_inflight",
        "operation",
        ["subject_id"],
        unique=True,
        **_partial(PROVISION_INFLIGHT),
    )
    op.create_index(
        "uq_signer_nonce",
        "operation_step",
        ["signer_address", "nonce"],
        unique=True,
        **_partial(NONCE_ASSIGNED),
    )
    op.create_index(
        "uq_step_txhash",
        "operation_step",
        ["tx_hash"],
        unique=True,
        **_partial(TXHASH_ASSIGNED),
    )
    if op.get_context().dialect.name == "postgresql":
        op.create_check_constraint("ck_outbox_topic_version", "outbox", TOPIC_VERSIONED)


def downgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        op.drop_constraint("ck_outbox_topic_version", "outbox", type_="check")
    op.drop_index("uq_step_txhash", table_name="operation_step")
    op.drop_index("uq_signer_nonce", table_name="operation_step")
    op.drop_index("uq_provision_inflight", table_name="operation")
