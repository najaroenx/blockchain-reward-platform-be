class SignupFailure(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class SignupRateLimited(Exception):
    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after
        super().__init__("RATE_LIMITED")
