from __future__ import annotations

import pytest

from vibe.cli.textual_ui.count_format import format_compact_count, format_count_markdown


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "0"),
        (999, "999"),
        (1_000, "1k"),
        (1_234, "1.23k"),
        (1_005, "1.01k"),
        (12_345, "12.3k"),
        (123_456, "123k"),
        (999_499, "999k"),
        (999_500, "1M"),
        (1_234_567, "1.23M"),
        (9_995_000, "10M"),
        (45_000_000, "45M"),
        (2_500_000_000, "2.5B"),
        (7_000_000_000_000, "7T"),
        (12_345_678_901_234_567, "12300T"),
    ],
)
def test_format_compact_count_keeps_three_significant_digits(
    value: int, expected: str
) -> None:
    assert format_compact_count(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "0"),
        (999, "999"),
        (1_000, "1k _(1,000)_"),
        (1_234_567, "1.23M _(1,234,567)_"),
        (999_999, "1M _(999,999)_"),
    ],
)
def test_format_count_markdown_emphasizes_exact_value_from_one_thousand(
    value: int, expected: str
) -> None:
    assert format_count_markdown(value) == expected
