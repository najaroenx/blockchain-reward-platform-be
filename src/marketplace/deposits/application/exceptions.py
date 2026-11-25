class PaymentProviderError(Exception):
    pass


class PaymentReferenceConflict(PaymentProviderError):
    pass
