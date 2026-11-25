import json
from datetime import datetime
from uuid import uuid4

from sqlalchemy import Connection, Engine, insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError

from marketplace.infrastructure.operation_history import record_operation_created
from marketplace.infrastructure.operation_models import (
    IdempotencyKey,
    Operation,
    OperationPlanStep,
    Outbox,
)
from marketplace.infrastructure.sql_idempotency import claim_key
from marketplace.merchants.application.interfaces.signup import SavedSignup, SignupRepository
from marketplace.merchants.domain.entities.signup import Signup, WalletReference
from marketplace.merchants.domain.exceptions import SignupFailure
from marketplace.merchants.infrastructure.models import Merchant, MerchantAdmin, Wallet


# Implements SQL preflight checks and persists the signup records in a single transaction.
class SqlMerchantSignup(SignupRepository):
    def __init__(self, engine: Engine) -> None:
        self.engine = (
            engine.execution_options(isolation_level="READ COMMITTED")
            if engine.dialect.name == "postgresql"
            else engine
        )

    def probe(self, scope: str, key: str, email: str) -> SavedSignup | None:
        try:
            with self.engine.connect() as connection:
                saved = self._saved(connection, scope, key)
                if saved is None:
                    email_taken = connection.execute(
                        select(MerchantAdmin.id).where(MerchantAdmin.email_normalized == email)
                    ).scalar_one_or_none()
                    if email_taken is not None:
                        saved = self._saved(connection, scope, key)
                        if saved is None:
                            raise SignupFailure("EMAIL_TAKEN")
                return saved
        except SQLAlchemyError:
            raise SignupFailure("SIGNUP_UNAVAILABLE") from None

    def save(
        self,
        signup: Signup,
        password_hash: str,
        wallet: WalletReference,
        now: datetime,
        *,
        scope: str,
        key: str,
        digest: bytes,
    ) -> str | SavedSignup:
        try:
            with self.engine.begin() as connection:
                claim = claim_key(connection, scope=scope, key=key, digest=digest, now=now)
                if claim is not None:
                    return self._create(connection, claim, signup, password_hash, wallet, now)
                saved = self._saved(connection, scope, key)
            if saved is None:
                raise SignupFailure("SIGNUP_UNAVAILABLE")
            return saved
        except SQLAlchemyError:
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
