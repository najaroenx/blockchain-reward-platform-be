import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Connection, Engine, insert, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from marketplace.infrastructure.idempotency import fingerprint
from marketplace.infrastructure.jwt_auth import MerchantIdentity
from marketplace.infrastructure.operation_history import record_operation_created
from marketplace.infrastructure.operation_models import IdempotencyKey, Operation, Outbox
from marketplace.infrastructure.sql_idempotency import claim_key
from marketplace.merchants.models import Merchant, MerchantAdmin, Wallet
from marketplace.point_types.models import PointType

POINT_DECIMALS = 18


class PointTypeFailure(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def point_type_conflict(error: IntegrityError) -> str:
    diagnostic = getattr(error.orig, "diag", None)
    constraint = getattr(diagnostic, "constraint_name", "") or ""
    if diagnostic is None:
        # SQLite reports columns for simple indexes and index names for expression indexes.
        message = str(error.orig).removeprefix("UNIQUE constraint failed: ")
        constraint = {
            "point_type.merchant_id, point_type.symbol": "uq_point_symbol",
            "index 'uq_point_name'": "uq_point_name",
            "ck_point_type_merchant_active": "ck_point_type_merchant_active",
        }.get(message, "")
    return {
        "uq_point_name": "NAME_TAKEN",
        "uq_point_symbol": "SYMBOL_TAKEN",
        "ck_point_type_merchant_active": "MERCHANT_NOT_ACTIVE",
    }.get(constraint, "POINT_TYPE_UNAVAILABLE")


class SqlPointTypes:
    def __init__(self, engine: Engine) -> None:
        self.engine = (
            engine.execution_options(isolation_level="READ COMMITTED")
            if engine.dialect.name == "postgresql"
            else engine
        )

    def create(
        self, *, identity: MerchantIdentity, key: str, name: str, symbol: str
    ) -> tuple[str, str]:
        scope = f"point-type:create:{identity.user_id}"
        digest = fingerprint(
            {
                "merchantId": identity.merchant_id,
                "name": name,
                "symbol": symbol,
                "decimals": str(POINT_DECIMALS),
            }
        )
        now = datetime.now(UTC)

        try:
            with self.engine.begin() as connection:
                claim = claim_key(connection, scope=scope, key=key, digest=digest, now=now)
                self._require_admin(connection, identity)
                if claim is None:
                    return self._replay(connection, scope, key, digest)
                self._require_wallet(connection, identity.merchant_id)

                point_type_id = "pt_" + uuid4().hex
                operation_id = "op_" + uuid4().hex
                response = json.dumps(
                    {
                        "pointTypeId": point_type_id,
                        "name": name,
                        "symbol": symbol,
                        "decimals": POINT_DECIMALS,
                        "status": "REQUESTED",
                        "operationId": operation_id,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                connection.execute(
                    insert(PointType).values(
                        id=point_type_id,
                        merchant_id=identity.merchant_id,
                        name=name,
                        symbol=symbol,
                        decimals=POINT_DECIMALS,
                        created_at=now,
                    )
                )
                connection.execute(
                    insert(Operation).values(
                        id=operation_id,
                        kind="POINT_TYPE_CREATE",
                        actor_user_id=identity.user_id,
                        subject_id=point_type_id,
                        status="PENDING",
                        payload={
                            "pointTypeId": point_type_id,
                            "merchantId": identity.merchant_id,
                            "name": name,
                            "symbol": symbol,
                        },
                        created_at=now,
                    )
                )
                record_operation_created(connection, operation_id, now)
                connection.execute(
                    insert(Outbox).values(
                        id="ob_" + uuid4().hex,
                        operation_id=operation_id,
                        event_type="point-type.create.v1",
                        created_at=now,
                    )
                )
                connection.execute(
                    update(IdempotencyKey)
                    .where(IdempotencyKey.id == claim)
                    .values(operation_id=operation_id, response_status=202, response_body=response)
                )
                return operation_id, response
        except IntegrityError as error:
            raise PointTypeFailure(point_type_conflict(error)) from None
        except SQLAlchemyError:
            raise PointTypeFailure("POINT_TYPE_UNAVAILABLE") from None

    @staticmethod
    def _require_admin(connection: Connection, identity: MerchantIdentity) -> None:
        admin = connection.scalar(
            select(MerchantAdmin.id)
            .where(
                MerchantAdmin.id == identity.user_id,
                MerchantAdmin.merchant_id == identity.merchant_id,
                MerchantAdmin.disabled_at.is_(None),
            )
            .with_for_update(read=True)
        )
        if admin is None:
            raise PointTypeFailure("FORBIDDEN")

    @staticmethod
    def _require_wallet(connection: Connection, merchant_id: str) -> None:
        status = connection.scalar(
            select(Merchant.status).where(Merchant.id == merchant_id).with_for_update(read=True)
        )
        if status != "ACTIVE":
            raise PointTypeFailure("MERCHANT_NOT_ACTIVE")
        wallet = connection.scalar(
            select(Wallet.id)
            .where(
                Wallet.owner_kind == "MERCHANT",
                Wallet.owner_id == merchant_id,
                Wallet.address != bytes(20),
            )
            .with_for_update(read=True)
        )
        if wallet is None:
            raise PointTypeFailure("MERCHANT_WALLET_NOT_READY")

    @staticmethod
    def _replay(connection: Connection, scope: str, key: str, digest: bytes) -> tuple[str, str]:
        row = (
            connection.execute(
                select(
                    IdempotencyKey.fingerprint,
                    IdempotencyKey.operation_id,
                    IdempotencyKey.response_status,
                    IdempotencyKey.response_body,
                ).where(IdempotencyKey.scope == scope, IdempotencyKey.key == key)
            )
            .tuples()
            .one_or_none()
        )
        if row is None:
            raise PointTypeFailure("POINT_TYPE_UNAVAILABLE")
        saved_digest, operation_id, status, body = row
        if saved_digest != digest:
            raise PointTypeFailure("IDEMPOTENCY_KEY_REUSED")
        if operation_id is None or body is None:
            raise PointTypeFailure("IDEMPOTENCY_KEY_IN_PROGRESS")
        if status != 202:
            raise PointTypeFailure("POINT_TYPE_UNAVAILABLE")
        return operation_id, body
