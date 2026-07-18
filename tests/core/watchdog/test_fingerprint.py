from __future__ import annotations

from vibe.core.watchdog import canonical_json, fingerprint_call, fingerprint_error


def test_fingerprint_is_independent_of_mapping_order() -> None:
    first = fingerprint_call("bash", {"command": "pwd", "timeout": 10})
    second = fingerprint_call("bash", {"timeout": 10, "command": "pwd"})

    assert first == second


def test_error_fingerprint_normalizes_whitespace() -> None:
    assert fingerprint_error("RuntimeError", "bad   result\nnow") == fingerprint_error(
        "RuntimeError", "bad result now"
    )


def test_canonical_json_redacts_secrets_and_bounds_text() -> None:
    serialized = canonical_json({"api_key": "private", "output": "x" * 2_100})

    assert "private" not in serialized
    assert "[REDACTED]" in serialized
    assert "[TRUNCATED]" in serialized
