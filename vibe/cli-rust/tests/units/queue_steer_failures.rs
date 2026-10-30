//! A queue steer that fails or lands after the turn ended keeps the queued prompts.

use std::sync::Arc;
use std::time::Instant;

use serde_json::json;
use tokio::sync::mpsc;
use vibe_rs::app::{App, Status};
use vibe_rs::commands::submission;
use vibe_rs::message_queue::{self as mq, QueueEvent, QueueItem};
use vibe_rs::server::{Client, PublicSessionState};

use crate::support::{queued, queued_texts, stub};

/// Two busy-time prompts merged into one accepted server queue item `q-1`.
fn busy_app_with_merged_queue() -> App {
    let mut app = App::default();
    app.session.session_id = Some("sess".into());
    app.session.active_turn_id = Some("turn".into());
    app.session.status = Status::Generating {
        since: Instant::now(),
    };
    for (id, text) in [("first", "first queued"), ("second", "second queued")] {
        app.view.transcript.add(&json!({
            "entry": {
                "id": id,
                "type": "message",
                "role": "user",
                "content": [{"type": "text", "text": text}],
                "generationStatus": "completed",
                "local": true,
                "pending": true
            }
        }));
        app.queue.items.push(QueueItem {
            queue_item_id: Some("q-1".into()),
            server_message_id: "first".into(),
            ..queued(id, text)
        });
    }
    app
}

fn press_empty_enter(app: &mut App, client: &Arc<Client>) -> bool {
    let (config_tx, _config_rx) = mpsc::channel(1);
    app.chat_input.clear();
    submission::submit(app, client, &config_tx)
}

fn assert_still_queued_and_removable(app: &App) {
    assert_eq!(queued_texts(app), ["first queued", "second queued"]);
    assert!(app.view.transcript.is_pending("first"));
    assert!(app.view.transcript.is_pending("second"));
    assert!(!mq::mutation_in_flight(app), "no steer stays in flight");
    assert!(mq::has_removable(app), "Ctrl+C can still remove a prompt");
}

#[tokio::test]
async fn a_steer_rejected_because_the_turn_ended_keeps_the_queue_for_the_next_turn() {
    let client = stub();
    let mut app = busy_app_with_merged_queue();
    let entries = app.view.transcript.lines().count();

    assert!(!press_empty_enter(&mut app, &client));
    assert!(mq::mutation_in_flight(&app), "the steer is in flight");
    let rejected = QueueEvent::SteerSettled {
        queue_item_id: "q-1".into(),
        accepted: Some(false),
    };
    mq::apply_event(&mut app, &client, rejected);

    assert_still_queued_and_removable(&app);
    assert!(mq::can_steer(&app), "a later Enter may steer again");
    assert_eq!(app.view.transcript.lines().count(), entries, "no error row");
    assert!(app.overlays.toasts.is_empty(), "no error toast");

    assert!(mq::turn_started(&mut app, &client, "q-1"));
    assert!(app.queue.is_empty(), "the block runs as the next turn");
}

#[tokio::test]
async fn a_steer_lost_to_a_disconnect_is_reconciled_from_the_snapshot() {
    let client = stub();
    let mut app = busy_app_with_merged_queue();

    assert!(!press_empty_enter(&mut app, &client));
    let lost = QueueEvent::SteerSettled {
        queue_item_id: "q-1".into(),
        accepted: None,
    };
    mq::apply_event(&mut app, &client, lost);
    assert!(
        mq::mutation_in_flight(&app),
        "the outcome is unknown until resync"
    );

    let state = serde_json::from_value::<PublicSessionState>(json!({
        "eventId": 9,
        "session": {"id": "sess"},
        "history": [],
        "turns": [],
        "turnQueue": {"paused": false, "items": [{
            "id": "q-1",
            "entries": [{
                "role": "user",
                "entryId": "first",
                "content": [{"type": "text", "text": "first queued"}]
            }]
        }]}
    }))
    .expect("state");
    mq::reconcile_snapshot(&mut app, &client, &state);

    assert_still_queued_and_removable(&app);
    assert!(
        !press_empty_enter(&mut app, &client),
        "submit keeps working"
    );
    assert!(mq::mutation_in_flight(&app), "the next Enter steers again");
}
