//! The inline notice: refusals stay until the next key, confirmations fade (VIBE-4099).

use vibe_rs::app::{App, Severity};
use vibe_rs::ui::notice;

use crate::feedback_support::notice_text;
use crate::support::{redraw, rows};

#[test]
fn warning_notice_keeps_warning_severity() {
    let mut app = App::default();

    notice::show_sticky(&mut app, Severity::Warning, "already processed");

    assert!(matches!(
        app.overlays.notice.as_ref().map(|notice| notice.severity),
        Some(Severity::Warning)
    ));
    assert!(app.overlays.notice.as_ref().unwrap().until.is_none());
}

#[test]
fn an_expired_confirmation_is_not_drawn() {
    let mut app = App::default();
    notice::show(&mut app, Severity::Information, "Copied to clipboard", 0);

    let screen = rows(&redraw(&mut app)).join("\n");

    assert!(!screen.contains("Copied to clipboard"));
    assert_eq!(notice_text(&app), None);
}

#[test]
fn a_failure_while_a_subagent_is_viewed_also_shows_a_notice() {
    let mut app = App::default();
    app.subagents.viewed_subagent_id = Some("child".into());

    vibe_rs::notices::failed(&mut app, "Failed to switch agent: broken");

    let notice = app.overlays.notice.as_ref().expect("notice");
    assert_eq!(notice.severity, Severity::Error);
    assert!(notice.until.is_none(), "stays until the next key");
}

#[test]
fn a_failure_on_the_main_conversation_shows_only_its_row() {
    let mut app = App::default();

    vibe_rs::notices::failed(&mut app, "Failed to switch agent: broken");

    assert_eq!(notice_text(&app), None);
}

#[test]
fn a_long_notice_ends_with_an_ellipsis() {
    let mut app = App::default();
    notice::show_sticky(&mut app, Severity::Warning, &"word ".repeat(30));

    let screen = rows(&redraw(&mut app)).join("\n");

    assert!(screen.contains("word…"));
}

#[test]
fn a_refusal_is_drawn_above_the_input() {
    let mut app = App::default();
    notice::show_sticky(
        &mut app,
        Severity::Warning,
        "Slash commands cannot be queued",
    );

    let screen = rows(&redraw(&mut app)).join("\n");

    assert!(screen.contains("Slash commands cannot be queued"));
}

#[test]
fn painting_a_refusal_marks_it_seen() {
    let mut app = App::default();
    notice::show_sticky(&mut app, Severity::Error, "Image is too large");
    assert!(!app.overlays.notice.as_ref().unwrap().shown());

    redraw(&mut app);

    assert!(app.overlays.notice.as_ref().unwrap().shown());
}

#[test]
fn a_key_cannot_hide_a_failure_before_its_hold_has_passed() {
    let mut app = App::default();
    notice::show_failure(&mut app, Severity::Error, "Failed to switch agent");
    redraw(&mut app);

    notice::answered(&mut app);
    assert_eq!(notice_text(&app), Some("Failed to switch agent"));

    let hold = notice::FAILURE_HOLD;
    let painted = app.overlays.notice.as_mut().unwrap();
    painted.shown_at = painted.shown_at.and_then(|at| at.checked_sub(hold));
    notice::answered(&mut app);
    assert_eq!(notice_text(&app), None);
}

#[test]
fn release_notes_over_a_notice_keep_it_unseen() {
    let mut app = App::default();
    vibe_rs::release_notes::open(&mut app);
    notice::show_failure(&mut app, Severity::Error, "Voice transcription failed");

    redraw(&mut app);

    assert!(!app.overlays.notice.as_ref().unwrap().shown());
}
