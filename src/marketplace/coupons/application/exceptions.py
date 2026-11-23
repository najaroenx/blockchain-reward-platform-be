class DuplicateCouponTypeName(Exception):
    pass


class IdempotencyKeyConflict(Exception):
    pass


class CouponTypeNotFound(Exception):
    pass
