//! Feedback rows are repeated only where the conversation is hidden, and a repeated refusal stays one row.

use serde_json::json;
use vibe_rs::app::App;
use vibe_rs::notices;

use crate::feedback_support::{notice_text, warnings};

#[test]
fn a_repeated_refusal_stays_one_row() {
    let mut app = App::default();

    notices::refused(&mut app, "This session is open in another Vibe process.");
    notices::refused(&mut app, "This session is open in another Vibe process.");

    assert_eq!(warnings(&app).len(), 1);
}

#[test]
fn a_repeated_refusal_stays_one_row_below_a_queue_and_the_banner() {
    let mut app = App::default();
    for (id, role, extra) in [
        ("queued", "user", "pending"),
        ("banner", "whats_new", "pinnedBottom"),
    ] {
        let mut entry = json!({"id": id, "type": "message", "role": role,
            "content": [{"type": "text", "text": id}], "generationStatus": "completed", "local": true});
        entry[extra] = json!(true);
        app.view.transcript.add(&json!({"entry": entry}));
    }

    notices::refused(&mut app, "Wait for the current turn to finish.");
    notices::refused(&mut app, "Wait for the current turn to finish.");

    assert_eq!(warnings(&app).len(), 1);
}

#[test]
fn a_hidden_server_row_below_a_refusal_does_not_split_it() {
    let mut app = App::default();
    notices::refused(&mut app, "Wait for the current turn to finish.");
    let server = json!({"id": "server", "type": "message", "role": "assistant",
        "content": [{"type": "text", "text": "dropped"}], "generationStatus": "completed"});
    app.view.transcript.add(&json!({ "entry": server }));
    app.view.transcript.dismiss("server");

    notices::refused(&mut app, "Wait for the current turn to finish.");

    assert_eq!(
        warnings(&app).len(),
        1,
        "the hidden row is not the last shown one"
    );
}

#[test]
fn a_failure_leaves_the_release_notes_open() {
    let mut app = App::default();
    vibe_rs::release_notes::open(&mut app);

    notices::failed(&mut app, "Failed to switch agent: broken");

    assert!(app.release_notes.open, "focus stays where the user types");
}

#[test]
fn an_approval_over_a_viewed_subagent_shows_the_row_only() {
    let mut app = App::default();
    app.subagents.viewed_subagent_id = Some("child".into());
    app.approval.open = true;

    notices::failed(&mut app, "Failed to answer approval: closed");

    assert_eq!(notice_text(&app), None);
}
