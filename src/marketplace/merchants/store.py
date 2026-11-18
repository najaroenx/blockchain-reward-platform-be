import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from re import fullmatch
from threading import BoundedSemaphore
from uuid import uuid4

from argon2 import PasswordHasher, exceptions, profiles
from sqlalchemy import Connection, Engine, insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError

from marketplace.infrastructure.idempotency import fingerprint
from marketplace.infrastructure.operation_history import record_operation_created
from marketplace.infrastructure.operation_models import (
    IdempotencyKey,
    Operation,
    OperationPlanStep,
    Outbox,
)
from marketplace.infrastructure.sql_idempotency import claim_key
from marketplace.merchants.models import Merchant, MerchantAdmin, Wallet
from marketplace.merchants.wallets import WalletProvider, WalletProviderError, WalletReference


@dataclass(frozen=True)
class Signup:
    name: str
    email: str
    email_normalized: str
    display_name: str
    password: str = field(repr=False)


@dataclass(frozen=True)
class SavedSignup:
    digest: bytes
    status: int | None
    body: str | None
    password_hash: str | None = field(repr=False)


class SignupFailure(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class SignupCredentials:
    def __init__(self) -> None:
        self._hasher = PasswordHasher.from_parameters(profiles.RFC_9106_LOW_MEMORY)
        self._slots = BoundedSemaphore(4)

    def hash(self, password: str) -> str:
        with self._slots:
            return self._hasher.hash(password)

    def matches(self, password: str, password_hash: str) -> bool:
        try:
            with self._slots:
                return self._hasher.verify(password_hash, password)
        except exceptions.VerifyMismatchError:
            return False


class SqlMerchantSignup:
    def __init__(
        self, engine: Engine, credentials: SignupCredentials, wallets: WalletProvider | None
    ) -> None:
        self.engine = (
            engine.execution_options(isolation_level="READ COMMITTED")
            if engine.dialect.name == "postgresql"
            else engine
        )
        self.credentials = credentials
        self.wallets = wallets

    def admit(self, key: str, signup: Signup) -> str:
        scope = "anon:" + sha256(signup.email_normalized.encode()).hexdigest()
        digest = fingerprint(
            {
                "name": signup.name,
                "email": signup.email_normalized,
                "displayName": signup.display_name,
            }
        )
        try:
            with self.engine.connect() as connection:
                saved = self._saved(connection, scope, key)
                if saved is None:
                    email_taken = connection.execute(
                        select(MerchantAdmin.id).where(
                            MerchantAdmin.email_normalized == signup.email_normalized
                        )
                    ).scalar_one_or_none()
                    if email_taken is not None:
                        saved = self._saved(connection, scope, key)
                        if saved is None:
                            raise SignupFailure("EMAIL_TAKEN")
            if saved is not None:
                return self._replay(saved, digest, signup.password)
            if self.wallets is None:
                raise SignupFailure("WALLET_UNAVAILABLE")

            password_hash = self.credentials.hash(signup.password)
            request_ref = sha256(json.dumps([scope, key]).encode()).hexdigest()
            wallet = self.wallets.reserve(request_ref)
            if (
                len(wallet.address) != 20
                or not any(wallet.address)
                or not fullmatch(
                    r"(?:kms|mock-kms)://[A-Za-z0-9][A-Za-z0-9._:/-]{0,511}", wallet.key_ref
                )
            ):
                raise WalletProviderError
            now = datetime.now(UTC)
            with self.engine.begin() as connection:
                claim = claim_key(connection, scope=scope, key=key, digest=digest, now=now)
                if claim is not None:
                    return self._create(connection, claim, signup, password_hash, wallet, now)
                saved = self._saved(connection, scope, key)
            if saved is None:
                raise SignupFailure("SIGNUP_UNAVAILABLE")
            return self._replay(saved, digest, signup.password)
        except (WalletProviderError, OSError):
            raise SignupFailure("WALLET_UNAVAILABLE") from None
        except (SQLAlchemyError, exceptions.Argon2Error):
            raise SignupFailure("SIGNUP_UNAVAILABLE") from None

    @staticmethod
    def _saved(connection: Connection, scope: str, key: str) -> SavedSignup | None:
        row = (
            connection.execute(
                select(
                    IdempotencyKey.fingerprint,
                    IdempotencyKey.response_status,
                    IdempotencyKey.response_body,
                    MerchantAdmin.password_hash,
                )
                .outerjoin(Operation, Operation.id == IdempotencyKey.operation_id)
                .outerjoin(MerchantAdmin, MerchantAdmin.id == Operation.actor_user_id)
                .where(IdempotencyKey.scope == scope, IdempotencyKey.key == key)
            )
            .tuples()
            .one_or_none()
        )
        return None if row is None else SavedSignup(*row)

    def _replay(self, saved: SavedSignup, digest: bytes, password: str) -> str:
        if saved.digest != digest:
            raise SignupFailure("IDEMPOTENCY_KEY_REUSED")
        if saved.status is None or saved.body is None:
            raise SignupFailure("IDEMPOTENCY_KEY_IN_PROGRESS")
        if saved.status != 201 or saved.password_hash is None:
            raise SignupFailure("SIGNUP_UNAVAILABLE")
        if not self.credentials.matches(password, saved.password_hash):
            raise SignupFailure("IDEMPOTENCY_KEY_REUSED")
        return saved.body

    @staticmethod
    def _create(
        connection: Connection,
        claim: str,
        signup: Signup,
        password_hash: str,
        wallet: WalletReference,
        now: datetime,
    ) -> str:
        merchant_id, admin_id, operation_id = (
            f"{prefix}_{uuid4().hex}" for prefix in ("mch", "adm", "op")
        )
        address = "0x" + wallet.address.hex()
        connection.execute(
            insert(Merchant).values(id=merchant_id, name=signup.name, created_at=now)
        )
        insert_admin = postgres_insert if connection.dialect.name == "postgresql" else sqlite_insert
        admin_added = connection.execute(
            insert_admin(MerchantAdmin)
            .values(
                id=admin_id,
                merchant_id=merchant_id,
                email=signup.email,
                email_normalized=signup.email_normalized,
                password_hash=password_hash,
                display_name=signup.display_name,
                created_at=now,
            )
            .on_conflict_do_nothing(index_elements=["email_normalized"])
            .returning(MerchantAdmin.id)
        ).scalar_one_or_none()
        if admin_added is None:
            raise SignupFailure("EMAIL_TAKEN")
        connection.execute(
            insert(Wallet).values(
                id=f"wal_{uuid4().hex}",
                address=wallet.address,
                key_ref=wallet.key_ref,
                owner_kind="MERCHANT",
                owner_id=merchant_id,
                created_at=now,
            )
        )
        connection.execute(
            insert(Operation).values(
                id=operation_id,
                kind="MERCHANT_PROVISION",
                status="PENDING",
                subject_id=merchant_id,
                actor_user_id=admin_id,
                created_at=now,
                payload={"merchantId": merchant_id, "walletAddress": address},
            )
        )
        record_operation_created(connection, operation_id, now)
        connection.execute(
            insert(OperationPlanStep),
            [
                {
                    "operation_id": operation_id,
                    "step_index": index,
                    "name": name,
                    "signer_role": signer,
                }
                for index, name, signer in (
                    (1, "addToWhitelist", "MARKETPLACE_OWNER"),
                    (2, "setApprovalForAll", "MERCHANT_WALLET"),
                )
            ],
        )
        connection.execute(
            insert(Outbox).values(
                id=f"ob_{uuid4().hex}",
                operation_id=operation_id,
                event_type="merchant.provision.v1",
                created_at=now,
            )
        )
        body = json.dumps(
            {
                "merchantId": merchant_id,
                "name": signup.name,
                "status": "PENDING",
                "createdAt": now.isoformat().replace("+00:00", "Z"),
                "admin": {"adminId": admin_id, "email": signup.email},
                "wallet": {"address": address, "ready": False},
                "provisioning": {"operationId": operation_id, "status": "PENDING"},
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        connection.execute(
            update(IdempotencyKey)
            .where(IdempotencyKey.id == claim)
            .values(operation_id=operation_id, response_status=201, response_body=body)
        )
        return body
