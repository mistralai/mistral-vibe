//! Text casing: the first letter upper-cased, the rest kept.

use vibe_rs::utils::text::capitalize;

#[test]
fn capitalize_upper_cases_only_the_first_letter() {
    for (value, expected) in [
        ("", ""),
        ("global", "Global"),
        ("Global", "Global"),
        ("workspace skill", "Workspace skill"),
        ("été", "Été"),
        ("x-y", "X-y"),
    ] {
        assert_eq!(capitalize(value), expected, "{value:?}");
    }
}
