use super::{push_command_error, push_command_status};
use crate::ui::markdown::LinkedLines;
use crate::ui::theme;

fn texts(lines: &LinkedLines) -> Vec<String> {
    lines
        .lines()
        .iter()
        .map(|line| {
            line.spans
                .iter()
                .map(|span| span.content.as_ref())
                .collect()
        })
        .collect()
}

#[test]
fn a_status_row_marks_its_first_line_and_aligns_the_rest_under_it() {
    let mut lines = LinkedLines::default();
    let text = "Plugins reloaded.\n\n- `+` `review` 1.0.0\n- `-` `legacy`";
    push_command_status(&mut lines, text, 80, "✓", theme::status_ready());
    let rows = texts(&lines);
    assert!(rows[0].trim().is_empty(), "a gap opens the row");
    assert_eq!(rows[1], "✓ Plugins reloaded.");
    assert!(rows[2].starts_with("  • + review"), "{rows:?}");
    assert!(rows[3].starts_with("  • - legacy"), "{rows:?}");
    let marker = &lines.lines()[1].spans[0];
    assert_eq!(marker.style.fg, Some(theme::status_ready()));
}

#[test]
fn a_wrapped_status_line_hangs_under_the_text() {
    let mut lines = LinkedLines::default();
    push_command_status(&mut lines, &"word ".repeat(10), 20, "⚠", theme::warning());
    let rows = texts(&lines);
    assert!(rows[1].starts_with("⚠ word"));
    assert!(rows[2].starts_with("  word"), "{rows:?}");
    assert_eq!(lines.folds()[2].map(|fold| fold.hang), Some(2));
}

#[test]
fn an_error_row_is_sanitized_and_only_its_marker_is_colored() {
    let mut lines = LinkedLines::default();
    push_command_error(&mut lines, "\x1b[31mboom\x1b[0m\nsecond line", 80);
    let rows = texts(&lines);
    assert_eq!(rows[1], "✕ Error: boom");
    assert_eq!(rows[2], "  second line");
    let spans = &lines.lines()[1].spans;
    assert_eq!(spans[0].style.fg, Some(theme::error()));
    assert_eq!(spans[1].style.fg, Some(theme::foreground()));
}
