use serde_json::json;

use super::{push_effect, EffectView};
use crate::server::EffectEntry;
use crate::ui::markdown::LinkedLines;

const WIDTH: u16 = 60;
const COMMAND: &str =
    "uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --log-level debug";

fn rows(message: &str, suffix: &str, expanded: bool) -> Vec<String> {
    let effect: EffectEntry = serde_json::from_value(json!({
        "detail": {"kind": "shell", "display": {"verb": "Running", "message": message}},
        "state": {"status": "completed", "outputText": "ok",
            "output": {"stdout": "ok", "stderr": "", "output": "ok", "truncated": false},
            "display": {"success": true,
            "verb": "Started", "message": message, "suffix": suffix}}
    }))
    .unwrap();
    let view = EffectView {
        index: 0,
        rev: 0,
        in_progress: false,
        local: false,
        expanded,
        grouped: false,
        stream_delta: None,
    };
    let mut lines = LinkedLines::default();
    push_effect(&mut lines, &effect, view, None, 0, WIDTH, None);
    lines.lines().iter().map(ToString::to_string).collect()
}

fn fits(rows: &[String]) -> bool {
    rows.iter()
        .all(|row| row.chars().count() <= usize::from(WIDTH))
}

#[test]
fn collapsed_header_keeps_its_suffix_after_the_ellipsis() {
    let rows = rows(COMMAND, "(running)", false);
    assert_eq!(rows.len(), 1, "{rows:?}");
    assert!(rows[0].ends_with("… (running)"), "{rows:?}");
    assert!(fits(&rows), "{rows:?}");
}

#[test]
fn expanded_header_ends_with_its_suffix_after_the_last_word() {
    let plain = rows(COMMAND, "", true);
    let tagged = rows(COMMAND, "(running)", true);
    assert!(plain.len() > 2, "{plain:?}");
    assert_eq!(tagged.len(), plain.len(), "{tagged:?}");
    let last = plain
        .iter()
        .rposition(|row| row.ends_with("debug"))
        .unwrap();
    for (index, (tagged, plain)) in tagged.iter().zip(&plain).enumerate() {
        let expected = match index == last {
            true => format!("{plain} (running)"),
            false => plain.clone(),
        };
        assert_eq!(tagged, &expected, "{index}");
    }
    assert!(fits(&tagged), "{tagged:?}");
}

#[test]
fn expanded_header_moves_a_suffix_that_does_not_fit_to_its_own_row() {
    let message = "a".repeat(95);
    let plain = rows(&message, "", true);
    let tagged = rows(&message, "(running)", true);
    let last = plain.iter().rposition(|row| row.ends_with('a')).unwrap();
    assert_eq!(tagged[..=last], plain[..=last], "{tagged:?}");
    // Same gutter and hang as the message row above it, then the suffix.
    let hang = tagged[last].trim_end_matches('a');
    assert_eq!(tagged[last + 1], format!("{hang}(running)"), "{tagged:?}");
    assert_eq!(tagged[last + 2..], plain[last + 1..], "{tagged:?}");
    assert!(fits(&tagged), "{tagged:?}");
}

fn running(message: &str, suffix: &str) -> LinkedLines {
    let effect: EffectEntry = serde_json::from_value(json!({
        "detail": {"kind": "shell", "display": {"verb": "Running",
            "message": message, "suffix": suffix}},
        "state": {"status": "running"}
    }))
    .unwrap();
    let view = EffectView {
        index: 0,
        rev: 0,
        in_progress: true,
        local: false,
        expanded: false,
        grouped: false,
        stream_delta: None,
    };
    let mut lines = LinkedLines::default();
    push_effect(&mut lines, &effect, view, None, 0, WIDTH, None);
    lines
}

#[test]
fn running_header_wraps_long_lines_under_the_message_column() {
    let lines = running(&format!("set -e\n{COMMAND}"), "");
    let rows: Vec<String> = lines.lines().iter().map(ToString::to_string).collect();
    assert_eq!(rows.len(), 3, "{rows:?}");
    assert!(fits(&rows), "{rows:?}");
    let hang = " ".repeat("■ Running ".chars().count());
    assert!(
        rows[1..].iter().all(|row| row.starts_with(&hang)),
        "{rows:?}"
    );
    // The command's own line opens a row; its wrapped rows fold back into it on copy.
    assert!(
        lines.folds()[1].is_some_and(|fold| fold.breaks),
        "{:?}",
        lines.folds()
    );
    assert!(
        lines.folds()[2].is_some_and(|fold| !fold.breaks),
        "{:?}",
        lines.folds()
    );
}

#[test]
fn running_header_ends_with_its_suffix_after_the_last_word() {
    let lines = running("line one\nline two", "(running)");
    let rows: Vec<String> = lines.lines().iter().map(ToString::to_string).collect();
    assert_eq!(rows.len(), 2, "{rows:?}");
    assert!(rows[0].ends_with("line one"), "{rows:?}");
    assert!(rows[1].ends_with("line two (running)"), "{rows:?}");
    assert!(
        lines.folds()[1].is_some_and(|fold| fold.breaks),
        "{:?}",
        lines.folds()
    );
}

#[test]
fn running_header_wraps_at_the_full_width_before_its_suffix() {
    let plain = running(COMMAND, "");
    let tagged = running(COMMAND, "(running)");
    let plain: Vec<String> = plain.lines().iter().map(ToString::to_string).collect();
    let rows: Vec<String> = tagged.lines().iter().map(ToString::to_string).collect();
    assert!(rows.len() > 1, "{rows:?}");
    assert_eq!(
        rows[..plain.len() - 1],
        plain[..plain.len() - 1],
        "{rows:?}"
    );
    assert!(rows.last().unwrap().ends_with(" (running)"), "{rows:?}");
    assert!(fits(&rows), "{rows:?}");
    // The wrapped rows fold back into the command on copy, with no suffix between them.
    assert!(
        tagged.folds()[1].is_some_and(|fold| !fold.breaks),
        "{:?}",
        tagged.folds()
    );
}
