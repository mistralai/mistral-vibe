//! The retry presentation resolves only once active.

use serde_json::{json, Value};
use vibe_rs::app::App;
use vibe_rs::commands::retry;
use vibe_rs::server::EntryHead;

use crate::support::message;

fn message_added(id: &str, role: &str, text: &str) -> Value {
    json!({
        "entry": message(id, role, text)
    })
}

fn notice_added() -> Value {
    json!({"entry": {"id": "n1", "type": "notice", "level": "info", "message": "x"}})
}

fn add(app: &mut App, entry: &Value) {
    app.view.transcript.add(entry);
    retry::track_turn_assistant(app, &EntryHead::read(entry));
}

#[test]
fn an_inactive_offer_survives_non_user_entries_until_the_user_acts() {
    let mut app = App::default();
    add(
        &mut app,
        &message_added("dst", "assistant", "Partial answer."),
    );
    retry::offer(&mut app, Some("err".into()));
    assert!(!app.session.retry_presentation.as_ref().unwrap().active);

    // A non-user entry arrives before the user runs /retry: the offer stands.
    retry::on_entry_added(&mut app, &EntryHead::read(&notice_added()));
    assert!(app.session.retry_presentation.is_some());
    retry::on_entry_added(
        &mut app,
        &EntryHead::read(&message_added("src", "assistant", " Late.")),
    );
    assert!(app.session.retry_presentation.is_some());
    assert!(app.session.retry_continuation.is_none());

    // A user entry cancels the offer; the rows stay mounted.
    retry::on_entry_added(
        &mut app,
        &EntryHead::read(&message_added("next", "user", "next turn")),
    );
    assert!(app.session.retry_presentation.is_none());
    assert!(!app.session.can_retry);
    assert!(app.view.transcript.contains("dst"));
}

#[test]
fn an_active_presentation_resolves_on_the_retried_turns_first_entry() {
    let mut app = App::default();
    add(
        &mut app,
        &message_added("dst", "assistant", "Partial answer."),
    );
    app.view
        .transcript
        .add(&message_added("err", "command_error", "Error: failed"));
    retry::offer(&mut app, Some("err".into()));
    retry::begin(&mut app);
    assert!(app.session.retry_presentation.as_ref().unwrap().active);

    retry::on_entry_added(
        &mut app,
        &EntryHead::read(&message_added("src", "assistant", " Continued.")),
    );
    assert!(app.session.retry_presentation.is_none());
    // The transient row vanishes; the continuation merges into the answer.
    assert!(!app.view.transcript.contains("err"));
    let continuation = app.session.retry_continuation.as_ref().unwrap();
    assert_eq!(continuation.dst_id, "dst");
    assert_eq!(continuation.src_id, "src");
}

#[test]
fn a_second_offer_keeps_the_captured_assistant_and_appends_the_new_error() {
    let mut app = App::default();
    add(
        &mut app,
        &message_added("dst", "assistant", "Partial answer."),
    );
    app.view
        .transcript
        .add(&message_added("err1", "command_error", "Error: failed"));
    retry::offer(&mut app, Some("err1".into()));
    retry::begin(&mut app);

    // The retried turn fails before any entry lands: the original answer and
    // the earlier rows must stay on the presentation.
    app.view.transcript.add(&message_added(
        "err2",
        "command_error",
        "Error: failed again",
    ));
    retry::offer(&mut app, Some("err2".into()));
    let presentation = app.session.retry_presentation.as_ref().unwrap();
    assert!(!presentation.active);
    assert_eq!(presentation.assistant_id.as_deref(), Some("dst"));
    assert_eq!(
        presentation.transient_ids,
        ["err1", "err2"].map(str::to_owned)
    );

    retry::begin(&mut app);
    retry::on_entry_added(
        &mut app,
        &EntryHead::read(&message_added("src", "assistant", " Continued.")),
    );
    assert!(app.session.retry_presentation.is_none());
    assert!(!app.view.transcript.contains("err1"));
    assert!(!app.view.transcript.contains("err2"));
    let continuation = app.session.retry_continuation.as_ref().unwrap();
    assert_eq!(continuation.dst_id, "dst");
    assert_eq!(continuation.src_id, "src");
}

#[test]
fn an_offer_after_a_consumed_presentation_still_knows_the_answer() {
    let mut app = App::default();
    add(
        &mut app,
        &message_added("dst", "assistant", "Partial answer."),
    );
    app.view
        .transcript
        .add(&message_added("err", "command_error", "Error: failed"));
    retry::offer(&mut app, Some("err".into()));
    retry::begin(&mut app);

    // A non-assistant entry consumes the presentation without continuing the
    // answer; the retried turn then fails again with no assistant entry.
    retry::on_entry_added(&mut app, &EntryHead::read(&notice_added()));
    assert!(app.session.retry_presentation.is_none());
    app.view.transcript.add(&message_added(
        "err2",
        "command_error",
        "Error: failed again",
    ));
    retry::offer(&mut app, Some("err2".into()));
    let presentation = app.session.retry_presentation.as_ref().unwrap();
    assert_eq!(presentation.assistant_id.as_deref(), Some("dst"));

    retry::begin(&mut app);
    retry::on_entry_added(
        &mut app,
        &EntryHead::read(&message_added("src", "assistant", " Continued.")),
    );
    let continuation = app.session.retry_continuation.as_ref().unwrap();
    assert_eq!(continuation.dst_id, "dst");
    assert_eq!(continuation.src_id, "src");
}
