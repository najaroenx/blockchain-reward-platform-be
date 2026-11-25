# Signals that an operation read could not return consistent data.
class OperationsUnavailable(Exception):
    pass


# Signals that an operation could not be found within the authenticated actor's scope.
class OperationNotFound(Exception):
    pass
