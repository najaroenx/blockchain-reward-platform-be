"""FEAT-03: `auth_session.revoked_reason` takes only the six known reasons.

The reference pack (reference/feat-03/db/migration.sql) declares `revoked_reason` as the enum
`revoke_reason`; 0002 ported it as a free `String(40)`, so a typo such as 'REUSED' would be stored
silently and break both the audit trail and FEAT-03 T15, which looks for 'REUSE_DETECTED'. This adds
the enum back as a CHECK, the same way 0002 represents every other Postgres enum, so it holds on
SQLite too (tests/sql/feat03_acceptance.sql T4b proves it on PostgreSQL).

  LOGOUT            the user signed out
  ROTATED           refreshed; the old token has done its job
  REUSE_DETECTED    a used token came back: the family is revoked
  PASSWORD_CHANGED
  ADMIN_REVOKED     revoked by an admin, or because the account was disabled
  EXPIRED

Upgrading fails if a stored reason is outside the list. Find offenders first:

    SELECT revoked_reason, count(*) FROM auth_session
     WHERE revoked_reason IS NOT NULL
       AND revoked_reason NOT IN ('LOGOUT', 'ROTATED', 'REUSE_DETECTED',
                                  'PASSWORD_CHANGED', 'ADMIN_REVOKED', 'EXPIRED')
     GROUP BY 1;
"""

from alembic import op

revision = "0005_feat03_revoke_reason"
down_revision = "0004_feat02_customer_otp"
branch_labels = None
depends_on = None

NAME = "ck_auth_session_revoked_reason_enum"
CONDITION = (
    "revoked_reason IS NULL OR revoked_reason IN"
    " ('LOGOUT', 'ROTATED', 'REUSE_DETECTED', 'PASSWORD_CHANGED', 'ADMIN_REVOKED', 'EXPIRED')"
)


def upgrade() -> None:
    # On SQLite batch mode rebuilds the table; its CHECKs and the partial uq_session_parent
    # index carry across unchanged (checked when this migration was written).
    with op.batch_alter_table("auth_session") as batch:
        batch.create_check_constraint(NAME, CONDITION)


def downgrade() -> None:
    with op.batch_alter_table("auth_session") as batch:
        batch.drop_constraint(NAME, type_="check")
