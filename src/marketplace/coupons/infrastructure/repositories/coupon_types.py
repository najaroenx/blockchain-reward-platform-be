import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from marketplace.coupons.application.dto import CreateCouponType
from marketplace.coupons.application.exceptions import (
    DuplicateCouponTypeName,
    IdempotencyKeyConflict,
)
from marketplace.coupons.application.interfaces.coupons import CouponTypeRepository
from marketplace.coupons.domain.entities.coupon import CouponTypeDraft
from marketplace.coupons.infrastructure.models import CouponTypeIdempotencyKey, CouponTypeModel


# Implements the draft port with SQL reads and idempotent creation under unique constraints.
class SqlCouponTypeRepository(CouponTypeRepository):
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_or_replay(
        self, data: CreateCouponType, *, idempotency_key: str, request_fingerprint: str
    ) -> CouponTypeDraft:
        replay = self._replay(idempotency_key, request_fingerprint)
        if replay is not None:
            return replay

        coupon_type = CouponTypeModel(
            id=str(uuid.uuid4()),
            name=data.name,
            start_date=data.start_date,
            expire_date=data.expire_date,
            face_value=data.face_value,
            total_supply=data.total_supply,
            chain_type_id=None,
            created_at=datetime.now(UTC),
        )
        try:
            # Two flushes, not one add+flush: without a declared relationship()
            # between the mappers, a single flush does not guarantee the parent
            # coupon_types row is inserted before the FK row that references it.
            self.session.add(coupon_type)
            self.session.flush()
            self.session.add(
                CouponTypeIdempotencyKey(
                    idempotency_key=idempotency_key,
                    request_fingerprint=request_fingerprint,
                    coupon_type_id=coupon_type.id,
                    created_at=datetime.now(UTC),
                )
            )
            self.session.flush()
        except IntegrityError:
            # Lost a race against a concurrent identical request, or the name is already
            # taken by a different coupon type. Roll back and reconcile from committed state
            # rather than trusting which constraint fired.
            self.session.rollback()
            replay = self._replay(idempotency_key, request_fingerprint)
            if replay is not None:
                return replay
            if self.session.scalar(
                select(CouponTypeModel).where(CouponTypeModel.name == data.name)
            ):
                raise DuplicateCouponTypeName from None
            raise
        self.session.commit()
        return coupon_type.to_domain()

    def _replay(self, idempotency_key: str, request_fingerprint: str) -> CouponTypeDraft | None:
        existing = self.session.get(CouponTypeIdempotencyKey, idempotency_key)
        if existing is None:
            return None
        if existing.request_fingerprint != request_fingerprint:
            raise IdempotencyKeyConflict
        coupon_type = self.session.get(CouponTypeModel, existing.coupon_type_id)
        assert coupon_type is not None
        return coupon_type.to_domain()

    def find(self, coupon_type_id: str) -> CouponTypeDraft | None:
        row = self.session.get(CouponTypeModel, coupon_type_id)
        return row.to_domain() if row is not None else None

    def list(self, *, limit: int, offset: int) -> tuple[tuple[CouponTypeDraft, ...], int]:
        total = self.session.scalar(select(func.count()).select_from(CouponTypeModel))
        rows = self.session.scalars(
            select(CouponTypeModel)
            .order_by(CouponTypeModel.created_at.desc(), CouponTypeModel.id)
            .limit(limit)
            .offset(offset)
        )
        return tuple(row.to_domain() for row in rows), total or 0
