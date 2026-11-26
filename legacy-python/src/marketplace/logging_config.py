import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TextIO

MASK = "[redacted]"
SECRET_KEYS = (
    "password",
    "privatekey",
    "secret",
    "token",
    "authorization",
    "cookie",
    "idempotencykey",
    "keyref",
    "keyarn",
    "mnemonic",
    "seed",
    "code",
    "databaseurl",
    "rpcurl",
)


def redact(value: object, *, depth: int = 0, seen: set[int] | None = None) -> object:
    if depth > 12:
        return "[depth-limit]"
    if isinstance(value, BaseException):
        return {"error_type": type(value).__name__}
    if isinstance(value, Mapping | list | tuple):
        visited = set() if seen is None else seen
        if id(value) in visited:
            return "[circular]"
        visited.add(id(value))
        if isinstance(value, Mapping):
            return {
                str(key): MASK
                if any(
                    secret in str(key).lower().replace("_", "").replace("-", "")
                    for secret in SECRET_KEYS
                )
                else redact(item, depth=depth + 1, seen=visited)
                for key, item in value.items()
            }
        return [redact(item, depth=depth + 1, seen=visited) for item in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return f"[{type(value).__name__}]"


class JsonFormatter(logging.Formatter):
    def __init__(self, role: str) -> None:
        super().__init__()
        self.role = role

    def format(self, record: logging.LogRecord) -> str:
        # Messages are event names. Put all variable data in the structured fields.
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname.lower(),
            "role": self.role,
            "event": record.msg if isinstance(record.msg, str) else "application_event",
            "fields": record.msg
            if isinstance(record.msg, Mapping)
            else getattr(record, "fields", {}),
        }
        if record.exc_info and record.exc_info[0]:
            payload["error_type"] = record.exc_info[0].__name__
        return json.dumps(redact(payload), ensure_ascii=False)


def configure_logging(role: str, level: str, stream: TextIO | None = None) -> logging.Logger:
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(role))
    logger = logging.getLogger(f"marketplace.{role}")
    logger.handlers = [handler]
    logger.propagate = False
    levels = {"trace": logging.DEBUG, "warn": logging.WARNING, "fatal": logging.CRITICAL}
    logger.setLevel(levels.get(level, getattr(logging, level.upper(), logging.INFO)))
    return logger
