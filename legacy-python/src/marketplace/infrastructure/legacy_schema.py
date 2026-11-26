"""Schema-only metadata for Alembic's drift check, mirroring migrations 0002 through 0008.

Declares the remaining unclaimed tables created by migrations/versions/0002_legacy_schema_port.py
(50 originally; `idempotency_key` and the five `auth`-owned tables — `merchant_admin`,
`auth_session`, `auth_event`, `login_attempt`, `login_lockout` — have since moved into their
owning module's `infrastructure/models.py`) as plain `sa.Table` objects on `Base.metadata`, so
`alembic check` sees them as declared schema instead of drift. These intentionally have no
ORM-mapped classes, domain entities, or repositories: no feature has claimed these tables yet
(`coupons` is still a package skeleton, and most others have no decided use case — see
docs/architecture.md). A feature that claims one of these tables should move its `sa.Table` out of
here and into that feature's own `infrastructure/models.py` as a proper mapped class.
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
# migration 0017 · FEAT-14 · projections also hold for the platform and outside addresses
PROJECTION_HOLDER_KIND = ("MERCHANT", "CUSTOMER", "PLATFORM", "EXTERNAL")

E164 = r"phone_e164 ~ '^\+[1-9][0-9]{7,14}$'"


# migration 0006 · FEAT-04 · the four business models of NewPointToken v3 (configureBusiness).
# Shared by point_type (requested by the merchant) and point_type_chain (read from the chain).
POINT_LOT_POLICY = "lot_policy IS NULL OR lot_policy IN ('FEFO_ONLY', 'USER_SELECT')"
POINT_BUSINESS_CONFIG = (
    "(business_model = 1 AND program_start_at IS NOT NULL"
    " AND program_expire_at > program_start_at AND lot_policy IS NULL)"
    " OR (business_model = 2 AND program_start_at IS NULL AND program_expire_at IS NULL"
    " AND lot_policy = 'FEFO_ONLY')"
    " OR (business_model = 3 AND program_start_at IS NULL AND program_expire_at IS NULL"
    " AND lot_policy = 'USER_SELECT')"
    " OR (business_model = 4 AND program_start_at IS NULL AND program_expire_at IS NULL"
    " AND lot_policy IS NOT NULL)"
)


THB_ACCOUNTS = ("CUSTOMER_THB", "MERCHANT_THB", "PLATFORM_THB", "ISSUANCE", "ESCROW")
# an account_id names its account: CUSTOMER_THB:cus_1, ESCROW:pur_1
THB_ACCOUNT_ID_PREFIX = (
    "substr(account_id, 1, length(account) + 1) = account || ':'"
    " AND length(account_id) > length(account) + 1"
)


def _pg_check(condition: str, name: str) -> sa.CheckConstraint:
    """A CHECK that exists on PostgreSQL only: SQLite has no regex operator."""
    return sa.CheckConstraint(condition, name=name).ddl_if(dialect="postgresql")


def _partial_unique(name: str, *columns: str, where: str) -> sa.Index:
    """Unique index over the rows matching `where`, with the same predicate on both dialects."""
    return sa.Index(
        name,
        *columns,
        unique=True,
        postgresql_where=sa.text(where),
        sqlite_where=sa.text(where),
    )


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
    # migration 0004 · F7 · the phone comes from the challenge that verified it
    sa.Column("verified_challenge_id", sa.Text(), nullable=False),
    _enum_check(
        "status", ("PENDING_WALLET", "ACTIVE", "SUSPENDED", "CLOSED"), "ck_customer_status_enum"
    ),
    sa.UniqueConstraint("verified_challenge_id", name="uq_customer_challenge"),
    sa.ForeignKeyConstraint(
        ["verified_challenge_id", "phone_e164"],
        ["otp_challenge.id", "otp_challenge.phone_e164"],
        name="fk_customer_verified_challenge",
    ),
    _pg_check(E164, "ck_customer_phone_e164"),
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
    # migrations 0003 + 0004 · FEAT-01 T6, FEAT-02 T36 · one unfinished provision per subject
    _partial_unique(
        "uq_provision_inflight",
        "kind",
        "subject_id",
        where="kind IN ('MERCHANT_PROVISION', 'CUSTOMER_PROVISION')"
        " AND status NOT IN ('FINALIZED', 'CHAIN_REVERTED')",
    ),
    # migration 0006 · FEAT-04 T8 · one unfinished deploy per point type
    _partial_unique(
        "uq_point_deploy_inflight",
        "subject_id",
        where="kind = 'POINT_TYPE_CREATE' AND status NOT IN ('FINALIZED', 'CHAIN_REVERTED')",
    ),
    # migration 0010 · FEAT-07 T15
    _partial_unique(
        "uq_coupon_deploy_inflight",
        "subject_id",
        where="kind = 'COUPON_TYPE_CREATE' AND status NOT IN ('FINALIZED', 'CHAIN_REVERTED')",
    ),
    # migration 0011 · FEAT-08 T19 · 0012 adds LISTING_BUY (FEAT-09 T13)
    _partial_unique(
        "uq_listing_inflight",
        "subject_id",
        where="kind IN ('LISTING_CREATE', 'LISTING_DELETE', 'LISTING_BUY')"
        " AND status NOT IN ('FINALIZED', 'CHAIN_REVERTED')",
    ),
    # migration 0015 · FEAT-12 T27
    _partial_unique(
        "uq_mint_inflight",
        "subject_id",
        where="kind = 'THB_MINT' AND status NOT IN ('FINALIZED', 'CHAIN_REVERTED')",
    ),
    # migration 0012 · FEAT-09 T8
    _pg_check(
        "actor_scope ~ '^(merchant|customer|admin|anon|system):.+$'", "ck_operation_actor_scope"
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
    _pg_check("kms_key_ref ~ '^kms://[A-Za-z0-9/_.:+=-]+$'", "ck_wallet_kms_key_ref"),
    # migration 0017 · FEAT-14 · one spelling, so the projector finds the wallet of an address
    _pg_check("address = lower(address)", "ck_wallet_address_lower"),
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
    # migration 0017 · FEAT-14 T26 · one live block per height; orphaned ones are kept
    _partial_unique("uq_block_live", "block_number", where="orphaned_at IS NULL"),
    sa.Index("ix_block_number", "block_number"),
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
    # migration 0004 · target of fk_customer_verified_challenge (F7)
    sa.UniqueConstraint("id", "phone_e164", name="uq_challenge_id_phone"),
    # migration 0004 · T14 · one live challenge per phone
    _partial_unique("uq_challenge_live", "phone_e164", where="status = 'PENDING'"),
    _pg_check(E164, "ck_otp_challenge_phone_e164"),
    _pg_check("pepper_ref ~ '^kms://'", "ck_otp_challenge_pepper_ref"),
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
    _pg_check(E164, "ck_phone_throttle_phone_e164"),
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
    # migration 0013 (FEAT-10) adds ESCROW: THB locked in the Vault for one purchase
    _enum_check("account", THB_ACCOUNTS, "ck_thb_balance_account_enum"),
    # migration 0013 · FEAT-10 T12b T33 · the 0002 port's numeric ck_balance_sign, as a string rule
    sa.CheckConstraint(THB_ACCOUNT_ID_PREFIX, name="ck_thb_balance_account_id"),
    sa.CheckConstraint(
        "substr(amount, 1, 1) <> '-' OR account = 'ISSUANCE'", name="ck_thb_balance_sign"
    ),
    _pg_check("f_is_int256(amount, false)", "ck_thb_balance_amount"),
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
    # migration 0015 · FEAT-12 T32 · the work queue never holds an unsigned webhook
    sa.Index(
        "ix_psp_unprocessed",
        "received_at",
        postgresql_where=sa.text("processed_at IS NULL AND signature_ok"),
        sqlite_where=sa.text("processed_at IS NULL AND signature_ok"),
    ),
)

# --- tier 1: depend only on tier 0 ---------------------------------------------------------------
point_type = sa.Table(
    "point_type",
    Base.metadata,
    sa.Column("id", sa.Text(), primary_key=True),
    sa.Column("merchant_id", sa.Text(), sa.ForeignKey("merchant.id"), nullable=False),
    sa.Column("name", sa.Text(), nullable=False),
    sa.Column("symbol", sa.Text(), nullable=False),
    sa.Column("decimals", sa.SmallInteger(), nullable=False, server_default="18"),
    sa.Column("status", sa.String(40), nullable=False, server_default="REQUESTED"),
    sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
    sa.Column("live_at", TS, nullable=True),
    sa.Column("retired_at", TS, nullable=True),
    # migration 0006 · T6 · write as lower(trim(:name)) in SQL so it always matches the CHECK
    sa.Column("name_normalized", sa.Text(), nullable=False),
    # migration 0006 · the configuration the merchant asks configureBusiness to set
    sa.Column("business_model", sa.SmallInteger(), nullable=False),
    sa.Column("program_start_at", TS, nullable=True),
    sa.Column("program_expire_at", TS, nullable=True),
    sa.Column("lot_policy", sa.String(20), nullable=True),
    sa.CheckConstraint("length(trim(name)) BETWEEN 1 AND 60", name="ck_point_type_name_len"),
    sa.CheckConstraint("name_normalized = lower(trim(name))", name="ck_point_type_name_normalized"),
    # migration 0006 · deployPointToken takes no decimals: every v3 point token has 18
    sa.CheckConstraint("decimals = 18", name="ck_point_type_decimals_fixed"),
    _enum_check("status", ("REQUESTED", "LIVE", "FAILED", "RETIRED"), "ck_point_type_status_enum"),
    sa.CheckConstraint(
        "(status = 'LIVE') = (live_at IS NOT NULL) OR status = 'RETIRED'", name="ck_point_type_live"
    ),
    sa.CheckConstraint(
        "(status = 'RETIRED') = (retired_at IS NOT NULL)", name="ck_point_type_retired"
    ),
    sa.CheckConstraint(POINT_LOT_POLICY, name="ck_point_type_lot_policy_enum"),
    sa.CheckConstraint(POINT_BUSINESS_CONFIG, name="ck_point_type_business_config"),
    # migration 0006 · T5 T6 T7 · a FAILED deploy releases its symbol and name
    _partial_unique("uq_point_symbol", "merchant_id", "symbol", where="status <> 'FAILED'"),
    _partial_unique("uq_point_name", "merchant_id", "name_normalized", where="status <> 'FAILED'"),
    _pg_check("symbol ~ '^[A-Z][A-Z0-9]{1,10}$'", "ck_point_type_symbol_format"),
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
    # migration 0010 (FEAT-07)
    sa.Column("name_normalized", sa.Text(), nullable=False),
    sa.Column("starts_at", TS, nullable=False, server_default=sa.func.now()),
    sa.Column("issued_count", sa.Integer(), nullable=False, server_default="0"),
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
    # migration 0010 · T3 T4 name per merchant, released by a FAILED deploy · T5 · T7 supply
    sa.CheckConstraint(
        "name_normalized = lower(trim(name))", name="ck_coupon_type_name_normalized"
    ),
    sa.CheckConstraint("expires_at > starts_at", name="ck_coupon_type_window"),
    sa.CheckConstraint(
        "issued_count >= 0 AND issued_count <= total_supply", name="ck_coupon_type_within_supply"
    ),
    _partial_unique(
        "uq_coupon_type_name", "merchant_id", "name_normalized", where="status <> 'FAILED'"
    ),
    _pg_check("f_is_uint256(face_value, false)", "ck_coupon_type_face_value_uint256"),
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
    # migration 0016 · FEAT-13 T14-T16
    _pg_check("url ~ '^https://[^/\\s]+'", "ck_webhook_endpoint_url_https"),
    _pg_check(
        "length(secret_ref) <= 200 AND secret_ref ~ '^kms://[A-Za-z0-9/_.:+=-]+$'",
        "ck_webhook_endpoint_secret_ref",
    ),
    _pg_check("f_is_event_type_list(event_types)", "ck_webhook_endpoint_event_types"),
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
    # migration 0015 · FEAT-12 T5 T7c · the 0002 port dropped amount_base > 0
    _pg_check("f_is_uint256(amount_base, true)", "ck_thb_deposit_base_uint256"),
    _partial_unique("uq_deposit_webhook", "webhook_id", where="webhook_id IS NOT NULL"),
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
    # migration 0014 (FEAT-11) · one row per Vault escrow, i.e. per purchase (T9)
    sa.Column("chain_escrow_id", AMOUNT, nullable=False),
    sa.Column(
        "purchase_id",
        sa.Text(),
        sa.ForeignKey("purchase.id", name="fk_escrow_purchase"),
        nullable=False,
    ),
    sa.UniqueConstraint("contract_address", "chain_escrow_id", name="uq_escrow_chain"),
    sa.UniqueConstraint("purchase_id", name="uq_escrow_purchase"),
    sa.CheckConstraint("(remaining_amount = '0') = (NOT active)", name="ck_escrow_closed"),
    _pg_check("f_is_uint256(total_amount, true)", "ck_escrow_total_uint256"),
    _pg_check("f_is_uint256(remaining_amount, false)", "ck_escrow_remaining_uint256"),
    _pg_check("f_is_uint256(chain_escrow_id, false)", "ck_escrow_chain_id_uint256"),
    _pg_check("f_is_uint256(chain_token_id, false)", "ck_escrow_token_uint256"),
    _pg_check(
        "NOT (f_is_uint256(total_amount, true) AND f_is_uint256(remaining_amount, false))"
        " OR remaining_amount::numeric <= total_amount::numeric",
        "ck_escrow_remaining_le_total",
    ),
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
    # migration 0017 · FEAT-14 · the projector's queue, in chain order
    sa.Index(
        "ix_event_unapplied",
        "block_number",
        "log_index",
        postgresql_where=sa.text("applied_at IS NULL AND orphaned_at IS NULL"),
        sqlite_where=sa.text("applied_at IS NULL AND orphaned_at IS NULL"),
    ),
    sa.Index("ix_event_by_block", "block_number"),
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
    # migration 0007 · FEAT-05 T8 · single items only: a point balance backs many reservations
    _partial_unique(
        "uq_reservation_exclusive",
        "subject_type",
        "subject_id",
        where="released_at IS NULL AND subject_type IN ('COUPON_UNIT', 'LISTING', 'ESCROW')",
    ),
    _pg_check("f_is_uint256(amount, true)", "ck_reservation_amount_uint256"),
    # migration 0013 · FEAT-10 T12 · a THB reservation spends a customer or merchant account
    _pg_check(
        "subject_type <> 'THB_BALANCE' OR subject_id ~ '^(CUSTOMER_THB|MERCHANT_THB):.+$'",
        "ck_reservation_thb_subject",
    ),
    _pg_check(
        "subject_type <> 'POINT_BALANCE' OR subject_id ~ '^[^|]+\\|(MERCHANT|CUSTOMER)\\|[^|]+$'",
        "ck_reservation_point_subject",
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
    # migration 0003 · T7 · versioned topic · PostgreSQL only (SQLite has no regex operator)
    sa.CheckConstraint(r"topic ~ '\.v[0-9]+$'", name="ck_outbox_topic_version").ddl_if(
        dialect="postgresql"
    ),
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
    # migration 0003 · T9 · a signer never reuses a nonce
    _partial_unique(
        "uq_signer_nonce",
        "signer_address",
        "nonce",
        where="signer_address IS NOT NULL AND nonce IS NOT NULL",
    ),
    # migration 0003 · T10 · one tx hash maps to one step
    _partial_unique("uq_step_txhash", "tx_hash", where="tx_hash IS NOT NULL"),
)

point_balance = sa.Table(
    "point_balance",
    Base.metadata,
    sa.Column("point_type_id", sa.Text(), sa.ForeignKey("point_type.id"), primary_key=True),
    sa.Column("holder_kind", sa.String(40), primary_key=True),
    sa.Column("holder_id", sa.Text(), primary_key=True),
    sa.Column("amount", AMOUNT, nullable=False),
    sa.Column("as_of_block", sa.BigInteger(), nullable=False),
    _enum_check("holder_kind", PROJECTION_HOLDER_KIND, "ck_point_balance_holder_kind_enum"),
    # migration 0017 · FEAT-14 T35 · a balance row exists only while it is positive
    _pg_check("f_is_uint256(amount, true)", "ck_point_balance_amount_uint256"),
)

# migration 0017 · FEAT-14 T22 · which codes the chain says were redeemed (CouponRedeemed carries
# the code_ref); a projection, unlike coupon_code.state, which the redeem operation writes
coupon_redemption = sa.Table(
    "coupon_redemption",
    Base.metadata,
    sa.Column("coupon_code_id", sa.Text(), sa.ForeignKey("coupon_code.id"), primary_key=True),
    sa.Column("redeemer_kind", sa.String(40), nullable=False),
    sa.Column("redeemer_id", sa.Text(), nullable=False),
    sa.Column("chain_event_id", sa.Text(), sa.ForeignKey("chain_event.id"), nullable=False),
    sa.Column("as_of_block", sa.BigInteger(), nullable=False),
    sa.Column("as_of_time", TS, nullable=False),
    _enum_check("redeemer_kind", PROJECTION_HOLDER_KIND, "ck_coupon_redemption_redeemer_kind_enum"),
)

point_type_chain = sa.Table(
    "point_type_chain",
    Base.metadata,
    sa.Column("point_type_id", sa.Text(), sa.ForeignKey("point_type.id"), primary_key=True),
    sa.Column("contract_address", ADDRESS, nullable=False, unique=True),
    sa.Column("deployed_block", sa.BigInteger(), nullable=False),
    sa.Column("deployed_tx_hash", HASH, nullable=False),
    sa.Column("applied_at", TS, nullable=False, server_default=sa.func.now()),
    # migration 0006 · from BusinessConfigured · NULL until the token is configured
    sa.Column("business_model", sa.SmallInteger(), nullable=True),
    sa.Column("program_start_at", TS, nullable=True),
    sa.Column("program_expire_at", TS, nullable=True),
    sa.Column("lot_policy", sa.String(20), nullable=True),
    sa.Column("configured_block", sa.BigInteger(), nullable=True),
    sa.Column("configured_tx_hash", HASH, nullable=True),
    sa.CheckConstraint("length(contract_address) = 42", name="ck_point_chain_contract_len"),
    sa.CheckConstraint("deployed_block > 0", name="ck_point_chain_block_pos"),
    sa.CheckConstraint("length(deployed_tx_hash) = 66", name="ck_point_chain_tx_hash_len"),
    sa.CheckConstraint(POINT_LOT_POLICY, name="ck_point_chain_lot_policy_enum"),
    sa.CheckConstraint(
        f"business_model IS NULL OR ({POINT_BUSINESS_CONFIG})",
        name="ck_point_chain_business_config",
    ),
    sa.CheckConstraint(
        "(business_model IS NULL) = (configured_block IS NULL)"
        " AND (configured_block IS NULL) = (configured_tx_hash IS NULL)",
        name="ck_point_chain_configured",
    ),
    sa.CheckConstraint(
        "configured_block IS NULL OR configured_block >= deployed_block",
        name="ck_point_chain_configured_after_deploy",
    ),
    sa.CheckConstraint(
        "configured_tx_hash IS NULL OR length(configured_tx_hash) = 66",
        name="ck_point_chain_configured_tx_len",
    ),
)

# migration 0008 · FEAT-05 · balances as NewPointToken v3 lots (projections of chain_event).
# Business A has one row with lot_key 'A' holding its program window.
point_lot = sa.Table(
    "point_lot",
    Base.metadata,
    sa.Column("point_type_id", sa.Text(), sa.ForeignKey("point_type.id"), primary_key=True),
    sa.Column("lot_key", sa.String(78), primary_key=True),
    sa.Column("start_at", TS, nullable=False),
    sa.Column("expire_at", TS, nullable=False),
    sa.Column("chain_event_id", sa.Text(), sa.ForeignKey("chain_event.id"), nullable=True),
    sa.Column("applied_at", TS, nullable=False, server_default=sa.func.now()),
    sa.CheckConstraint("expire_at > start_at", name="ck_point_lot_window"),
    _pg_check("lot_key = 'A' OR f_is_uint256(lot_key, false)", "ck_point_lot_key_format"),
)

point_lot_balance = sa.Table(
    "point_lot_balance",
    Base.metadata,
    sa.Column("point_type_id", sa.Text(), primary_key=True),
    sa.Column("holder_kind", sa.String(40), primary_key=True),
    sa.Column("holder_id", sa.Text(), primary_key=True),
    sa.Column("lot_key", sa.String(78), primary_key=True),
    sa.Column("amount", AMOUNT, nullable=False),
    sa.Column("as_of_block", sa.BigInteger(), nullable=True),
    sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    sa.CheckConstraint(
        "holder_kind IN ('MERCHANT', 'CUSTOMER')", name="ck_point_lot_balance_holder_kind_enum"
    ),
    sa.ForeignKeyConstraint(
        ["point_type_id", "lot_key"],
        ["point_lot.point_type_id", "point_lot.lot_key"],
        name="fk_point_lot_balance_lot",
    ),
    _pg_check("f_is_uint256(amount, false)", "ck_point_lot_balance_amount_uint256"),
)

# migration 0008 · business D's points waiting for delivery; they do not age
point_inventory = sa.Table(
    "point_inventory",
    Base.metadata,
    sa.Column("point_type_id", sa.Text(), sa.ForeignKey("point_type.id"), primary_key=True),
    sa.Column("holder_kind", sa.String(40), primary_key=True),
    sa.Column("holder_id", sa.Text(), primary_key=True),
    sa.Column("amount", AMOUNT, nullable=False),
    sa.Column("as_of_block", sa.BigInteger(), nullable=True),
    sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    sa.CheckConstraint("holder_kind = 'MERCHANT'", name="ck_point_inventory_holder_kind"),
    _pg_check("f_is_uint256(amount, false)", "ck_point_inventory_amount_uint256"),
)

# --- tier 2: depend on tier 0/1 -------------------------------------------------------------------
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
    # migration 0007 · THB only (FEAT-10/12): point entries live in point_ledger_entry
    sa.CheckConstraint("entry_seq >= 1", name="ck_ledger_entry_seq_min"),
    _enum_check("account", THB_ACCOUNTS, "ck_ledger_entry_account_enum"),
    sa.CheckConstraint("asset = 'THB'", name="ck_ledger_entry_asset"),
    sa.UniqueConstraint("operation_id", "entry_seq", name="uq_ledger_seq"),
    # migration 0013 · FEAT-10 T12b T18 · non-zero signed amount (0002 dropped the numeric rule)
    sa.CheckConstraint(THB_ACCOUNT_ID_PREFIX, name="ck_ledger_entry_account_id"),
    _pg_check("f_is_int256(amount, true)", "ck_ledger_entry_amount"),
    sa.Index("ix_ledger_account", "account", "account_id"),
)

# migration 0007 · FEAT-05 T16 · the point ledger: DEBIT = CREDIT per operation, checked at
# COMMIT by trg_point_ledger_balanced (PostgreSQL)
point_ledger_entry = sa.Table(
    "point_ledger_entry",
    Base.metadata,
    sa.Column("id", sa.Text(), primary_key=True),
    sa.Column("operation_id", sa.Text(), sa.ForeignKey("operation.id"), nullable=False),
    sa.Column("entry_seq", sa.Integer(), nullable=False),
    sa.Column("point_type_id", sa.Text(), sa.ForeignKey("point_type.id"), nullable=False),
    sa.Column("holder_kind", sa.String(40), nullable=False),
    sa.Column("holder_id", sa.Text(), nullable=False),
    sa.Column("lot_key", sa.String(78), nullable=True),
    sa.Column("direction", sa.String(10), nullable=False),
    sa.Column("amount", AMOUNT, nullable=False),
    sa.Column("chain_event_id", sa.Text(), sa.ForeignKey("chain_event.id"), nullable=True),
    sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
    sa.CheckConstraint("entry_seq >= 1", name="ck_point_ledger_seq_min"),
    sa.CheckConstraint(
        "holder_kind IN ('MERCHANT', 'CUSTOMER', 'ISSUANCE', 'INVENTORY')",
        name="ck_point_ledger_holder_kind_enum",
    ),
    sa.CheckConstraint("direction IN ('DEBIT', 'CREDIT')", name="ck_point_ledger_direction_enum"),
    sa.UniqueConstraint("operation_id", "entry_seq", name="uq_point_ledger_seq"),
    # migration 0008 · T16b
    sa.ForeignKeyConstraint(
        ["point_type_id", "lot_key"],
        ["point_lot.point_type_id", "point_lot.lot_key"],
        name="fk_point_ledger_lot",
    ),
    _pg_check("f_is_uint256(amount, true)", "ck_point_ledger_amount_uint256"),
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
    # migration 0011 · FEAT-08 T8 T20
    _pg_check("f_is_uint256(unit_price, true)", "ck_listing_unit_price_uint256"),
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
    # migration 0010 · FEAT-07 T13b
    _pg_check("f_is_uint256(chain_type_id, false)", "ck_coupon_type_chain_type_id_uint256"),
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
    # migration 0016 · FEAT-13 T18 T24e · the first delivery of an event is once; replays exempt
    _partial_unique(
        "uq_delivery_event", "endpoint_id", "operation_id", "event_seq", where="replay_of IS NULL"
    ),
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
    # migration 0015 · FEAT-12 · the 0002 port dropped amount > 0 and available >= 0
    _pg_check("f_is_uint256(amount_base, true)", "ck_deposit_reversal_amount_uint256"),
    _pg_check(
        "f_is_uint256(available_at_reversal, false)", "ck_deposit_reversal_available_uint256"
    ),
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
    # migration 0015 · FEAT-12 · the 0002 port dropped amount_base > 0; one Mint event, one mint
    _pg_check("f_is_uint256(amount_base, true)", "ck_thb_mint_amount_uint256"),
    sa.Index("uq_thb_mint_chain_event", "chain_event_id", unique=True),
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
    sa.Column("threshold_days", sa.Integer(), nullable=False),
    sa.Column("notified_at", TS, nullable=False, server_default=sa.func.now()),
    sa.Column("channel", sa.String(40), nullable=False),
    sa.CheckConstraint("threshold_days IN (30, 7, 1)", name="ck_expiry_notice_threshold"),
    _enum_check("channel", ("PUSH", "EMAIL", "SMS"), "ck_expiry_notice_channel_enum"),
    # migration 0008 · names the holder's balance in a lot (was a point_balance_bucket)
    sa.Column("point_type_id", sa.Text(), nullable=False),
    sa.Column("holder_kind", sa.String(40), nullable=False),
    sa.Column("holder_id", sa.Text(), nullable=False),
    sa.Column("lot_key", sa.String(78), nullable=False),
    sa.ForeignKeyConstraint(
        ["point_type_id", "holder_kind", "holder_id", "lot_key"],
        [
            "point_lot_balance.point_type_id",
            "point_lot_balance.holder_kind",
            "point_lot_balance.holder_id",
            "point_lot_balance.lot_key",
        ],
        name="fk_expiry_notice_lot_balance",
        ondelete="CASCADE",
    ),
    sa.UniqueConstraint(
        "point_type_id", "holder_kind", "holder_id", "lot_key", "threshold_days", name="uq_notice"
    ),
)

# --- tier 3: depend on tier 2 -----------------------------------------------------------------
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
    # migration 0010 · FEAT-07 T6 · the reference sent on chain is derived from the row id
    _pg_check(
        "code_ref = '0x' || encode(sha256(convert_to(id, 'UTF8')), 'hex')", "ck_code_ref_derived"
    ),
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
    # migration 0011 · FEAT-08 T17b
    _pg_check("f_is_uint256(chain_listing_id, false)", "ck_listing_chain_id_uint256"),
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
    sa.CheckConstraint("quantity > 0", name="ck_purchase_quantity_pos"),
    sa.CheckConstraint("fee_bps BETWEEN 0 AND 10000", name="ck_purchase_fee_bps_range"),
    # ck_purchase_unit_price_pos, ck_purchase_total_price_pos, ck_purchase_fee_amount_nonneg,
    # and ck_purchase_net_nonneg are dropped: all four are decimal-string amounts.
    _enum_check("status", ("PENDING", "SETTLED", "FAILED"), "ck_purchase_status_enum"),
    sa.CheckConstraint(
        "(status = 'SETTLED') = (settled_at IS NOT NULL)", name="ck_purchase_settled"
    ),
    sa.CheckConstraint(
        "NOT (buyer_kind = seller_kind AND buyer_id = seller_id)", name="ck_no_self_trade"
    ),
    # migration 0013 · FEAT-10 T30 · whole listings only; T8 T20 · the dropped amount rules
    _partial_unique("uq_purchase_listing", "listing_id", where="status <> 'FAILED'"),
    _pg_check("f_is_uint256(unit_price, true)", "ck_purchase_unit_price_uint256"),
    _pg_check("f_is_uint256(total_price, true)", "ck_purchase_total_uint256"),
    _pg_check("f_is_uint256(fee_amount, false)", "ck_purchase_fee_uint256"),
    _pg_check("f_is_uint256(net_to_seller, false)", "ck_purchase_net_uint256"),
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
    # migration 0017 · FEAT-14 · an ERC-1155 amount per holder, from the last event that moved
    # it; no column takes the time of applying (RFD 6 §3.2: a replay gives the same rows)
    sa.Column("chain_event_id", sa.Text(), sa.ForeignKey("chain_event.id"), nullable=False),
    sa.Column("as_of_block", sa.BigInteger(), nullable=False),
    sa.Column("as_of_time", TS, nullable=False),
    _enum_check("holder_kind", PROJECTION_HOLDER_KIND, "ck_coupon_holding_holder_kind_enum"),
    _pg_check("f_is_uint256(quantity, true)", "ck_coupon_holding_quantity_uint256"),
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
