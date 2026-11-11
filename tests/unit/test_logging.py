import io
import json

from marketplace.logging_config import MASK, configure_logging


def test_nested_secrets_are_removed_from_actual_log_output() -> None:
    output = io.StringIO()
    log = configure_logging("api", "info", output)
    log.info(
        "user_event",
        extra={
            "fields": {
                "password": "hunter2",
                "wallet": {"private_key": "PRIVATE", "key-ref": "kms-secret"},
                "headers": {"Authorization": "Bearer secret", "Cookie": "session=secret"},
                "database_url": "postgresql://user:password@localhost/reward",
                "idempotencyKey": "KEY",
                "display_name": "สมชาย",
                "safe": [1, {"access_token": "TOKEN"}],
            }
        },
    )
    line = output.getvalue()
    for value in ("hunter2", "PRIVATE", "kms-secret", "Bearer secret", "session=secret", "TOKEN"):
        assert value not in line
    assert "สมชาย" in line
    assert MASK in line
    assert json.loads(line)["role"] == "api"


def test_deep_and_circular_payloads_do_not_leak() -> None:
    output = io.StringIO()
    log = configure_logging("api", "debug", output)
    nested: dict[str, object] = {"password": "DEEP-SECRET"}
    for _ in range(20):
        nested = {"nested": nested}
    circular: dict[str, object] = {}
    circular["self"] = circular
    log.info({"deep": nested, "cycle": circular})
    assert "DEEP-SECRET" not in output.getvalue()
    assert "[depth-limit]" in output.getvalue()
    assert "[circular]" in output.getvalue()


def test_exception_details_are_not_serialized() -> None:
    output = io.StringIO()
    log = configure_logging("worker", "info", output)
    try:
        raise ValueError("SECRET database password")
    except ValueError:
        log.exception("database_failed")
    assert "SECRET" not in output.getvalue()
    assert json.loads(output.getvalue())["error_type"] == "ValueError"
