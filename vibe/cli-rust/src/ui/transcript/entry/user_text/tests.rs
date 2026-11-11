use serde_json::json;

use super::sticky;
use crate::server::MessageEntry;
use crate::transcript::FiredLoop;
use crate::ui::theme;

fn prompt(text: &str) -> MessageEntry {
    serde_json::from_value(json!({"role": "user", "content": [{"type": "text", "text": text}]}))
        .expect("a user message")
}

fn rows(text: &str, width: u16) -> Vec<String> {
    sticky(&prompt(text), None, width, 3)
        .lines()
        .iter()
        .map(|line| line.to_string().trim_end().to_owned())
        .collect()
}

#[test]
fn a_prompt_that_fits_keeps_its_rows_then_the_separator() {
    assert_eq!(
        rows("one\ntwo\nthree", 12),
        ["> one", "  two", "  three", &"─".repeat(12)]
    );
}

#[test]
fn a_longer_prompt_is_cut_on_its_third_row_with_an_ellipsis() {
    assert_eq!(
        rows("one\ntwo\nthree\nfour", 12),
        ["> one", "  two", "  three…", &"─".repeat(12)]
    );
}

#[test]
fn a_long_single_line_ellipsizes_within_the_width() {
    let lines = rows(&"word ".repeat(400), 22);
    assert_eq!(lines.len(), 4);
    assert!(lines[2].ends_with('…'), "{lines:?}");
    assert!(
        lines.iter().all(|line| line.chars().count() <= 22),
        "{lines:?}"
    );
}

fn fired() -> FiredLoop {
    FiredLoop {
        loop_id: "ae1aa08a".into(),
        fired_at: None,
    }
}

fn loop_rows(text: &str, width: u16) -> Vec<String> {
    sticky(&prompt(text), Some(&fired()), width, 3)
        .lines()
        .iter()
        .map(|line| line.to_string().trim_end().to_owned())
        .collect()
}

#[test]
fn a_loop_prompt_pins_as_its_folded_title_in_the_primary_marker_colour() {
    let lines = sticky(
        &prompt("Say hi\nSay hi to the user."),
        Some(&fired()),
        30,
        3,
    );
    assert_eq!(lines.lines()[0].spans[0].style.fg, Some(theme::primary()));
    assert_eq!(
        loop_rows("Say hi\nSay hi to the user.", 30),
        ["⏵ Say hi", &"─".repeat(30)]
    );
    let lines = sticky(&prompt("tick"), None, 20, 3);
    assert_eq!(lines.lines()[0].spans[0].style.fg, Some(theme::ORANGE));
}

#[test]
fn a_long_loop_title_wraps_then_is_cut_on_its_third_row() {
    let title = "word ".repeat(40);
    let lines = loop_rows(&format!("{title}\nthe instructions"), 22);
    assert_eq!(lines.len(), 4, "{lines:?}");
    assert!(lines[0].starts_with("⏵ word"), "{lines:?}");
    assert!(lines[2].ends_with('…'), "{lines:?}");
    assert!(
        lines.iter().all(|line| line.chars().count() <= 22),
        "{lines:?}"
    );
    assert!(
        lines
            .iter()
            .all(|line| !line.contains("instructions") && !line.contains("loop")),
        "{lines:?}"
    );
}

#[test]
fn a_cut_inside_zero_width_marks_still_shows_the_ellipsis() {
    let lines = rows(&format!("{}\nnext", "e\u{301}".repeat(400)), 12);
    assert!(lines[2].ends_with('…'), "{lines:?}");
    assert!(lines.iter().all(|line| !line.contains("next")), "{lines:?}");
}
