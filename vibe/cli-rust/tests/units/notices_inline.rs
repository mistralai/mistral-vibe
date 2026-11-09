//! Failures from pickers and key actions: a flat inline notice, never a conversation row, except under `/config`.

use vibe_rs::app::{App, Severity};
use vibe_rs::feedback::Message;
use vibe_rs::notices;
use vibe_rs::ui::notice;

use crate::support::{messages, redraw, rows};

#[test]
fn an_inline_failure_is_a_flat_notice_and_no_row() {
    let mut app = App::default();

    notices::inline(
        &mut app,
        Severity::Error,
        "\u{1b}[31mFailed\u{1b}[0m:\nboom",
    );

    let notice = app.overlays.notice.as_ref().expect("notice");
    assert_eq!(notice.text, "Failed: boom");
    assert!(messages(&app).is_empty());
}

#[test]
fn an_inline_failure_under_config_keeps_its_row_and_status_line() {
    let mut app = App::default();
    app.config_screen.open = true;

    notices::inline(&mut app, Severity::Error, "Failed to save");

    assert!(app.overlays.notice.is_none());
    assert_eq!(
        messages(&app),
        vec![("command_error".to_owned(), "Failed to save".to_owned())]
    );
    assert!(app.config_screen.status.is_some());
}

const NEWER: &str = "This session was created by a newer version of Vibe. Restart with --check-upgrade to update Vibe, then resume this session.";

#[test]
fn a_long_failure_wraps_whole_beside_the_feedback_prompt() {
    let mut app = App::default();
    app.feedback.message = Message::Prompt;
    notices::inline(&mut app, Severity::Error, NEWER);

    let screen = rows(&redraw(&mut app)).join(" ");

    assert!(screen.contains("--check-upgrade"));
    assert!(screen.contains("resume this session."));
}

#[test]
fn a_huge_failure_takes_at_most_three_rows() {
    let mut app = App::default();
    notices::inline(&mut app, Severity::Error, &"boom ".repeat(200));

    let screen = rows(&redraw(&mut app));

    let boom_rows = screen.iter().filter(|row| row.contains("boom")).count();
    assert_eq!(boom_rows, notice::MAX_ROWS);
    assert!(screen.iter().any(|row| row.trim_end().ends_with('…')));
}

#[test]
fn a_squeezed_failure_block_still_ends_in_an_ellipsis() {
    let mut app = App::default();
    notices::inline(&mut app, Severity::Error, &"boom ".repeat(200));

    let screen = rows(&crate::support::render(40, 11, |frame| app.draw(frame)));

    let boom: Vec<&String> = screen.iter().filter(|row| row.contains("boom")).collect();
    assert_eq!(boom.len(), 2, "the squeezed block keeps two rows");
    assert!(boom[1].trim_end().ends_with('…'));
}
