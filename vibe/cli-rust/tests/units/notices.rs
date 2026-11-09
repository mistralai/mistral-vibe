//! Startup warnings share one framed box until the first turn, then each mounts as a row (VIBE-4099).

use serde_json::{json, Value};
use vibe_rs::app::{App, Status};
use vibe_rs::notices;
use vibe_rs::server::{HistoryEntry, MessageContent};

/// Every message entry as `(role, text)`, in transcript order.
fn messages(app: &App) -> Vec<(String, String)> {
    app.view
        .transcript
        .lines()
        .filter_map(|entry| match entry.entry {
            HistoryEntry::Message(message) => Some((
                message.role.clone(),
                message
                    .content
                    .iter()
                    .filter_map(|block| match block {
                        MessageContent::Text { text } => Some(text.as_str()),
                        _ => None,
                    })
                    .collect::<Vec<_>>()
                    .join("\n"),
            )),
            _ => None,
        })
        .collect()
}

fn runtime(issues: Value, warnings: &[&str]) -> Value {
    json!({"runtime": {"issues": issues, "config": {"validationWarnings": warnings}}})
}

#[test]
fn startup_issues_and_warnings_share_one_box() {
    let mut app = App::default();
    let issues = json!([{"file": "/proj/.vibe/skills/broken/SKILL.md", "message": "Failed to load: bad yaml"}]);

    notices::show_config_issues(
        &mut app,
        &runtime(issues, &["Model 'toto' is not available."]),
    );

    assert_eq!(
        messages(&app),
        vec![(
            "startup_notices".to_owned(),
            "- \\/proj\\/\\.vibe\\/skills\\/broken\\/SKILL\\.md\\: Failed to load\\: bad yaml\n- Model \\'toto\\' is not available\\.".to_owned()
        )]
    );
    assert!(app.overlays.notice.is_none());
}

#[test]
fn a_warning_before_the_first_turn_joins_the_box() {
    let mut app = App::default();
    notices::show_config_issues(&mut app, &runtime(json!([]), &["first"]));

    notices::warn(&mut app, "MCP server 'github' failed to connect: timeout.");
    notices::warn(&mut app, "MCP server 'github' failed to connect: timeout.");

    assert_eq!(
        messages(&app),
        vec![(
            "startup_notices".to_owned(),
            "- first\n- MCP server \\'github\\' failed to connect\\: timeout\\.".to_owned()
        )]
    );
}

#[test]
fn after_the_first_turn_each_warning_is_its_own_row() {
    let mut app = App::default();
    notices::warn(&mut app, "startup");
    notices::close_startup(&mut app);

    notices::warn(&mut app, "Request too long: compacting.");
    notices::error(&mut app, "Scheduled loop failed: boom");

    let roles: Vec<String> = messages(&app).into_iter().map(|(role, _)| role).collect();
    assert_eq!(
        roles,
        vec!["startup_notices", "notice_warning", "notice_error"]
    );
}

#[test]
fn a_cleared_box_mounts_again_with_every_warning() {
    let mut app = App::default();
    notices::warn(&mut app, "first");
    app.view.transcript.clear();

    notices::warn(&mut app, "second");

    assert_eq!(
        messages(&app),
        vec![("startup_notices".to_owned(), "- first\n- second".to_owned())]
    );
}

#[test]
fn a_restored_transcript_gets_the_box_back_on_sync() {
    let mut app = App::default();
    let before = app.view.transcript.snapshot();
    notices::warn(&mut app, "first");
    app.view.transcript.restore(before);
    assert!(messages(&app).is_empty());

    notices::sync(&mut app);

    assert_eq!(
        messages(&app),
        vec![("startup_notices".to_owned(), "- first".to_owned())]
    );
}

#[test]
fn messages_are_sanitized_and_their_markdown_escaped() {
    let mut app = App::default();

    notices::warn(&mut app, "\u{1b}[31mbad\u{1b}[0m `code` *star*");
    notices::close_startup(&mut app);
    notices::error(&mut app, "\u{1b}[1mboom\u{1b}[0m");

    assert_eq!(
        messages(&app),
        vec![
            (
                "startup_notices".to_owned(),
                "- bad \\`code\\` \\*star\\*".to_owned()
            ),
            ("notice_error".to_owned(), "boom".to_owned()),
        ]
    );
}

#[test]
fn a_clear_drops_the_startup_warnings() {
    let mut app = App::default();
    notices::warn(&mut app, "before clear");

    vibe_rs::commands::clear::apply_cleared(&mut app, "s2".to_owned(), None, Vec::new());
    notices::warn(&mut app, "after clear");

    assert_eq!(
        messages(&app),
        vec![("startup_notices".to_owned(), "- after clear".to_owned())]
    );
}

#[test]
fn a_clear_after_a_turn_reopens_the_startup_box() {
    let mut app = App::default();
    notices::close_startup(&mut app);

    vibe_rs::commands::clear::apply_cleared(&mut app, "s2".to_owned(), None, Vec::new());
    notices::warn(&mut app, "after clear");

    assert_eq!(
        messages(&app),
        vec![("startup_notices".to_owned(), "- after clear".to_owned())]
    );
}

#[test]
fn an_error_never_joins_the_startup_box() {
    let mut app = App::default();

    notices::error(
        &mut app,
        "Your administrator-managed configuration could not be applied",
    );

    assert_eq!(
        messages(&app),
        vec![(
            "notice_error".to_owned(),
            "Your administrator-managed configuration could not be applied".to_owned()
        )]
    );
}

#[test]
fn nothing_to_report_mounts_nothing() {
    let mut app = App::default();

    notices::show_config_issues(&mut app, &runtime(json!([]), &[]));
    notices::warn(&mut app, "   ");

    assert!(messages(&app).is_empty());
}

#[test]
fn updated_warning_is_held_until_ready_then_shows_once() {
    let mut app = App::default();
    let runtime = runtime(json!([]), &["Active model 'toto' is not configured"]);

    notices::show_updated_config_warnings(&mut app, &runtime);
    assert!(messages(&app).is_empty());

    app.set_status(Status::Ready);
    notices::show_updated_config_warnings(&mut app, &runtime);
    notices::show_updated_config_warnings(&mut app, &runtime);
    assert_eq!(messages(&app).len(), 1);
}

#[test]
fn updated_warning_shows_while_generating() {
    let mut app = App::default();
    app.set_status(Status::Generating {
        since: vibe_rs::clock::now(),
    });

    notices::show_updated_config_warnings(&mut app, &runtime(json!([]), &["late warning"]));

    assert_eq!(messages(&app).len(), 1);
}

#[test]
fn startup_warnings_are_not_repeated_by_a_later_update() {
    let mut app = App::default();
    app.set_status(Status::Ready);
    let runtime = runtime(json!([]), &["Active model 'toto' is not configured"]);

    notices::show_config_issues(&mut app, &runtime);
    notices::close_startup(&mut app);
    notices::show_updated_config_warnings(&mut app, &runtime);

    assert_eq!(messages(&app).len(), 1);
}

#[test]
fn a_warning_that_disappears_can_show_again() {
    let mut app = App::default();
    app.set_status(Status::Ready);
    notices::close_startup(&mut app);

    notices::show_updated_config_warnings(&mut app, &runtime(json!([]), &["toto"]));
    notices::show_updated_config_warnings(&mut app, &runtime(json!([]), &[]));
    notices::show_updated_config_warnings(&mut app, &runtime(json!([]), &["toto"]));

    assert_eq!(messages(&app).len(), 2);
}

#[test]
fn updated_warning_is_held_when_failed() {
    let mut app = App::default();
    app.set_status(Status::Failed);

    notices::show_updated_config_warnings(&mut app, &runtime(json!([]), &["toto"]));

    assert!(messages(&app).is_empty());
}
