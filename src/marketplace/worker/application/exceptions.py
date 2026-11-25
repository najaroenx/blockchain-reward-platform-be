# Carries the underlying error type when the Worker cannot access the outbox.
class OutboxUnavailable(Exception):
    def __init__(self, error_type: str) -> None:
        self.error_type = error_type
        super().__init__(error_type)
