from __future__ import annotations

import pytest

from vibe.core.loop import MIN_INTERVAL_SECONDS, LoopError, parse_interval


class TestParseInterval:
    def test_parses_supported_units(self) -> None:
        assert parse_interval("30s") == 30
        assert parse_interval("5m") == 300
        assert parse_interval("2h") == 7200
        assert parse_interval("1d") == 86400

    def test_parses_case_insensitively(self) -> None:
        assert parse_interval("30S") == 30
        assert parse_interval("2H") == 7200

    def test_strips_whitespace(self) -> None:
        assert parse_interval("  30s  ") == 30

    @pytest.mark.parametrize("bad", ["", "5", "5x", "-5m", "5 m", "1.5m", "abc"])
    def test_rejects_invalid_inputs(self, bad: str) -> None:
        with pytest.raises(LoopError):
            parse_interval(bad)

    def test_rejects_below_minimum(self) -> None:
        with pytest.raises(LoopError, match=f"at least {MIN_INTERVAL_SECONDS}"):
            parse_interval("29s")
