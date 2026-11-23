"""Revoke disabled owners' sessions and reject new live sessions for them."""

from alembic import op

revision = "0003_disabled_sessions"
down_revision = "0002_auth_login"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE auth_session SET
            revoked_at = COALESCE(
                (SELECT disabled_at FROM merchant_admin WHERE id = auth_session.merchant_admin_id),
                (SELECT disabled_at FROM customer WHERE id = auth_session.customer_id)
            ), revoked_reason = 'ADMIN_REVOKED'
        WHERE revoked_at IS NULL AND (
            EXISTS (SELECT 1 FROM merchant_admin
                WHERE id = auth_session.merchant_admin_id AND disabled_at IS NOT NULL)
            OR EXISTS (SELECT 1 FROM customer
                WHERE id = auth_session.customer_id AND disabled_at IS NOT NULL)
        )
    """)
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""
            CREATE FUNCTION auth_require_enabled_owner() RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE blocked boolean;
            BEGIN
                IF NEW.revoked_at IS NULL THEN
                    IF NEW.merchant_admin_id IS NOT NULL THEN
                        SELECT disabled_at IS NOT NULL INTO blocked FROM merchant_admin
                        WHERE id = NEW.merchant_admin_id FOR SHARE;
                    ELSE
                        SELECT disabled_at IS NOT NULL INTO blocked FROM customer
                        WHERE id = NEW.customer_id FOR SHARE;
                    END IF;
                    IF blocked THEN
                        RAISE EXCEPTION 'ck_session_owner_enabled'
                            USING ERRCODE = '23514', CONSTRAINT = 'ck_session_owner_enabled';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $$
        """)
        op.execute("""
            CREATE FUNCTION auth_revoke_disabled_owner() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.disabled_at IS NOT NULL THEN
                    IF TG_TABLE_NAME = 'merchant_admin' THEN
                        UPDATE auth_session SET revoked_at = NEW.disabled_at,
                            revoked_reason = 'ADMIN_REVOKED'
                        WHERE merchant_admin_id = NEW.id AND revoked_at IS NULL;
                    ELSE
                        UPDATE auth_session SET revoked_at = NEW.disabled_at,
                            revoked_reason = 'ADMIN_REVOKED'
                        WHERE customer_id = NEW.id AND revoked_at IS NULL;
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $$
        """)
        for name, table in (("admin", "merchant_admin"), ("customer", "customer")):
            op.execute(f"""
                CREATE TRIGGER auth_{name}_disabled AFTER UPDATE OF disabled_at ON {table}
                FOR EACH ROW EXECUTE FUNCTION auth_revoke_disabled_owner()
            """)
        for name, event in (
            ("insert", "INSERT"),
            ("update", "UPDATE OF merchant_admin_id, customer_id, revoked_at"),
        ):
            op.execute(f"""
                CREATE TRIGGER auth_session_enabled_{name} BEFORE {event} ON auth_session
                FOR EACH ROW EXECUTE FUNCTION auth_require_enabled_owner()
            """)
    else:
        for name, table, column in (
            ("admin", "merchant_admin", "merchant_admin_id"),
            ("customer", "customer", "customer_id"),
        ):
            op.execute(f"""
                CREATE TRIGGER auth_{name}_disabled AFTER UPDATE OF disabled_at ON {table}
                WHEN NEW.disabled_at IS NOT NULL BEGIN
                    UPDATE auth_session SET revoked_at = NEW.disabled_at,
                        revoked_reason = 'ADMIN_REVOKED'
                    WHERE {column} = NEW.id AND revoked_at IS NULL;
                END
            """)
        for name, event in (
            ("insert", "INSERT"),
            ("update", "UPDATE OF merchant_admin_id, customer_id, revoked_at"),
        ):
            op.execute(f"""
                CREATE TRIGGER auth_session_enabled_{name} BEFORE {event} ON auth_session
                WHEN NEW.revoked_at IS NULL AND (
                    EXISTS (SELECT 1 FROM merchant_admin
                        WHERE id = NEW.merchant_admin_id AND disabled_at IS NOT NULL)
                    OR EXISTS (SELECT 1 FROM customer
                        WHERE id = NEW.customer_id AND disabled_at IS NOT NULL)
                ) BEGIN
                    SELECT RAISE(ABORT, 'ck_session_owner_enabled');
                END
            """)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for name, table in (
            ("auth_admin_disabled", "merchant_admin"),
            ("auth_customer_disabled", "customer"),
            ("auth_session_enabled_insert", "auth_session"),
            ("auth_session_enabled_update", "auth_session"),
        ):
            op.execute(f"DROP TRIGGER {name} ON {table}")
        op.execute("DROP FUNCTION auth_revoke_disabled_owner()")
        op.execute("DROP FUNCTION auth_require_enabled_owner()")
    else:
        for name in (
            "auth_admin_disabled",
            "auth_customer_disabled",
            "auth_session_enabled_insert",
            "auth_session_enabled_update",
        ):
            op.execute(f"DROP TRIGGER {name}")
