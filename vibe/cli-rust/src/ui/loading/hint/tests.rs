use std::time::Instant;

use serde_json::json;

use super::{push, running, starting, CANCEL_LAST_QUEUED};
use crate::app::{App, Status};
use crate::message_queue::QueueItem;

fn busy_app(status: Status) -> App {
    let mut app = App::default();
    app.session.session_id = Some("sess".into());
    app.session.active_turn_id = Some("turn".into());
    app.session.status = status;
    app.queue.items = vec![QueueItem {
        queue_item_id: Some("q-a".into()),
        message_id: "a".into(),
        server_message_id: "a".into(),
        text: "a".into(),
        images: Vec::new(),
        mentions: None,
        sent: true,
        ever_sent: true,
        revision: 0,
        replacing: false,
    }];
    app.view.transcript.add(&json!({
        "entry": {
            "id": "a",
            "type": "message",
            "role": "user",
            "content": [{"type": "text", "text": "a"}],
            "generationStatus": "completed",
            "local": true,
            "pending": true
        }
    }));
    app
}

#[test]
fn composer_queue_hints_need_the_composer_to_own_the_keys() {
    let mut app = busy_app(Status::Generating {
        since: Instant::now(),
    });
    assert_eq!(
        running(&app),
        [("Esc", "interrupt"), ("Enter", "steer"), CANCEL_LAST_QUEUED]
    );

    app.subagents.list.focused = true;
    assert_eq!(
        running(&app),
        [("Esc", "interrupt")],
        "the list takes Enter"
    );
}

#[test]
fn a_picker_opened_during_a_turn_hides_the_interrupt_hints() {
    let mut app = busy_app(Status::Generating {
        since: Instant::now(),
    });
    app.vibe_code_project.pending = true;
    assert!(running(&app).is_empty(), "Esc cancels the opening picker");
    app.question_app.open = true;
    assert_eq!(
        running(&app),
        [("Esc", "interrupt")],
        "a question keeps them"
    );
    app.question_app.open = false;
    app.vibe_code_project.pending = false;
    app.theme_picker.open = true;
    assert!(running(&app).is_empty());

    app.approval.open = true;
    assert_eq!(
        running(&app),
        [("Esc", "interrupt")],
        "a callback keeps them"
    );
}

#[test]
fn startup_cancellation_needs_the_composer_to_own_the_keys() {
    let mut app = busy_app(Status::Starting);
    assert_eq!(starting(&app), [CANCEL_LAST_QUEUED]);

    app.model_picker.open = true;
    assert!(starting(&app).is_empty(), "the picker owns the keys");
}

#[test]
fn the_elapsed_time_stays_when_no_hint_applies() {
    let text = |prefix: &str, hints: &[crate::hints::Hint]| {
        let mut spans = Vec::new();
        push(&mut spans, prefix.to_owned(), hints);
        spans
            .iter()
            .map(|span| span.content.as_ref())
            .collect::<String>()
    };

    assert_eq!(text("12s ", &[]), "(12s)");
    assert_eq!(text("", &[]), "");
    assert_eq!(
        text("12s ", &[("Esc", "interrupt")]),
        "(12s Esc to interrupt)"
    );
}
