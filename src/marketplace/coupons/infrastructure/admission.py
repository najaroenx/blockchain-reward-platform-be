import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import Connection, Engine, insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError

from marketplace.coupons.application.dto import RedeemCouponPayload, RedeemCouponResult
from marketplace.coupons.application.exceptions import RedeemErrorCode, RedeemFailure
from marketplace.infrastructure.idempotency import fingerprint
from marketplace.infrastructure.operation_models import IdempotencyKey, Operation, Outbox

REDEEM_ENDPOINT = "POST:/v1/coupons/redeem"


def normalized_payload(payload: RedeemCouponPayload) -> dict[str, str]:
    return {
        "chainId": str(payload.chain_id),
        "couponContractAddress": payload.coupon_contract_address.lower(),
        "walletAddress": payload.wallet_address.lower(),
        "typeId": str(payload.type_id),
        "amount": str(payload.amount),
    }


class SqlRedeemAdmissionStore:
    def __init__(self, engine: Engine) -> None:
        # A conflicting INSERT may wait for another transaction. Its subsequent
        # SELECT must see the newly committed acknowledgement, not an old snapshot.
        self.engine = (
            engine.execution_options(isolation_level="READ COMMITTED")
            if engine.dialect.name == "postgresql"
            else engine
        )

    def admit(
        self, *, user_id: str, idempotency_key: str, payload: RedeemCouponPayload
    ) -> RedeemCouponResult:
        resolved = normalized_payload(payload)
        digest = fingerprint(resolved)
        scope = f"user:{user_id}:{REDEEM_ENDPOINT}"
        now = datetime.now(UTC)
        try:
            with self.engine.begin() as connection:
                return self._admit(
                    connection, scope, user_id, idempotency_key, resolved, digest, now
                )
        except SQLAlchemyError:
            # The transaction context has rolled back. SQL errors are never
            # interpreted as duplicate requests or converted into a success.
            raise RedeemFailure(RedeemErrorCode.ADMISSION_UNAVAILABLE) from None

    def _admit(
        self,
        connection: Connection,
        scope: str,
        user_id: str,
        key: str,
        payload: dict[str, str],
        digest: bytes,
        now: datetime,
    ) -> RedeemCouponResult:
        claim = (
            (postgres_insert if connection.dialect.name == "postgresql" else sqlite_insert)(
                IdempotencyKey
            )
            .values(
                id="ik_" + uuid4().hex,
                scope=scope,
                key=key,
                fingerprint=digest,
                created_at=now,
                expires_at=now + timedelta(hours=72),
            )
            .on_conflict_do_nothing(index_elements=["scope", "key"])
            .returning(IdempotencyKey.id)
        )
        inserted = connection.execute(claim).scalar_one_or_none()
        if inserted is None:
            saved = connection.execute(
                select(
                    IdempotencyKey.fingerprint,
                    IdempotencyKey.operation_id,
                    IdempotencyKey.response_status,
                    IdempotencyKey.response_body,
                ).where(IdempotencyKey.scope == scope, IdempotencyKey.key == key)
            ).one()
            if saved.fingerprint != digest:
                raise RedeemFailure(RedeemErrorCode.IDEMPOTENCY_KEY_REUSED)
            if saved.operation_id is None or saved.response_body is None:
                raise RedeemFailure(RedeemErrorCode.IDEMPOTENCY_KEY_IN_PROGRESS)
            if saved.response_status != 202:
                raise RedeemFailure(RedeemErrorCode.INTERNAL_ERROR)
            return RedeemCouponResult(
                operation_id=saved.operation_id,
                type_id=int(payload["typeId"]),
                response_body=saved.response_body,
            )

        operation_id = "op_" + uuid4().hex
        response_body = json.dumps(
            {
                "operationId": operation_id,
                "status": "PENDING",
                "typeId": payload["typeId"],
                "amount": "1",
            },
            separators=(",", ":"),
        )
        connection.execute(
            insert(Operation).values(
                id=operation_id,
                kind="COUPON_REDEEM",
                actor_user_id=user_id,
                status="PENDING",
                payload=payload,
                created_at=now,
            )
        )
        connection.execute(
            insert(Outbox).values(
                id="ob_" + uuid4().hex,
                operation_id=operation_id,
                event_type="COUPON_REDEEM_REQUESTED",
                created_at=now,
            )
        )
        connection.execute(
            update(IdempotencyKey)
            .where(IdempotencyKey.id == inserted)
            .values(
                operation_id=operation_id,
                response_status=202,
                response_body=response_body,
            )
        )
        return RedeemCouponResult(
            operation_id=operation_id,
            type_id=int(payload["typeId"]),
            response_body=response_body,
        )
