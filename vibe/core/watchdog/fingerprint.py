from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import JsonValue

_REDACTED = "[REDACTED]"
_SECRET_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
}
DEFAULT_EXCERPT_LIMIT = 2_000


def sanitize_artifact(
    value: JsonValue, *, excerpt_limit: int = DEFAULT_EXCERPT_LIMIT
) -> JsonValue:
    if isinstance(value, dict):
        return {
            key: _REDACTED
            if key.lower() in _SECRET_KEYS
            else sanitize_artifact(item, excerpt_limit=excerpt_limit)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_artifact(item, excerpt_limit=excerpt_limit) for item in value]
    if isinstance(value, str) and len(value) > excerpt_limit:
        return f"{value[:excerpt_limit]}…[TRUNCATED]"
    return value


def canonical_json(value: JsonValue) -> str:
    sanitized = sanitize_artifact(value)
    return json.dumps(
        sanitized, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def fingerprint(value: JsonValue) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def fingerprint_call(tool_name: str, arguments: dict[str, JsonValue]) -> str:
    return fingerprint({"tool": tool_name, "arguments": arguments})


def fingerprint_result(result_type: str, result: JsonValue) -> str:
    return fingerprint({"type": result_type, "result": result})


def fingerprint_error(error_class: str, message: str) -> str:
    normalized = " ".join(message.split())
    return fingerprint({"class": error_class, "message": normalized})


def fingerprint_repository(changed_files: dict[str, str]) -> str:
    normalized: dict[str, JsonValue] = {
        Path(path).as_posix(): identity for path, identity in changed_files.items()
    }
    return fingerprint(normalized)


def fingerprint_evidence(
    detector: str, *parts: str, repository: str | None = None
) -> str:
    return fingerprint({"detector": detector, "parts": list(parts), "repo": repository})
