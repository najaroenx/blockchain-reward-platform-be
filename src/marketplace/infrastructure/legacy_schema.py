"""Schema-only metadata for Alembic's drift check, mirroring migration 0002.

Declares the 50 tables created by migrations/versions/0002_legacy_schema_port.py as plain
`sa.Table` objects on `Base.metadata`, so `alembic check` sees them as declared schema instead of
drift. These intentionally have no ORM-mapped classes, domain entities, or repositories: no
feature has claimed these tables yet (`coupons` is still a package skeleton, and most others have
no decided use case — see docs/architecture.md). A feature that claims one of these tables should
move its `sa.Table` out of here and into that feature's own `infrastructure/models.py` as a proper
mapped class.
"""

import sqlalchemy as sa

from marketplace.infrastructure.database import Base

ADDRESS = sa.String(42)
HASH = sa.String(66)
AMOUNT = sa.String(78)
TS = sa.DateTime(timezone=True)


def _enum_check(column: str, values: tuple[str, ...], name: str) -> sa.CheckConstraint:
    quoted = ", ".join(f"'{v}'" for v in values)
    return sa.CheckConstraint(f"{column} IN ({quoted})", name=name)


HOLDER_KIND = ("MERCHANT", "CUSTOMER")

# --- tier 0: no foreign keys -------------------------------------------------------------------
customer = sa.Table(
    "customer",
    Base.metadata,
    sa.Column("id", sa.Text(), primary_key=True),
    sa.Column("phone_e164", sa.Text(), nullable=False, unique=True),
    sa.Column("display_name", sa.Text(), nullable=True),
    sa.Column("status", sa.String(40), nullable=False, server_default="PENDING_WALLET"),
    sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
    sa.Column("phone_verified_at", TS, nullable=False),
    sa.Column("tel_hash", sa.LargeBinary(), nullable=True, unique=True),
    sa.Column("disabled_at", TS, nullable=True),
    _enum_check(
        "status", ("PENDING_WALLET", "ACTIVE", "SUSPENDED", "CLOSED"), "ck_customer_status_enum"
    ),
)

merchant = sa.Table(
    "merchant",
    Base.metadata,
    sa.Column("id", sa.Text(), primary_key=True),
    sa.Column("name", sa.Text(), nullable=False),
    sa.Column("status", sa.String(40), nullable=False, server_default="PENDING"),
    sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
    sa.Column("activated_at", TS, nullable=True),
    sa.CheckConstraint("length(trim(name)) BETWEEN 1 AND 200", name="ck_merchant_name_len"),
    _enum_check("status", ("PENDING", "ACTIVE", "SUSPENDED", "CLOSED"), "ck_merchant_status_enum"),
    sa.CheckConstraint(
        "(status = 'ACTIVE') = (activated_at IS NOT NULL)", name="ck_merchant_active"
    ),
)

operation = sa.Table(
    "operation",
    Base.metadata,
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

signer = sa.Table(
    "signer",
    Base.metadata,
    sa.Column("address", ADDRESS, primary_key=True),
    sa.Column("key_ref", sa.Text(), nullable=False),
    sa.Column("next_nonce", sa.BigInteger(), nullable=False, server_default="0"),
    sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    sa.CheckConstraint("length(address) = 42", name="ck_signer_address_len"),
    sa.CheckConstraint("next_nonce >= 0", name="ck_signer_nonce_nonneg"),
)

wallet = sa.Table(
    "wallet",
    Base.metadata,
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

fee_schedule = sa.Table(
    "fee_schedule",
    Base.metadata,
    sa.Column("id", sa.Text(), primary_key=True),
    sa.Column("bps", sa.Integer(), nullable=False),
    sa.Column("effective_from", TS, nullable=False, unique=True),
    sa.Column("note", sa.Text(), nullable=True),
    sa.CheckConstraint("bps BETWEEN 0 AND 10000", name="ck_fee_bps_range"),
)

finality_config = sa.Table(
    "finality_config",
    Base.metadata,
    sa.Column("id", sa.Text(), primary_key=True),
    sa.Column("depth_blocks", sa.Integer(), nullable=False),
    sa.Column("effective_from", TS, nullable=False, unique=True),
    sa.Column("note", sa.Text(), nullable=True),
    sa.CheckConstraint("depth_blocks BETWEEN 1 AND 1000", name="ck_finality_depth_range"),
)

raw_block = sa.Table(
    "raw_block",
    Base.metadata,
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

indexer_cursor = sa.Table(
    "indexer_cursor",
    Base.metadata,
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
        "contract_address IS NULL OR length(contract_address) = 42", name="ck_cursor_contract_len"
    ),
    sa.CheckConstraint("(lease_owner IS NULL) = (lease_until IS NULL)", name="ck_lease_pair"),
    sa.CheckConstraint("(last_block = 0) = (last_block_hash IS NULL)", name="ck_cursor_hash"),
)

login_attempt = sa.Table(
    "login_attempt",
    Base.metadata,
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

login_lockout = sa.Table(
    "login_lockout",
    Base.metadata,
    sa.Column("identifier_hash", HASH, primary_key=True),
    sa.Column("locked_until", TS, nullable=False),
    sa.Column("attempt_count", sa.Integer(), nullable=False),
    sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    sa.CheckConstraint("length(identifier_hash) = 66", name="ck_login_lockout_hash_len"),
    sa.CheckConstraint("attempt_count > 0", name="ck_login_lockout_count_pos"),
)

otp_challenge = sa.Table(
    "otp_challenge",
    Base.metadata,
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
    sa.CheckConstraint("max_attempts BETWEEN 1 AND 10", name="ck_otp_challenge_max_attempts_range"),
    sa.CheckConstraint(
        "(verified_at IS NOT NULL) = (status IN ('VERIFIED','CONSUMED'))", name="ck_ch_verified"
    ),
    sa.CheckConstraint("(consumed_at IS NOT NULL) = (status = 'CONSUMED')", name="ck_ch_consumed"),
    sa.CheckConstraint("attempts <= max_attempts", name="ck_ch_attempts"),
    sa.CheckConstraint("expires_at > created_at", name="ck_ch_window"),
)

phone_throttle = sa.Table(
    "phone_throttle",
    Base.metadata,
    sa.Column("phone_e164", sa.Text(), primary_key=True),
    sa.Column("window_start", TS, nullable=False, server_default=sa.func.now()),
    sa.Column("sent_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("last_sent_at", TS, nullable=True),
    sa.Column("locked_until", TS, nullable=True),
    sa.CheckConstraint("sent_count >= 0", name="ck_phone_throttle_sent_count_nonneg"),
)

projection_watermark = sa.Table(
    "projection_watermark",
    Base.metadata,
    sa.Column("id", sa.Boolean(), primary_key=True, server_default=sa.true()),
    sa.Column("last_block", sa.BigInteger(), nullable=False),
    sa.Column("last_block_at", TS, nullable=False),
    sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    sa.CheckConstraint("id", name="ck_watermark_single_row"),
    sa.CheckConstraint("last_block >= 0", name="ck_watermark_last_block_nonneg"),
)

reorg_log = sa.Table(
    "reorg_log",
    Base.metadata,
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

thb_balance = sa.Table(
    "thb_balance",
    Base.metadata,
    sa.Column("account", sa.String(40), primary_key=True),
    sa.Column("account_id", sa.Text(), primary_key=True),
    sa.Column("amount", AMOUNT, nullable=False, server_default="0"),
    sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    _enum_check(
        "account",
        ("CUSTOMER_THB", "MERCHANT_THB", "PLATFORM_THB", "ISSUANCE"),
        "ck_thb_balance_account_enum",
    ),
    sa.CheckConstraint("amount >= 0 OR account = 'ISSUANCE'", name="ck_balance_sign"),
)

psp_webhook = sa.Table(
    "psp_webhook",
    Base.metadata,
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

# --- tier 1: depend only on tier 0 ---------------------------------------------------------------
merchant_admin = sa.Table(
    "merchant_admin",
    Base.metadata,
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

point_type = sa.Table(
    "point_type",
    Base.metadata,
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
    _enum_check("status", ("REQUESTED", "LIVE", "FAILED", "RETIRED"), "ck_point_type_status_enum"),
    sa.CheckConstraint(
        "(status = 'LIVE') = (live_at IS NOT NULL) OR status = 'RETIRED'", name="ck_point_type_live"
    ),
    sa.CheckConstraint(
        "(status = 'RETIRED') = (retired_at IS NOT NULL)", name="ck_point_type_retired"
    ),
)

coupon_type = sa.Table(
    "coupon_type",
    Base.metadata,
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
    sa.CheckConstraint("total_supply BETWEEN 1 AND 1000000", name="ck_coupon_type_supply_range"),
    _enum_check("status", ("REQUESTED", "LIVE", "FAILED", "RETIRED"), "ck_coupon_type_status_enum"),
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

webhook_endpoint = sa.Table(
    "webhook_endpoint",
    Base.metadata,
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

thb_deposit = sa.Table(
    "thb_deposit",
    Base.metadata,
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
    sa.Column("expires_at", TS, nullable=False),
    _enum_check("holder_kind", HOLDER_KIND, "ck_thb_deposit_holder_kind_enum"),
    sa.CheckConstraint("amount_satang > 0", name="ck_thb_deposit_satang_pos"),
    sa.CheckConstraint("amount_base > 0", name="ck_thb_deposit_base_pos"),
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

otp_attempt = sa.Table(
    "otp_attempt",
    Base.metadata,
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

escrow = sa.Table(
    "escrow",
    Base.metadata,
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
    sa.CheckConstraint("coupons_redeemed <= coupons_locked", name="ck_escrow_count"),
)

chain_event = sa.Table(
    "chain_event",
    Base.metadata,
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
    sa.CheckConstraint("orphaned_at IS NULL OR applied_at IS NULL", name="ck_orphan_not_applied"),
    sa.UniqueConstraint("tx_hash", "log_index", name="uq_chain_event"),
)

customer_session = sa.Table(
    "customer_session",
    Base.metadata,
    sa.Column("id", sa.Text(), primary_key=True),
    sa.Column("customer_id", sa.Text(), sa.ForeignKey("customer.id"), nullable=False),
    sa.Column("token_hash", HASH, nullable=False, unique=True),
    sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
    sa.Column("expires_at", TS, nullable=False),
    sa.Column("revoked_at", TS, nullable=True),
    sa.CheckConstraint("length(token_hash) = 66", name="ck_customer_session_hash_len"),
    sa.CheckConstraint("expires_at > created_at", name="ck_session_window"),
)

idempotency_key = sa.Table(
    "idempotency_key",
    Base.metadata,
    sa.Column("id", sa.Text(), primary_key=True),
    sa.Column("scope", sa.Text(), nullable=False),
    sa.Column("key", sa.Text(), nullable=False),
    sa.Column("fingerprint", HASH, nullable=False),
    sa.Column("operation_id", sa.Text(), sa.ForeignKey("operation.id"), nullable=True),
    sa.Column("response_status", sa.Integer(), nullable=True),
    sa.Column("response_body", sa.JSON(), nullable=True),
    sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
    sa.Column("expires_at", TS, nullable=False),
    sa.CheckConstraint("length(key) BETWEEN 8 AND 255", name="ck_idem_key_len"),
    sa.CheckConstraint("length(fingerprint) = 66", name="ck_idem_fingerprint_len"),
    sa.UniqueConstraint("scope", "key", name="uq_idem"),
)

reservation = sa.Table(
    "reservation",
    Base.metadata,
    sa.Column("id", sa.Text(), primary_key=True),
    sa.Column(
        "operation_id", sa.Text(), sa.ForeignKey("operation.id", ondelete="CASCADE"), nullable=False
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

outbox = sa.Table(
    "outbox",
    Base.metadata,
    sa.Column("id", sa.Text(), primary_key=True),
    sa.Column(
        "operation_id", sa.Text(), sa.ForeignKey("operation.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("topic", sa.Text(), nullable=False),
    sa.Column("payload", sa.JSON(), nullable=False),
    sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
    sa.Column("sent_at", TS, nullable=True),
)

operation_event = sa.Table(
    "operation_event",
    Base.metadata,
    sa.Column("id", sa.Text(), primary_key=True),
    sa.Column(
        "operation_id", sa.Text(), sa.ForeignKey("operation.id", ondelete="CASCADE"), nullable=False
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

operation_step = sa.Table(
    "operation_step",
    Base.metadata,
    sa.Column("id", sa.Text(), primary_key=True),
    sa.Column(
        "operation_id", sa.Text(), sa.ForeignKey("operation.id", ondelete="CASCADE"), nullable=False
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
    sa.CheckConstraint("mint_count BETWEEN 1 AND 500", name="ck_operation_step_mint_count_range"),
    sa.CheckConstraint(
        "status = 'PENDING' OR raw_tx IS NOT NULL", name="ck_rawtx_before_broadcast"
    ),
    sa.UniqueConstraint("operation_id", "step_index", name="uq_step_order"),
    sa.UniqueConstraint("operation_id", "name", name="uq_step_name"),
)

point_balance = sa.Table(
    "point_balance",
    Base.metadata,
    sa.Column("point_type_id", sa.Text(), sa.ForeignKey("point_type.id"), primary_key=True),
    sa.Column("holder_kind", sa.String(40), primary_key=True),
    sa.Column("holder_id", sa.Text(), primary_key=True),
    sa.Column("amount", AMOUNT, nullable=False),
    sa.Column("as_of_block", sa.BigInteger(), nullable=False),
    _enum_check("holder_kind", HOLDER_KIND, "ck_point_balance_holder_kind_enum"),
    sa.CheckConstraint("amount >= 0", name="ck_point_balance_amount_nonneg"),
)

point_type_chain = sa.Table(
    "point_type_chain",
    Base.metadata,
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

point_balance_bucket = sa.Table(
    "point_balance_bucket",
    Base.metadata,
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
    sa.CheckConstraint("amount >= 0", name="ck_point_bucket_amount_nonneg"),
    sa.CheckConstraint("expires_at > received_at", name="ck_bucket_expiry"),
    sa.UniqueConstraint(
        "point_type_id", "holder_kind", "holder_id", "epoch_index", name="uq_bucket"
    ),
)

# --- tier 2: depend on tier 0/1 -------------------------------------------------------------------
auth_session = sa.Table(
    "auth_session",
    Base.metadata,
    sa.Column("id", sa.Text(), primary_key=True),
    sa.Column("subject_kind", sa.String(40), nullable=False),
    sa.Column("merchant_admin_id", sa.Text(), sa.ForeignKey("merchant_admin.id"), nullable=True),
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

issue_batch = sa.Table(
    "issue_batch",
    Base.metadata,
    sa.Column("id", sa.Text(), primary_key=True),
    sa.Column("coupon_type_id", sa.Text(), sa.ForeignKey("coupon_type.id"), nullable=False),
    sa.Column("operation_id", sa.Text(), sa.ForeignKey("operation.id"), nullable=True),
    sa.Column("quantity", sa.Integer(), nullable=False),
    sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
    sa.CheckConstraint("quantity BETWEEN 1 AND 10000", name="ck_issue_batch_quantity_range"),
)

ledger_entry = sa.Table(
    "ledger_entry",
    Base.metadata,
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
    sa.CheckConstraint("amount <> 0", name="ck_ledger_entry_amount_nonzero"),
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

listing = sa.Table(
    "listing",
    Base.metadata,
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
        "status", ("PENDING", "ACTIVE", "SOLD", "DELISTED", "FAILED"), "ck_listing_status_enum"
    ),
    sa.CheckConstraint(
        "(status = 'ACTIVE') = (active_at IS NOT NULL) OR status IN ('SOLD','DELISTED')",
        name="ck_listing_active",
    ),
    sa.CheckConstraint(
        "(status IN ('SOLD','DELISTED')) = (closed_at IS NOT NULL)", name="ck_listing_closed"
    ),
    sa.CheckConstraint(
        "closed_at IS NULL OR closed_at >= active_at", name="ck_listing_close_order"
    ),
)

coupon_type_chain = sa.Table(
    "coupon_type_chain",
    Base.metadata,
    sa.Column("coupon_type_id", sa.Text(), sa.ForeignKey("coupon_type.id"), primary_key=True),
    sa.Column("contract_address", ADDRESS, nullable=False),
    sa.Column("chain_type_id", AMOUNT, nullable=False),
    sa.Column("deployed_block", sa.BigInteger(), nullable=False),
    sa.Column("deployed_tx_hash", HASH, nullable=False),
    sa.Column("applied_at", TS, nullable=False, server_default=sa.func.now()),
    sa.CheckConstraint("length(contract_address) = 42", name="ck_coupon_type_chain_contract_len"),
    sa.CheckConstraint("deployed_block > 0", name="ck_coupon_type_chain_block_pos"),
    sa.CheckConstraint("length(deployed_tx_hash) = 66", name="ck_coupon_type_chain_tx_hash_len"),
    sa.UniqueConstraint("contract_address", "chain_type_id", name="uq_coupon_chain"),
)

webhook_delivery = sa.Table(
    "webhook_delivery",
    Base.metadata,
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
        "status", ("PENDING", "IN_FLIGHT", "SENT", "DEAD"), "ck_webhook_delivery_status_enum"
    ),
    sa.CheckConstraint("attempt >= 0", name="ck_webhook_delivery_attempt_nonneg"),
    sa.CheckConstraint("(status = 'SENT') = (sent_at IS NOT NULL)", name="ck_delivery_sent"),
    sa.CheckConstraint("(status = 'DEAD') = (dead_at IS NOT NULL)", name="ck_delivery_dead"),
    sa.CheckConstraint("status <> 'IN_FLIGHT' OR claimed_at IS NOT NULL", name="ck_delivery_claim"),
    sa.CheckConstraint("replay_of IS NULL OR replay_of <> id", name="ck_no_self_replay"),
    sa.UniqueConstraint("endpoint_id", "operation_id", "delivery_seq", name="uq_delivery_seq"),
)

deposit_reversal = sa.Table(
    "deposit_reversal",
    Base.metadata,
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
    sa.CheckConstraint("amount_base > 0", name="ck_deposit_reversal_amount_pos"),
    sa.CheckConstraint("available_at_reversal >= 0", name="ck_deposit_reversal_available_nonneg"),
    _enum_check("outcome", ("BURNED", "MANUAL_REVIEW"), "ck_deposit_reversal_outcome_enum"),
)

thb_mint = sa.Table(
    "thb_mint",
    Base.metadata,
    sa.Column("deposit_id", sa.Text(), sa.ForeignKey("thb_deposit.id"), primary_key=True),
    sa.Column("operation_id", sa.Text(), sa.ForeignKey("operation.id"), nullable=False),
    sa.Column("amount_base", AMOUNT, nullable=False),
    sa.Column("to_account", sa.String(40), nullable=False),
    sa.Column("to_account_id", sa.Text(), nullable=False),
    sa.Column("chain_event_id", sa.Text(), sa.ForeignKey("chain_event.id"), nullable=True),
    sa.Column("tx_hash", HASH, nullable=True),
    sa.Column("minted_block", sa.BigInteger(), nullable=True),
    sa.Column("applied_at", TS, nullable=True),
    sa.CheckConstraint("amount_base > 0", name="ck_thb_mint_amount_pos"),
    _enum_check(
        "to_account",
        ("CUSTOMER_THB", "MERCHANT_THB", "PLATFORM_THB", "ISSUANCE"),
        "ck_thb_mint_to_account_enum",
    ),
    sa.CheckConstraint("tx_hash IS NULL OR length(tx_hash) = 66", name="ck_thb_mint_tx_hash_len"),
    sa.CheckConstraint("minted_block IS NULL OR minted_block > 0", name="ck_thb_mint_block_pos"),
    sa.CheckConstraint("(applied_at IS NULL) = (chain_event_id IS NULL)", name="ck_mint_applied"),
    sa.UniqueConstraint("operation_id", name="uq_mint_operation"),
)

expiry_notice = sa.Table(
    "expiry_notice",
    Base.metadata,
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

# --- tier 3: depend on tier 2 -----------------------------------------------------------------
auth_event = sa.Table(
    "auth_event",
    Base.metadata,
    sa.Column("id", sa.Text(), primary_key=True),
    sa.Column("subject_kind", sa.String(40), nullable=True),
    sa.Column("subject_id", sa.Text(), nullable=True),
    sa.Column("kind", sa.Text(), nullable=False),
    sa.Column("session_id", sa.Text(), sa.ForeignKey("auth_session.id"), nullable=True),
    sa.Column("ip_hash", sa.LargeBinary(), nullable=True),
    sa.Column("metadata", sa.JSON(), nullable=False),
    sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
    sa.CheckConstraint(
        "subject_kind IS NULL OR subject_kind IN ('MERCHANT_ADMIN', 'CUSTOMER', 'PLATFORM_ADMIN')",
        name="ck_auth_event_subject_kind_enum",
    ),
)

coupon_code = sa.Table(
    "coupon_code",
    Base.metadata,
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
        "state", ("ISSUED", "HELD", "REDEEMED", "EXPIRED", "VOIDED"), "ck_coupon_code_state_enum"
    ),
    sa.CheckConstraint(
        "holder_kind IS NULL OR holder_kind IN ('MERCHANT', 'CUSTOMER')",
        name="ck_coupon_code_holder_kind_enum",
    ),
    sa.CheckConstraint("(holder_kind IS NULL) = (holder_id IS NULL)", name="ck_code_holder"),
    sa.CheckConstraint("state <> 'HELD' OR holder_id IS NOT NULL", name="ck_code_held"),
    sa.CheckConstraint("(state = 'REDEEMED') = (redeemed_at IS NOT NULL)", name="ck_code_redeemed"),
    sa.Index("ix_code_type_state", "coupon_type_id", "state"),
)

listing_chain = sa.Table(
    "listing_chain",
    Base.metadata,
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
        "closed_block IS NULL OR closed_block >= created_block", name="ck_chain_close_order"
    ),
    sa.UniqueConstraint("contract_address", "chain_listing_id", name="uq_listing_chain"),
)

purchase = sa.Table(
    "purchase",
    Base.metadata,
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
    sa.CheckConstraint("unit_price > 0", name="ck_purchase_unit_price_pos"),
    sa.CheckConstraint("quantity > 0", name="ck_purchase_quantity_pos"),
    sa.CheckConstraint("total_price > 0", name="ck_purchase_total_price_pos"),
    sa.CheckConstraint("fee_bps BETWEEN 0 AND 10000", name="ck_purchase_fee_bps_range"),
    sa.CheckConstraint("fee_amount >= 0", name="ck_purchase_fee_amount_nonneg"),
    sa.CheckConstraint("net_to_seller >= 0", name="ck_purchase_net_nonneg"),
    _enum_check("status", ("PENDING", "SETTLED", "FAILED"), "ck_purchase_status_enum"),
    sa.CheckConstraint(
        "(status = 'SETTLED') = (settled_at IS NOT NULL)", name="ck_purchase_settled"
    ),
    sa.CheckConstraint(
        "NOT (buyer_kind = seller_kind AND buyer_id = seller_id)", name="ck_no_self_trade"
    ),
)

webhook_delivery_attempt = sa.Table(
    "webhook_delivery_attempt",
    Base.metadata,
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

# --- tier 4: depend on tier 3 -----------------------------------------------------------------
coupon_holding = sa.Table(
    "coupon_holding",
    Base.metadata,
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
    sa.CheckConstraint("quantity >= 0", name="ck_coupon_holding_quantity_nonneg"),
    sa.UniqueConstraint("coupon_type_id", "holder_kind", "holder_id", name="uq_holding"),
)

listing_item = sa.Table(
    "listing_item",
    Base.metadata,
    sa.Column("id", sa.Text(), primary_key=True),
    sa.Column(
        "listing_id", sa.Text(), sa.ForeignKey("listing.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("coupon_code_id", sa.Text(), sa.ForeignKey("coupon_code.id"), nullable=False),
    sa.UniqueConstraint("listing_id", "coupon_code_id", name="uq_item_in_listing"),
)
