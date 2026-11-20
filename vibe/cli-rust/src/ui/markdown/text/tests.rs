use ratatui::style::Style;

use super::{graphemes, word_width, wrap_chars, Words};
use crate::ui::markdown::Sc;

fn chars(text: &str) -> Vec<Sc> {
    text.chars().map(|c| (c, Style::default(), None)).collect()
}

#[test]
fn height_matches_wrap_chars() {
    let texts = [
        "",
        " ",
        "a",
        "  leading indent then words",
        "trailing spaces   ",
        "one two  three   four",
        "agents/harness/harness/plugins/schemas/mistral-vibe/1.0.0/hooks.schema.json",
        "+8/-2 : ajoute pre_agent et notification au schéma de hooks.toml",
        "mixed short and averyveryverylongwordthatmustbreak between words",
        "line one\nline two\n\nafter a blank line",
        "emoji 👍🏽 and 家族 wide glyphs 漢字漢字漢字漢字 split",
        "a  \n  b",
        "x \u{0301}combining marks é",
    ];
    for text in texts {
        let text = chars(text);
        let words = Words::new(&text);
        for width in 0..90 {
            assert_eq!(
                words.height(width),
                wrap_chars(&text, width).len(),
                "{:?} at width {width}",
                text.iter().map(|sc| sc.0).collect::<String>(),
            );
        }
    }
}

#[test]
fn word_width_matches_grapheme_widths() {
    let ascii = (' '..='~').collect::<String>();
    for text in [
        ascii.as_str(),
        "tab\there",
        "na\u{ef}ve",
        "e\u{301}",
        "漢字",
        "👍🏽",
        "\u{7f}",
    ] {
        let text = chars(text);
        let segmented = graphemes(&text)
            .iter()
            .map(|&(_, width)| width)
            .sum::<usize>();
        assert_eq!(word_width(&text), segmented, "{text:?}");
    }
}

#[test]
fn height_matches_wrap_chars_for_graphemes_wider_than_a_byte() {
    let text = chars(&format!("x {} y", "\u{1100}".repeat(300)));
    let words = Words::new(&text);
    for width in [250, 300, 599, 600, 700] {
        assert_eq!(
            words.height(width),
            wrap_chars(&text, width).len(),
            "width {width}"
        );
    }
}
