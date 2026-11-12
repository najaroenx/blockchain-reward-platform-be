"""Port the legacy TypeScript/Prisma reference schema as a structural baseline.

Ports the 50-table structure from docs/legacy/0001_typescript_reference.sql (14 proposed
features, archived there as a non-authoritative reference). All trigger-encoded business logic
from that file's "2 · function" and "6 · trigger" sections — status-transition rules, computed
field consistency (purchase math, ledger balance), OTP verification flow, reservation/ledger
balance enforcement, phone normalization, and the `f_no_secret_columns` event-trigger guard — is
intentionally NOT ported. That logic belongs in the Python application layer per this project's
Clean Architecture (see docs/layer-based-vs-feature-based.md); secret-column avoidance is a code
review concern here, not a DB-enforced one. Known contract-vs-schema conflicts from
docs/contract-validation.md (listing price mutability via `updatePrice`, atomic on-chain
redemption, `totalSupply` semantics) are not reconciled by this migration and must be resolved
before any of these tables are wired to live use cases.

Representation choices, made to keep the schema portable across this project's PostgreSQL
production target and SQLite test/dev target (see tests/conftest.py, which runs this migration
against SQLite):
  - `numeric(78,0)` on-chain integers -> `String(78)` decimal strings, matching the convention
    already used in marketplace.listings.infrastructure.models.
  - `bytea` fixed to 20 bytes (addresses) -> `String(42)` lowercase-hex (`0x` + 40 chars).
  - `bytea` fixed to 32 bytes (hashes) -> `String(66)` lowercase-hex (`0x` + 64 chars).
  - `bytea` with no fixed length (raw tx bytes, raw webhook bodies, event data) -> `LargeBinary`.
  - `jsonb` and `text[]` -> `JSON` (plain `JSON`, not `JSONB`, for SQLite compatibility).
  - Native Postgres enums -> `String` + a named `CHECK ... IN (...)` constraint.
  - `now()` defaults -> `sa.func.now()`, which compiles per-dialect.

Constraints and defaults dropped from the source (not re-implemented here; see the migration
PR description for the full list by table):
  - Every CHECK using the Postgres regex operator `~` (phone/URL/KMS-ref/topic format checks) —
    SQLite has no equivalent operator. Format validation moves to the application layer.
  - `ck_phone_normalized` / `ck_ch_normalized`, which called the dropped `f_normalize_phone`.
  - `ck_one_subject`, which called the dropped `num_nonnulls()` (Postgres-only).
  - The `event_types` array CHECK (`cardinality`/`array_position`), moot once the column became
    JSON.
  - `DEFAULT now() + interval ...` on two `expires_at` columns (Postgres interval literal syntax)
    and `'{}'::jsonb` / `'[]'::jsonb` column defaults — the inserting use case must supply these.
  - Every partial (`WHERE ...`) or function-based (e.g. `lower(trim(name))`) unique index from the
    source's section 5 — SQLite supports partial indexes too, but porting them correctly is schema
    optimization work deferred to a follow-up pass, not structural baseline. Only plain, full-table
    unique constraints are ported here.
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_legacy_schema_port"
down_revision = "0001_marketplace_projection"
branch_labels = None
depends_on = None

ADDRESS = sa.String(42)
HASH = sa.String(66)
AMOUNT = sa.String(78)
TS = sa.DateTime(timezone=True)


def _enum_check(column: str, values: tuple[str, ...], name: str) -> sa.CheckConstraint:
    quoted = ", ".join(f"'{v}'" for v in values)
    return sa.CheckConstraint(f"{column} IN ({quoted})", name=name)


HOLDER_KIND = ("MERCHANT", "CUSTOMER")


def upgrade() -> None:
    # --- tier 0: no foreign keys -------------------------------------------------------------
    op.create_table(
        "customer",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("phone_e164", sa.Text(), nullable=False, unique=True),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("status", sa.String(40), nullable=False, server_default="PENDING_WALLET"),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("phone_verified_at", TS, nullable=False),
        sa.Column("tel_hash", sa.LargeBinary(), nullable=True, unique=True),
        sa.Column("disabled_at", TS, nullable=True),
        _enum_check(
            "status",
            ("PENDING_WALLET", "ACTIVE", "SUSPENDED", "CLOSED"),
            "ck_customer_status_enum",
        ),
    )
    op.create_table(
        "merchant",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="PENDING"),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("activated_at", TS, nullable=True),
        sa.CheckConstraint("length(trim(name)) BETWEEN 1 AND 200", name="ck_merchant_name_len"),
        _enum_check(
            "status", ("PENDING", "ACTIVE", "SUSPENDED", "CLOSED"), "ck_merchant_status_enum"
        ),
        sa.CheckConstraint(
            "(status = 'ACTIVE') = (activated_at IS NOT NULL)", name="ck_merchant_active"
        ),
    )
    op.create_table(
        "operation",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("actor_scope", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="PENDING"),
        sa.Column("subject_id", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("error_note", sa.Text(), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("finalized_at", TS, nullable=True),
        _enum_check(
            "kind",
            (
                "MERCHANT_PROVISION",
                "CUSTOMER_PROVISION",
                "POINT_TYPE_CREATE",
                "POINT_TRANSFER",
                "COUPON_TYPE_CREATE",
                "COUPON_ISSUE",
                "COUPON_REDEEM",
                "LISTING_CREATE",
                "LISTING_DELETE",
                "LISTING_BUY",
                "THB_MINT",
            ),
            "ck_operation_kind_enum",
        ),
        _enum_check(
            "status",
            (
                "PENDING",
                "SUBMITTED",
                "CONFIRMED",
                "FINALIZED",
                "CHAIN_REVERTED",
                "ORPHANED",
                "MANUAL_REVIEW",
            ),
            "ck_operation_status_enum",
        ),
        sa.CheckConstraint(
            "(status = 'FINALIZED') = (finalized_at IS NOT NULL)", name="ck_finalized_consistent"
        ),
    )
    op.create_table(
        "signer",
        sa.Column("address", ADDRESS, primary_key=True),
        sa.Column("key_ref", sa.Text(), nullable=False),
        sa.Column("next_nonce", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("length(address) = 42", name="ck_signer_address_len"),
        sa.CheckConstraint("next_nonce >= 0", name="ck_signer_nonce_nonneg"),
    )
    op.create_table(
        "wallet",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("address", ADDRESS, nullable=False, unique=True),
        sa.Column("key_ref", sa.Text(), nullable=False),
        sa.Column("owner_kind", sa.String(40), nullable=False),
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("kms_key_ref", sa.Text(), nullable=True),
        sa.Column("status", sa.String(40), nullable=True, server_default="REQUESTED"),
        sa.Column("live_at", TS, nullable=True),
        sa.CheckConstraint("length(address) = 42", name="ck_wallet_address_len"),
        _enum_check("owner_kind", ("MERCHANT", "CUSTOMER", "PLATFORM"), "ck_wallet_owner_enum"),
        _enum_check("status", ("REQUESTED", "LIVE", "FAILED"), "ck_wallet_status_enum"),
        sa.CheckConstraint("(status = 'LIVE') = (live_at IS NOT NULL)", name="ck_wallet_live"),
        sa.UniqueConstraint("owner_kind", "owner_id", name="uq_wallet_owner"),
    )
    op.create_table(
        "fee_schedule",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("bps", sa.Integer(), nullable=False),
        sa.Column("effective_from", TS, nullable=False, unique=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.CheckConstraint("bps BETWEEN 0 AND 10000", name="ck_fee_bps_range"),
    )
    op.create_table(
        "finality_config",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("depth_blocks", sa.Integer(), nullable=False),
        sa.Column("effective_from", TS, nullable=False, unique=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.CheckConstraint("depth_blocks BETWEEN 1 AND 1000", name="ck_finality_depth_range"),
    )
    op.create_table(
        "raw_block",
        sa.Column("block_hash", HASH, primary_key=True),
        sa.Column("block_number", sa.BigInteger(), nullable=False),
        sa.Column("parent_hash", HASH, nullable=False),
        sa.Column("timestamp", TS, nullable=False),
        sa.Column("tx_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("orphaned_at", TS, nullable=True),
        sa.Column("read_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("length(block_hash) = 66", name="ck_raw_block_hash_len"),
        sa.CheckConstraint("length(parent_hash) = 66", name="ck_raw_block_parent_len"),
        sa.CheckConstraint("block_number > 0", name="ck_raw_block_number_pos"),
        sa.CheckConstraint("tx_count >= 0", name="ck_raw_block_tx_count_nonneg"),
        sa.CheckConstraint("block_hash <> parent_hash", name="ck_not_own_parent"),
    )
    op.create_table(
        "indexer_cursor",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("last_block", sa.BigInteger(), nullable=False),
        sa.Column("last_block_hash", HASH, nullable=True),
        sa.Column("lease_owner", sa.Text(), nullable=True),
        sa.Column("lease_until", TS, nullable=True),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("contract_address", ADDRESS, nullable=True),
        sa.CheckConstraint("id = 'main'", name="ck_cursor_id"),
        sa.CheckConstraint("last_block >= 0", name="ck_cursor_last_block_nonneg"),
        sa.CheckConstraint(
            "last_block_hash IS NULL OR length(last_block_hash) = 66", name="ck_cursor_hash_len"
        ),
        sa.CheckConstraint(
            "contract_address IS NULL OR length(contract_address) = 42",
            name="ck_cursor_contract_len",
        ),
        sa.CheckConstraint("(lease_owner IS NULL) = (lease_until IS NULL)", name="ck_lease_pair"),
        sa.CheckConstraint("(last_block = 0) = (last_block_hash IS NULL)", name="ck_cursor_hash"),
    )
    op.create_table(
        "login_attempt",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("identifier_hash", HASH, nullable=False),
        sa.Column("ip_hash", HASH, nullable=False),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        sa.Column("failure_code", sa.Text(), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("length(identifier_hash) = 66", name="ck_login_attempt_id_hash_len"),
        sa.CheckConstraint("length(ip_hash) = 66", name="ck_login_attempt_ip_hash_len"),
        sa.CheckConstraint("succeeded = (failure_code IS NULL)", name="ck_failure_code"),
    )
    op.create_table(
        "login_lockout",
        sa.Column("identifier_hash", HASH, primary_key=True),
        sa.Column("locked_until", TS, nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("length(identifier_hash) = 66", name="ck_login_lockout_hash_len"),
        sa.CheckConstraint("attempt_count > 0", name="ck_login_lockout_count_pos"),
    )
    op.create_table(
        "otp_challenge",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("phone_e164", sa.Text(), nullable=False),
        sa.Column("code_hash", HASH, nullable=False),
        sa.Column("pepper_ref", sa.Text(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="PENDING"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("verified_at", TS, nullable=True),
        sa.Column("consumed_at", TS, nullable=True),
        sa.CheckConstraint("length(code_hash) = 66", name="ck_otp_challenge_code_hash_len"),
        _enum_check(
            "status",
            ("PENDING", "VERIFIED", "CONSUMED", "EXPIRED", "LOCKED"),
            "ck_otp_challenge_status_enum",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_otp_challenge_attempts_nonneg"),
        sa.CheckConstraint(
            "max_attempts BETWEEN 1 AND 10", name="ck_otp_challenge_max_attempts_range"
        ),
        sa.CheckConstraint(
            "(verified_at IS NOT NULL) = (status IN ('VERIFIED','CONSUMED'))",
            name="ck_ch_verified",
        ),
        sa.CheckConstraint(
            "(consumed_at IS NOT NULL) = (status = 'CONSUMED')", name="ck_ch_consumed"
        ),
        sa.CheckConstraint("attempts <= max_attempts", name="ck_ch_attempts"),
        sa.CheckConstraint("expires_at > created_at", name="ck_ch_window"),
    )
    op.create_table(
        "phone_throttle",
        sa.Column("phone_e164", sa.Text(), primary_key=True),
        sa.Column("window_start", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("sent_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_sent_at", TS, nullable=True),
        sa.Column("locked_until", TS, nullable=True),
        sa.CheckConstraint("sent_count >= 0", name="ck_phone_throttle_sent_count_nonneg"),
    )
    op.create_table(
        "projection_watermark",
        sa.Column("id", sa.Boolean(), primary_key=True, server_default=sa.true()),
        sa.Column("last_block", sa.BigInteger(), nullable=False),
        sa.Column("last_block_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("id", name="ck_watermark_single_row"),
        sa.CheckConstraint("last_block >= 0", name="ck_watermark_last_block_nonneg"),
    )
    op.create_table(
        "reorg_log",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("detected_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("fork_block", sa.BigInteger(), nullable=False),
        sa.Column("old_tip", sa.BigInteger(), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("finality_depth", sa.Integer(), nullable=False),
        sa.Column("orphaned_events", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(40), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.CheckConstraint("fork_block > 0", name="ck_reorg_fork_block_pos"),
        sa.CheckConstraint("depth >= 1", name="ck_reorg_depth_min"),
        sa.CheckConstraint("orphaned_events >= 0", name="ck_reorg_orphaned_nonneg"),
        _enum_check("outcome", ("ROLLED_BACK", "MANUAL_REVIEW"), "ck_reorg_outcome_enum"),
        sa.CheckConstraint("depth = old_tip - fork_block + 1", name="ck_depth_matches"),
    )
    op.create_table(
        "thb_balance",
        sa.Column("account", sa.String(40), primary_key=True),
        sa.Column("account_id", sa.Text(), primary_key=True),
        sa.Column("amount", AMOUNT, nullable=False, server_default="0"),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        _enum_check(
            "account",
            ("CUSTOMER_THB", "MERCHANT_THB", "PLATFORM_THB", "ISSUANCE"),
            "ck_thb_balance_account_enum",
        ),
        # ck_balance_sign (amount >= 0 OR account = 'ISSUANCE') is dropped: amount is a decimal
        # string (uint256), and Postgres has no `>=` operator between varchar and integer, so this
        # invariant moves to application code along with the rest of the ledger business logic.
    )
    op.create_table(
        "psp_webhook",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_event_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("raw_body", sa.LargeBinary(), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("signature_ok", sa.Boolean(), nullable=False),
        sa.Column("received_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("processed_at", TS, nullable=True),
        sa.UniqueConstraint("provider", "provider_event_id", name="uq_psp_event"),
    )

    # --- tier 1: depend only on tier 0 --------------------------------------------------------
    op.create_table(
        "merchant_admin",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("merchant_id", sa.Text(), sa.ForeignKey("merchant.id"), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("email_normalized", sa.Text(), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("disabled_at", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("last_login_at", TS, nullable=True),
        sa.CheckConstraint(
            "email_normalized = lower(trim(email_normalized))", name="ck_admin_email_normalized"
        ),
        sa.CheckConstraint("password_hash LIKE '$argon2id$%'", name="ck_admin_password_hash"),
    )
    op.create_table(
        "point_type",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("merchant_id", sa.Text(), sa.ForeignKey("merchant.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("decimals", sa.SmallInteger(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="REQUESTED"),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("live_at", TS, nullable=True),
        sa.Column("retired_at", TS, nullable=True),
        sa.CheckConstraint("length(trim(name)) BETWEEN 1 AND 60", name="ck_point_type_name_len"),
        sa.CheckConstraint("decimals BETWEEN 0 AND 30", name="ck_point_type_decimals_range"),
        _enum_check(
            "status", ("REQUESTED", "LIVE", "FAILED", "RETIRED"), "ck_point_type_status_enum"
        ),
        sa.CheckConstraint(
            "(status = 'LIVE') = (live_at IS NOT NULL) OR status = 'RETIRED'",
            name="ck_point_type_live",
        ),
        sa.CheckConstraint(
            "(status = 'RETIRED') = (retired_at IS NOT NULL)", name="ck_point_type_retired"
        ),
    )
    op.create_table(
        "coupon_type",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("merchant_id", sa.Text(), sa.ForeignKey("merchant.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("face_value", AMOUNT, nullable=False),
        sa.Column("total_supply", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="REQUESTED"),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("live_at", TS, nullable=True),
        sa.Column("contract_address", ADDRESS, nullable=True),
        sa.Column("chain_type_id", AMOUNT, nullable=True),
        sa.CheckConstraint("length(trim(name)) BETWEEN 1 AND 120", name="ck_coupon_type_name_len"),
        sa.CheckConstraint(
            "total_supply BETWEEN 1 AND 1000000", name="ck_coupon_type_supply_range"
        ),
        _enum_check(
            "status", ("REQUESTED", "LIVE", "FAILED", "RETIRED"), "ck_coupon_type_status_enum"
        ),
        sa.CheckConstraint(
            "contract_address IS NULL OR length(contract_address) = 42",
            name="ck_coupon_type_contract_len",
        ),
        sa.CheckConstraint("expires_at > created_at", name="ck_coupon_type_expiry"),
        sa.CheckConstraint(
            "(status = 'LIVE') = (live_at IS NOT NULL) OR status = 'RETIRED'",
            name="ck_coupon_type_live",
        ),
        sa.UniqueConstraint("contract_address", "chain_type_id", name="uq_coupon_type_chain"),
    )
    op.create_table(
        "webhook_endpoint",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("merchant_id", sa.Text(), sa.ForeignKey("merchant.id"), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("secret_ref", sa.Text(), nullable=False),
        sa.Column("event_types", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="ACTIVE"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="6"),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("url NOT LIKE 'http://%'", name="ck_https_only"),
        _enum_check("status", ("ACTIVE", "PAUSED", "DISABLED"), "ck_webhook_endpoint_status_enum"),
        sa.CheckConstraint(
            "max_attempts BETWEEN 1 AND 12", name="ck_webhook_endpoint_max_attempts_range"
        ),
    )
    op.create_table(
        "thb_deposit",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("holder_kind", sa.String(40), nullable=False),
        sa.Column("holder_id", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_ref", sa.Text(), nullable=False),
        sa.Column("amount_satang", sa.BigInteger(), nullable=False),
        sa.Column("amount_base", AMOUNT, nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="PENDING_PAYMENT"),
        sa.Column("webhook_id", sa.Text(), sa.ForeignKey("psp_webhook.id"), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("confirmed_at", TS, nullable=True),
        sa.Column("minted_at", TS, nullable=True),
        # Source default was `now() + interval '24 hours'`; the inserting use case must set this.
        sa.Column("expires_at", TS, nullable=False),
        _enum_check("holder_kind", HOLDER_KIND, "ck_thb_deposit_holder_kind_enum"),
        sa.CheckConstraint("amount_satang > 0", name="ck_thb_deposit_satang_pos"),
        # ck_thb_deposit_base_pos (amount_base > 0) is dropped: amount_base is a decimal-string
        # amount; see the note on ck_balance_sign above.
        _enum_check(
            "status",
            ("PENDING_PAYMENT", "CONFIRMED", "MINTED", "REVERSED", "FAILED", "EXPIRED"),
            "ck_thb_deposit_status_enum",
        ),
        sa.CheckConstraint(
            "(confirmed_at IS NOT NULL) = (status IN ('CONFIRMED','MINTED','REVERSED'))",
            name="ck_dep_confirmed",
        ),
        sa.CheckConstraint(
            "(minted_at IS NOT NULL) = (status IN ('MINTED','REVERSED'))", name="ck_dep_minted"
        ),
        sa.CheckConstraint(
            "status IN ('PENDING_PAYMENT','FAILED','EXPIRED') OR webhook_id IS NOT NULL",
            name="ck_dep_needs_webhook",
        ),
        sa.UniqueConstraint("provider", "provider_ref", name="uq_deposit_provider_ref"),
    )
    op.create_table(
        "otp_attempt",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "challenge_id",
            sa.Text(),
            sa.ForeignKey("otp_challenge.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("result", sa.String(40), nullable=False),
        sa.Column("ip_hash", HASH, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("attempt_no >= 1", name="ck_otp_attempt_no_min"),
        _enum_check(
            "result",
            ("OK", "WRONG_CODE", "EXPIRED", "LOCKED", "ALREADY_USED"),
            "ck_otp_attempt_result_enum",
        ),
        sa.CheckConstraint(
            "ip_hash IS NULL OR length(ip_hash) = 66", name="ck_otp_attempt_ip_hash_len"
        ),
        sa.UniqueConstraint("challenge_id", "attempt_no", name="uq_attempt_no"),
    )
    op.create_table(
        "escrow",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("chain_token_id", AMOUNT, nullable=False),
        sa.Column("contract_address", ADDRESS, nullable=False),
        sa.Column("seller_wallet_id", sa.Text(), sa.ForeignKey("wallet.id"), nullable=False),
        sa.Column("buyer_wallet_id", sa.Text(), sa.ForeignKey("wallet.id"), nullable=False),
        sa.Column("total_amount", AMOUNT, nullable=False),
        sa.Column("remaining_amount", AMOUNT, nullable=False),
        sa.Column("coupons_locked", sa.Integer(), nullable=False),
        sa.Column("coupons_redeemed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("locked_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("length(contract_address) = 42", name="ck_escrow_contract_len"),
        sa.CheckConstraint("coupons_locked > 0", name="ck_escrow_locked_pos"),
        sa.CheckConstraint("coupons_redeemed >= 0", name="ck_escrow_redeemed_nonneg"),
        # ck_escrow_remaining (remaining_amount <= total_amount) is dropped: both columns are
        # decimal-string uint256 values, and SQLite compares same-affinity TEXT columns
        # lexicographically rather than numerically, so this invariant belongs in application code.
        sa.CheckConstraint("coupons_redeemed <= coupons_locked", name="ck_escrow_count"),
    )
    op.create_table(
        "chain_event",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("block_number", sa.BigInteger(), nullable=False),
        sa.Column("block_hash", HASH, sa.ForeignKey("raw_block.block_hash"), nullable=False),
        sa.Column("tx_hash", HASH, nullable=False),
        sa.Column("log_index", sa.Integer(), nullable=False),
        sa.Column("address", ADDRESS, nullable=False),
        sa.Column("topic0", HASH, nullable=False),
        sa.Column("topics", sa.JSON(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("block_timestamp", TS, nullable=False),
        sa.Column("event_name", sa.String(40), nullable=False, server_default="UNKNOWN"),
        sa.Column("args", sa.JSON(), nullable=True),
        sa.Column("operation_id", sa.Text(), sa.ForeignKey("operation.id"), nullable=True),
        sa.Column("applied_at", TS, nullable=True),
        sa.Column("orphaned_at", TS, nullable=True),
        sa.Column("finalized_at", TS, nullable=True),
        sa.CheckConstraint("length(tx_hash) = 66", name="ck_chain_event_tx_hash_len"),
        sa.CheckConstraint("log_index >= 0", name="ck_chain_event_log_index_nonneg"),
        sa.CheckConstraint("length(address) = 42", name="ck_chain_event_address_len"),
        sa.CheckConstraint("length(topic0) = 66", name="ck_chain_event_topic0_len"),
        _enum_check(
            "event_name",
            ("CouponTransfer", "PointTransfer", "CouponRedeemed", "UNKNOWN"),
            "ck_chain_event_name_enum",
        ),
        sa.CheckConstraint("event_name = 'UNKNOWN' OR args IS NOT NULL", name="ck_known_has_args"),
        sa.CheckConstraint(
            "orphaned_at IS NULL OR applied_at IS NULL", name="ck_orphan_not_applied"
        ),
        sa.UniqueConstraint("tx_hash", "log_index", name="uq_chain_event"),
    )
    op.create_table(
        "customer_session",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("customer_id", sa.Text(), sa.ForeignKey("customer.id"), nullable=False),
        sa.Column("token_hash", HASH, nullable=False, unique=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("revoked_at", TS, nullable=True),
        sa.CheckConstraint("length(token_hash) = 66", name="ck_customer_session_hash_len"),
        sa.CheckConstraint("expires_at > created_at", name="ck_session_window"),
    )
    op.create_table(
        "idempotency_key",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("fingerprint", HASH, nullable=False),
        sa.Column("operation_id", sa.Text(), sa.ForeignKey("operation.id"), nullable=True),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.JSON(), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        # Source default was `now() + interval '72 hours'`; the inserting use case must set this.
        sa.Column("expires_at", TS, nullable=False),
        sa.CheckConstraint("length(key) BETWEEN 8 AND 255", name="ck_idem_key_len"),
        sa.CheckConstraint("length(fingerprint) = 66", name="ck_idem_fingerprint_len"),
        sa.UniqueConstraint("scope", "key", name="uq_idem"),
    )
    op.create_table(
        "reservation",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "operation_id",
            sa.Text(),
            sa.ForeignKey("operation.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subject_type", sa.String(40), nullable=False),
        sa.Column("subject_id", sa.Text(), nullable=False),
        sa.Column("amount", AMOUNT, nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("released_at", TS, nullable=True),
        _enum_check(
            "subject_type",
            ("POINT_BALANCE", "COUPON_UNIT", "LISTING", "THB_BALANCE", "ESCROW"),
            "ck_reservation_subject_enum",
        ),
    )
    op.create_table(
        "outbox",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "operation_id",
            sa.Text(),
            sa.ForeignKey("operation.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("sent_at", TS, nullable=True),
    )
    op.create_table(
        "operation_event",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "operation_id",
            sa.Text(),
            sa.ForeignKey("operation.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(40), nullable=True),
        sa.Column("to_status", sa.String(40), nullable=False),
        sa.Column("actor", sa.String(40), nullable=False, server_default="SYSTEM"),
        sa.Column("tx_hash", HASH, nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("seq >= 1", name="ck_operation_event_seq_min"),
        _enum_check(
            "actor",
            ("SYSTEM", "MERCHANT_ADMIN", "CUSTOMER", "PLATFORM_ADMIN"),
            "ck_operation_event_actor_enum",
        ),
        sa.CheckConstraint(
            "tx_hash IS NULL OR length(tx_hash) = 66", name="ck_operation_event_tx_hash_len"
        ),
        sa.CheckConstraint("(seq = 1) = (from_status IS NULL)", name="ck_first_event"),
        sa.UniqueConstraint("operation_id", "seq", name="uq_opevent_seq"),
    )
    op.create_table(
        "operation_step",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "operation_id",
            sa.Text(),
            sa.ForeignKey("operation.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("recipient_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("signer_address", ADDRESS, sa.ForeignKey("signer.address"), nullable=True),
        sa.Column("nonce", sa.BigInteger(), nullable=True),
        sa.Column("raw_tx", sa.LargeBinary(), nullable=True),
        sa.Column("tx_hash", HASH, nullable=True),
        sa.Column("status", sa.String(40), nullable=False, server_default="PENDING"),
        sa.Column("submitted_at", TS, nullable=True),
        sa.Column("confirmed_at", TS, nullable=True),
        sa.Column("finalized_at", TS, nullable=True),
        sa.Column("mint_count", sa.Integer(), nullable=True, server_default="1"),
        sa.CheckConstraint("step_index >= 1", name="ck_operation_step_index_min"),
        sa.CheckConstraint(
            "recipient_count BETWEEN 1 AND 100", name="ck_operation_step_recipients_range"
        ),
        sa.CheckConstraint("nonce IS NULL OR nonce >= 0", name="ck_operation_step_nonce_nonneg"),
        sa.CheckConstraint(
            "tx_hash IS NULL OR length(tx_hash) = 66", name="ck_operation_step_tx_hash_len"
        ),
        _enum_check(
            "status",
            ("PENDING", "SUBMITTED", "CONFIRMED", "FINALIZED", "CHAIN_REVERTED", "ORPHANED"),
            "ck_operation_step_status_enum",
        ),
        sa.CheckConstraint(
            "mint_count BETWEEN 1 AND 500", name="ck_operation_step_mint_count_range"
        ),
        sa.CheckConstraint(
            "status = 'PENDING' OR raw_tx IS NOT NULL", name="ck_rawtx_before_broadcast"
        ),
        sa.UniqueConstraint("operation_id", "step_index", name="uq_step_order"),
        sa.UniqueConstraint("operation_id", "name", name="uq_step_name"),
    )
    op.create_table(
        "point_balance",
        sa.Column("point_type_id", sa.Text(), sa.ForeignKey("point_type.id"), primary_key=True),
        sa.Column("holder_kind", sa.String(40), primary_key=True),
        sa.Column("holder_id", sa.Text(), primary_key=True),
        sa.Column("amount", AMOUNT, nullable=False),
        sa.Column("as_of_block", sa.BigInteger(), nullable=False),
        _enum_check("holder_kind", HOLDER_KIND, "ck_point_balance_holder_kind_enum"),
        # ck_point_balance_amount_nonneg (amount >= 0) is dropped: decimal-string amount; see the
        # note on ck_balance_sign above.
    )
    op.create_table(
        "point_type_chain",
        sa.Column("point_type_id", sa.Text(), sa.ForeignKey("point_type.id"), primary_key=True),
        sa.Column("contract_address", ADDRESS, nullable=False, unique=True),
        sa.Column("deployed_block", sa.BigInteger(), nullable=False),
        sa.Column("deployed_tx_hash", HASH, nullable=False),
        sa.Column("genesis_at", TS, nullable=False),
        sa.Column("epoch_seconds", sa.Integer(), nullable=False),
        sa.Column("validity_epochs", sa.Integer(), nullable=False),
        sa.Column("applied_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("length(contract_address) = 42", name="ck_point_chain_contract_len"),
        sa.CheckConstraint("deployed_block > 0", name="ck_point_chain_block_pos"),
        sa.CheckConstraint("length(deployed_tx_hash) = 66", name="ck_point_chain_tx_hash_len"),
        sa.CheckConstraint("epoch_seconds > 0", name="ck_point_chain_epoch_pos"),
        sa.CheckConstraint("validity_epochs > 0", name="ck_point_chain_validity_pos"),
    )
    op.create_table(
        "point_balance_bucket",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("point_type_id", sa.Text(), sa.ForeignKey("point_type.id"), nullable=False),
        sa.Column("holder_kind", sa.String(40), nullable=False),
        sa.Column("holder_id", sa.Text(), nullable=False),
        sa.Column("epoch_index", sa.Integer(), nullable=False),
        sa.Column("amount", AMOUNT, nullable=False),
        sa.Column("received_at", TS, nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        _enum_check("holder_kind", HOLDER_KIND, "ck_point_bucket_holder_kind_enum"),
        sa.CheckConstraint("epoch_index >= 0", name="ck_point_bucket_epoch_nonneg"),
        # ck_point_bucket_amount_nonneg (amount >= 0) is dropped: decimal-string amount; see the
        # note on ck_balance_sign above.
        sa.CheckConstraint("expires_at > received_at", name="ck_bucket_expiry"),
        sa.UniqueConstraint(
            "point_type_id", "holder_kind", "holder_id", "epoch_index", name="uq_bucket"
        ),
    )

    # --- tier 2: depend on tier 0/1 ------------------------------------------------------------
    op.create_table(
        "auth_session",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("subject_kind", sa.String(40), nullable=False),
        sa.Column(
            "merchant_admin_id", sa.Text(), sa.ForeignKey("merchant_admin.id"), nullable=True
        ),
        sa.Column("customer_id", sa.Text(), sa.ForeignKey("customer.id"), nullable=True),
        sa.Column("family_id", sa.Text(), nullable=False),
        sa.Column("refresh_token_hash", HASH, nullable=False, unique=True),
        sa.Column("parent_id", sa.Text(), sa.ForeignKey("auth_session.id"), nullable=True),
        sa.Column("user_agent_hash", sa.LargeBinary(), nullable=True),
        sa.Column("ip_hash", sa.LargeBinary(), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("used_at", TS, nullable=True),
        sa.Column("revoked_at", TS, nullable=True),
        sa.Column("revoked_reason", sa.String(40), nullable=True),
        _enum_check(
            "subject_kind",
            ("MERCHANT_ADMIN", "CUSTOMER", "PLATFORM_ADMIN"),
            "ck_auth_session_subject_kind_enum",
        ),
        sa.CheckConstraint("length(refresh_token_hash) = 66", name="ck_auth_session_token_len"),
        sa.CheckConstraint(
            "(subject_kind = 'MERCHANT_ADMIN' AND merchant_admin_id IS NOT NULL)"
            " OR (subject_kind = 'CUSTOMER' AND customer_id IS NOT NULL)",
            name="ck_subject_matches",
        ),
        sa.CheckConstraint("expires_at > created_at", name="ck_expiry_after_create"),
        sa.CheckConstraint(
            "(revoked_at IS NULL) = (revoked_reason IS NULL)", name="ck_revoke_has_reason"
        ),
    )
    op.create_table(
        "issue_batch",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("coupon_type_id", sa.Text(), sa.ForeignKey("coupon_type.id"), nullable=False),
        sa.Column("operation_id", sa.Text(), sa.ForeignKey("operation.id"), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("quantity BETWEEN 1 AND 10000", name="ck_issue_batch_quantity_range"),
    )
    op.create_table(
        "ledger_entry",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("operation_id", sa.Text(), sa.ForeignKey("operation.id"), nullable=False),
        sa.Column("entry_seq", sa.Integer(), nullable=False),
        sa.Column("account", sa.String(40), nullable=False),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("asset", sa.Text(), nullable=False, server_default="THB"),
        sa.Column("amount", AMOUNT, nullable=False),
        sa.Column("memo", sa.Text(), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("point_type_id", sa.Text(), sa.ForeignKey("point_type.id"), nullable=True),
        sa.Column("holder_kind", sa.String(40), nullable=True),
        sa.Column("holder_id", sa.Text(), nullable=True),
        sa.Column("direction", sa.String(40), nullable=True),
        sa.Column("chain_event_id", sa.Text(), nullable=True),
        sa.CheckConstraint("entry_seq >= 1", name="ck_ledger_entry_seq_min"),
        _enum_check(
            "account",
            ("CUSTOMER_THB", "MERCHANT_THB", "PLATFORM_THB", "ISSUANCE"),
            "ck_ledger_entry_account_enum",
        ),
        sa.CheckConstraint("asset = 'THB'", name="ck_ledger_entry_asset"),
        # ck_ledger_entry_amount_nonzero (amount <> 0) is dropped: decimal-string amount; see the
        # note on ck_balance_sign above.
        sa.CheckConstraint(
            "holder_kind IS NULL OR holder_kind IN ('MERCHANT', 'CUSTOMER')",
            name="ck_ledger_entry_holder_kind_enum",
        ),
        sa.CheckConstraint(
            "direction IS NULL OR direction IN ('DEBIT', 'CREDIT')",
            name="ck_ledger_entry_direction_enum",
        ),
        sa.UniqueConstraint("operation_id", "entry_seq", name="uq_ledger_seq"),
    )
    op.create_table(
        "listing",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("seller_kind", sa.String(40), nullable=False),
        sa.Column("seller_id", sa.Text(), nullable=False),
        sa.Column("coupon_type_id", sa.Text(), sa.ForeignKey("coupon_type.id"), nullable=False),
        sa.Column("unit_price", AMOUNT, nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="PENDING"),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("active_at", TS, nullable=True),
        sa.Column("closed_at", TS, nullable=True),
        _enum_check("seller_kind", HOLDER_KIND, "ck_listing_seller_kind_enum"),
        sa.CheckConstraint("quantity BETWEEN 1 AND 500", name="ck_listing_quantity_range"),
        _enum_check(
            "status",
            ("PENDING", "ACTIVE", "SOLD", "DELISTED", "FAILED"),
            "ck_listing_status_enum",
        ),
        sa.CheckConstraint(
            "(status = 'ACTIVE') = (active_at IS NOT NULL) OR status IN ('SOLD','DELISTED')",
            name="ck_listing_active",
        ),
        sa.CheckConstraint(
            "(status IN ('SOLD','DELISTED')) = (closed_at IS NOT NULL)",
            name="ck_listing_closed",
        ),
        sa.CheckConstraint(
            "closed_at IS NULL OR closed_at >= active_at", name="ck_listing_close_order"
        ),
    )
    op.create_table(
        "coupon_type_chain",
        sa.Column("coupon_type_id", sa.Text(), sa.ForeignKey("coupon_type.id"), primary_key=True),
        sa.Column("contract_address", ADDRESS, nullable=False),
        sa.Column("chain_type_id", AMOUNT, nullable=False),
        sa.Column("deployed_block", sa.BigInteger(), nullable=False),
        sa.Column("deployed_tx_hash", HASH, nullable=False),
        sa.Column("applied_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "length(contract_address) = 42", name="ck_coupon_type_chain_contract_len"
        ),
        sa.CheckConstraint("deployed_block > 0", name="ck_coupon_type_chain_block_pos"),
        sa.CheckConstraint(
            "length(deployed_tx_hash) = 66", name="ck_coupon_type_chain_tx_hash_len"
        ),
        sa.UniqueConstraint("contract_address", "chain_type_id", name="uq_coupon_chain"),
    )
    op.create_table(
        "webhook_delivery",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("endpoint_id", sa.Text(), sa.ForeignKey("webhook_endpoint.id"), nullable=False),
        sa.Column("operation_id", sa.Text(), sa.ForeignKey("operation.id"), nullable=False),
        sa.Column("event_seq", sa.Integer(), nullable=False),
        sa.Column("delivery_seq", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("signed_at", TS, nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="PENDING"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("claimed_at", TS, nullable=True),
        sa.Column("sent_at", TS, nullable=True),
        sa.Column("dead_at", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("replay_of", sa.Text(), sa.ForeignKey("webhook_delivery.id"), nullable=True),
        sa.CheckConstraint("event_seq >= 1", name="ck_webhook_delivery_event_seq_min"),
        sa.CheckConstraint("delivery_seq >= 1", name="ck_webhook_delivery_delivery_seq_min"),
        _enum_check(
            "status",
            ("PENDING", "IN_FLIGHT", "SENT", "DEAD"),
            "ck_webhook_delivery_status_enum",
        ),
        sa.CheckConstraint("attempt >= 0", name="ck_webhook_delivery_attempt_nonneg"),
        sa.CheckConstraint("(status = 'SENT') = (sent_at IS NOT NULL)", name="ck_delivery_sent"),
        sa.CheckConstraint("(status = 'DEAD') = (dead_at IS NOT NULL)", name="ck_delivery_dead"),
        sa.CheckConstraint(
            "status <> 'IN_FLIGHT' OR claimed_at IS NOT NULL", name="ck_delivery_claim"
        ),
        sa.CheckConstraint("replay_of IS NULL OR replay_of <> id", name="ck_no_self_replay"),
        sa.UniqueConstraint("endpoint_id", "operation_id", "delivery_seq", name="uq_delivery_seq"),
    )
    op.create_table(
        "deposit_reversal",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "deposit_id", sa.Text(), sa.ForeignKey("thb_deposit.id"), nullable=False, unique=True
        ),
        sa.Column("webhook_id", sa.Text(), sa.ForeignKey("psp_webhook.id"), nullable=False),
        sa.Column("operation_id", sa.Text(), sa.ForeignKey("operation.id"), nullable=False),
        sa.Column("amount_base", AMOUNT, nullable=False),
        sa.Column("available_at_reversal", AMOUNT, nullable=False),
        sa.Column("outcome", sa.String(40), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        # ck_deposit_reversal_amount_pos (amount_base > 0) and
        # ck_deposit_reversal_available_nonneg (available_at_reversal >= 0) are dropped:
        # decimal-string amounts; see the note on ck_balance_sign above.
        _enum_check("outcome", ("BURNED", "MANUAL_REVIEW"), "ck_deposit_reversal_outcome_enum"),
    )
    op.create_table(
        "thb_mint",
        sa.Column("deposit_id", sa.Text(), sa.ForeignKey("thb_deposit.id"), primary_key=True),
        sa.Column("operation_id", sa.Text(), sa.ForeignKey("operation.id"), nullable=False),
        sa.Column("amount_base", AMOUNT, nullable=False),
        sa.Column("to_account", sa.String(40), nullable=False),
        sa.Column("to_account_id", sa.Text(), nullable=False),
        sa.Column("chain_event_id", sa.Text(), sa.ForeignKey("chain_event.id"), nullable=True),
        sa.Column("tx_hash", HASH, nullable=True),
        sa.Column("minted_block", sa.BigInteger(), nullable=True),
        sa.Column("applied_at", TS, nullable=True),
        # ck_thb_mint_amount_pos (amount_base > 0) is dropped: decimal-string amount; see the
        # note on ck_balance_sign above.
        _enum_check(
            "to_account",
            ("CUSTOMER_THB", "MERCHANT_THB", "PLATFORM_THB", "ISSUANCE"),
            "ck_thb_mint_to_account_enum",
        ),
        sa.CheckConstraint(
            "tx_hash IS NULL OR length(tx_hash) = 66", name="ck_thb_mint_tx_hash_len"
        ),
        sa.CheckConstraint(
            "minted_block IS NULL OR minted_block > 0", name="ck_thb_mint_block_pos"
        ),
        sa.CheckConstraint(
            "(applied_at IS NULL) = (chain_event_id IS NULL)", name="ck_mint_applied"
        ),
        sa.UniqueConstraint("operation_id", name="uq_mint_operation"),
    )
    op.create_table(
        "expiry_notice",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "bucket_id",
            sa.Text(),
            sa.ForeignKey("point_balance_bucket.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("threshold_days", sa.Integer(), nullable=False),
        sa.Column("notified_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("channel", sa.String(40), nullable=False),
        sa.CheckConstraint("threshold_days IN (30, 7, 1)", name="ck_expiry_notice_threshold"),
        _enum_check("channel", ("PUSH", "EMAIL", "SMS"), "ck_expiry_notice_channel_enum"),
        sa.UniqueConstraint("bucket_id", "threshold_days", name="uq_notice"),
    )

    # --- tier 3: depend on tier 2 ---------------------------------------------------------------
    op.create_table(
        "auth_event",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("subject_kind", sa.String(40), nullable=True),
        sa.Column("subject_id", sa.Text(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), sa.ForeignKey("auth_session.id"), nullable=True),
        sa.Column("ip_hash", sa.LargeBinary(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "subject_kind IS NULL"
            " OR subject_kind IN ('MERCHANT_ADMIN', 'CUSTOMER', 'PLATFORM_ADMIN')",
            name="ck_auth_event_subject_kind_enum",
        ),
    )
    op.create_table(
        "coupon_code",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("coupon_type_id", sa.Text(), sa.ForeignKey("coupon_type.id"), nullable=False),
        sa.Column("code_hash", HASH, nullable=False, unique=True),
        sa.Column("code_ref", HASH, nullable=False, unique=True),
        sa.Column("state", sa.String(40), nullable=False, server_default="ISSUED"),
        sa.Column("holder_kind", sa.String(40), nullable=True),
        sa.Column("holder_id", sa.Text(), nullable=True),
        sa.Column("issue_batch_id", sa.Text(), sa.ForeignKey("issue_batch.id"), nullable=True),
        sa.Column("issued_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("held_at", TS, nullable=True),
        sa.Column("redeemed_at", TS, nullable=True),
        sa.CheckConstraint("length(code_hash) = 66", name="ck_coupon_code_hash_len"),
        sa.CheckConstraint("length(code_ref) = 66", name="ck_coupon_code_ref_len"),
        _enum_check(
            "state",
            ("ISSUED", "HELD", "REDEEMED", "EXPIRED", "VOIDED"),
            "ck_coupon_code_state_enum",
        ),
        sa.CheckConstraint(
            "holder_kind IS NULL OR holder_kind IN ('MERCHANT', 'CUSTOMER')",
            name="ck_coupon_code_holder_kind_enum",
        ),
        sa.CheckConstraint("(holder_kind IS NULL) = (holder_id IS NULL)", name="ck_code_holder"),
        sa.CheckConstraint("state <> 'HELD' OR holder_id IS NOT NULL", name="ck_code_held"),
        sa.CheckConstraint(
            "(state = 'REDEEMED') = (redeemed_at IS NOT NULL)", name="ck_code_redeemed"
        ),
        sa.Index("ix_code_type_state", "coupon_type_id", "state"),
    )
    op.create_table(
        "listing_chain",
        sa.Column("listing_id", sa.Text(), sa.ForeignKey("listing.id"), primary_key=True),
        sa.Column("contract_address", ADDRESS, nullable=False),
        sa.Column("chain_listing_id", AMOUNT, nullable=False),
        sa.Column("created_block", sa.BigInteger(), nullable=False),
        sa.Column("created_tx_hash", HASH, nullable=False),
        sa.Column("closed_block", sa.BigInteger(), nullable=True),
        sa.Column("closed_tx_hash", HASH, nullable=True),
        sa.Column("close_reason", sa.String(40), nullable=True),
        sa.Column("applied_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("length(contract_address) = 42", name="ck_listing_chain_contract_len"),
        sa.CheckConstraint("created_block > 0", name="ck_listing_chain_created_block_pos"),
        sa.CheckConstraint("length(created_tx_hash) = 66", name="ck_listing_chain_created_tx_len"),
        sa.CheckConstraint(
            "closed_block IS NULL OR closed_block > 0", name="ck_listing_chain_closed_block_pos"
        ),
        sa.CheckConstraint(
            "closed_tx_hash IS NULL OR length(closed_tx_hash) = 66",
            name="ck_listing_chain_closed_tx_len",
        ),
        sa.CheckConstraint(
            "close_reason IS NULL OR close_reason IN ('CANCELLED', 'SOLD')",
            name="ck_listing_chain_close_reason_enum",
        ),
        sa.CheckConstraint(
            "(close_reason IS NULL) = (closed_block IS NULL)"
            " AND (close_reason IS NULL) = (closed_tx_hash IS NULL)",
            name="ck_chain_close",
        ),
        sa.CheckConstraint(
            "closed_block IS NULL OR closed_block >= created_block",
            name="ck_chain_close_order",
        ),
        sa.UniqueConstraint("contract_address", "chain_listing_id", name="uq_listing_chain"),
    )
    op.create_table(
        "purchase",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("listing_id", sa.Text(), sa.ForeignKey("listing.id"), nullable=False),
        sa.Column("buyer_kind", sa.String(40), nullable=False),
        sa.Column("buyer_id", sa.Text(), nullable=False),
        sa.Column("seller_kind", sa.String(40), nullable=False),
        sa.Column("seller_id", sa.Text(), nullable=False),
        sa.Column("unit_price", AMOUNT, nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("total_price", AMOUNT, nullable=False),
        sa.Column("fee_bps", sa.Integer(), nullable=False),
        sa.Column("fee_amount", AMOUNT, nullable=False),
        sa.Column("net_to_seller", AMOUNT, nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="PENDING"),
        sa.Column("operation_id", sa.Text(), sa.ForeignKey("operation.id"), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("settled_at", TS, nullable=True),
        _enum_check("buyer_kind", HOLDER_KIND, "ck_purchase_buyer_kind_enum"),
        _enum_check("seller_kind", HOLDER_KIND, "ck_purchase_seller_kind_enum"),
        sa.CheckConstraint("quantity > 0", name="ck_purchase_quantity_pos"),
        sa.CheckConstraint("fee_bps BETWEEN 0 AND 10000", name="ck_purchase_fee_bps_range"),
        # ck_purchase_unit_price_pos, ck_purchase_total_price_pos, ck_purchase_fee_amount_nonneg,
        # and ck_purchase_net_nonneg are dropped: all four are decimal-string amounts; see the
        # note on ck_balance_sign above.
        _enum_check("status", ("PENDING", "SETTLED", "FAILED"), "ck_purchase_status_enum"),
        sa.CheckConstraint(
            "(status = 'SETTLED') = (settled_at IS NOT NULL)", name="ck_purchase_settled"
        ),
        sa.CheckConstraint(
            "NOT (buyer_kind = seller_kind AND buyer_id = seller_id)", name="ck_no_self_trade"
        ),
    )
    op.create_table(
        "webhook_delivery_attempt",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "delivery_id",
            sa.Text(),
            sa.ForeignKey("webhook_delivery.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("finished_at", TS, nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column("error_note", sa.Text(), nullable=True),
        sa.CheckConstraint("attempt >= 1", name="ck_webhook_attempt_min"),
        sa.CheckConstraint("finished_at >= started_at", name="ck_attempt_time"),
        sa.CheckConstraint(
            "response_status IS NULL OR response_status BETWEEN 100 AND 599",
            name="ck_webhook_attempt_response_range",
        ),
        sa.CheckConstraint(
            "(response_status IS NULL) <> (error_note IS NULL)", name="ck_attempt_outcome"
        ),
        sa.UniqueConstraint("delivery_id", "attempt", name="uq_attempt_seq"),
    )

    # --- tier 4: depend on tier 3 ----------------------------------------------------------------
    op.create_table(
        "coupon_holding",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("coupon_type_id", sa.Text(), sa.ForeignKey("coupon_type.id"), nullable=False),
        sa.Column("holder_kind", sa.String(40), nullable=False),
        sa.Column("holder_id", sa.Text(), nullable=False),
        sa.Column("quantity", AMOUNT, nullable=False),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("coupon_code_id", sa.Text(), sa.ForeignKey("coupon_code.id"), nullable=True),
        sa.Column("chain_event_id", sa.Text(), sa.ForeignKey("chain_event.id"), nullable=True),
        sa.Column("applied_at", TS, nullable=True, server_default=sa.func.now()),
        sa.Column("as_of_block", sa.BigInteger(), nullable=True),
        sa.Column("as_of_time", TS, nullable=True),
        _enum_check("holder_kind", HOLDER_KIND, "ck_coupon_holding_holder_kind_enum"),
        # ck_coupon_holding_quantity_nonneg (quantity >= 0) is dropped: decimal-string amount;
        # see the note on ck_balance_sign above.
        sa.UniqueConstraint("coupon_type_id", "holder_kind", "holder_id", name="uq_holding"),
    )
    op.create_table(
        "listing_item",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "listing_id",
            sa.Text(),
            sa.ForeignKey("listing.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("coupon_code_id", sa.Text(), sa.ForeignKey("coupon_code.id"), nullable=False),
        sa.UniqueConstraint("listing_id", "coupon_code_id", name="uq_item_in_listing"),
    )


def downgrade() -> None:
    op.drop_table("listing_item")
    op.drop_table("coupon_holding")
    op.drop_table("webhook_delivery_attempt")
    op.drop_table("purchase")
    op.drop_table("listing_chain")
    op.drop_table("coupon_code")
    op.drop_table("auth_event")
    op.drop_table("expiry_notice")
    op.drop_table("thb_mint")
    op.drop_table("deposit_reversal")
    op.drop_table("webhook_delivery")
    op.drop_table("coupon_type_chain")
    op.drop_table("listing")
    op.drop_table("ledger_entry")
    op.drop_table("issue_batch")
    op.drop_table("auth_session")
    op.drop_table("point_balance_bucket")
    op.drop_table("point_type_chain")
    op.drop_table("point_balance")
    op.drop_table("operation_step")
    op.drop_table("operation_event")
    op.drop_table("outbox")
    op.drop_table("reservation")
    op.drop_table("idempotency_key")
    op.drop_table("customer_session")
    op.drop_table("chain_event")
    op.drop_table("escrow")
    op.drop_table("otp_attempt")
    op.drop_table("thb_deposit")
    op.drop_table("webhook_endpoint")
    op.drop_table("coupon_type")
    op.drop_table("point_type")
    op.drop_table("merchant_admin")
    op.drop_table("psp_webhook")
    op.drop_table("thb_balance")
    op.drop_table("reorg_log")
    op.drop_table("projection_watermark")
    op.drop_table("phone_throttle")
    op.drop_table("otp_challenge")
    op.drop_table("login_lockout")
    op.drop_table("login_attempt")
    op.drop_table("indexer_cursor")
    op.drop_table("raw_block")
    op.drop_table("finality_config")
    op.drop_table("fee_schedule")
    op.drop_table("wallet")
    op.drop_table("signer")
    op.drop_table("operation")
    op.drop_table("merchant")
    op.drop_table("customer")
