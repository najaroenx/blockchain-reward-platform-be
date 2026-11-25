# Signals a failure while obtaining or validating payment instructions.
class PaymentProviderError(Exception):
    pass


# Signals that the payment reference already belongs to a different amount.
class PaymentReferenceConflict(PaymentProviderError):
    pass
