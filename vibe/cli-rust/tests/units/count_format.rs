//! Human-readable counts: compact value first, exact value second.

use vibe_rs::utils::text::{format_compact_count, format_count_markdown};

#[test]
fn compact_count_keeps_three_significant_digits() {
    for (value, expected) in [
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
    ] {
        assert_eq!(format_compact_count(value), expected, "{value}");
    }
}

#[test]
fn compact_count_handles_the_largest_value() {
    assert_eq!(format_compact_count(u64::MAX), "18400000T");
}

#[test]
fn count_markdown_emphasizes_exact_value_from_one_thousand() {
    for (value, expected) in [
        (0, "0"),
        (999, "999"),
        (1_000, "1k _(1,000)_"),
        (1_234_567, "1.23M _(1,234,567)_"),
        (999_999, "1M _(999,999)_"),
    ] {
        assert_eq!(format_count_markdown(value), expected, "{value}");
    }
}
