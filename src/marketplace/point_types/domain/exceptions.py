# Carries a stable point type creation failure code for the application and HTTP boundary.
class PointTypeFailure(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)
