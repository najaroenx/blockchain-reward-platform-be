import json
from collections.abc import Mapping
from hashlib import sha256
from unicodedata import normalize


def canonical_json(value: Mapping[str, str]) -> str:
    return normalize(
        "NFC", json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )


def fingerprint(value: Mapping[str, str]) -> bytes:
    """Reproducible SHA-256; contains no salt, key, or process-specific input."""
    return sha256(canonical_json(value).encode("utf-8")).digest()
