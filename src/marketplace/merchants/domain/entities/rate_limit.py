from threading import Lock
from time import monotonic


# Tracks signup attempts per client and determines when to apply a retry delay.
class SignupRateLimit:
    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._minute = -1
        self._counts: dict[str, int] = {}
        self._lock = Lock()

    def retry_after(self, address: str) -> int | None:
        with self._lock:
            minute, seconds = divmod(int(monotonic()), 60)
            if minute != self._minute:
                self._counts.clear()
                self._minute = minute
            count = self._counts.get(address, 0)
            if count >= self.per_minute or (count == 0 and len(self._counts) >= 4096):
                return 60 - seconds
            self._counts[address] = count + 1
            return None
