from __future__ import annotations

from vibe.app_server._utils import optional_time_ms, time_ms


def test_time_ms_parses_iso_timestamp() -> None:
    assert time_ms("2026-09-10T12:00:00+00:00") == 1_789_041_600_000


def test_time_ms_uses_fallback_for_malformed_timestamp() -> None:
    assert time_ms("not-a-timestamp", fallback=lambda: 123_456) == 123_456


def test_optional_time_ms_returns_none_for_missing_or_malformed_timestamp() -> None:
    assert optional_time_ms(None) is None
    assert optional_time_ms("not-a-timestamp") is None
