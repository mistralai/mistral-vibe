//! A late enqueue answer keeps the feedback check on the session the prompt was submitted to.

use std::sync::Arc;

use vibe_rs::app::App;
use vibe_rs::message_queue::{self, QueueEvent, QueueItem};
use vibe_rs::server::Client;

const SUBMITTED: &str = "00000000-0000-4000-8000-000000000001";
const COMPACTED: &str = "00000000-0000-4000-8000-000000000002";

#[test]
fn a_late_accepted_event_reports_the_submit_session_not_the_current_one() {
    let mut app = App::default();
    app.queue.items.push(QueueItem {
        queue_item_id: None,
        message_id: "message-1".to_owned(),
        server_message_id: "message-1".to_owned(),
        text: "hi".to_owned(),
        images: Vec::new(),
        sent: true,
        ever_sent: true,
        revision: 1,
        replacing: false,
    });
    let client = Arc::new(Client::stub());

    // The turn started before the enqueue answer landed, and a mid-turn
    // `session/compacted` handoff has already replaced the current session.
    assert!(!message_queue::turn_started(&mut app, &client, "queue-1"));
    app.session.session_id = Some(COMPACTED.to_owned());

    let session = message_queue::apply_event(
        &mut app,
        &client,
        QueueEvent::Accepted {
            message_id: "message-1".to_owned(),
            queue_item_id: "queue-1".to_owned(),
            session_id: SUBMITTED.to_owned(),
            images: Vec::new(),
        },
    );

    assert_eq!(session.as_deref(), Some(SUBMITTED));
    assert!(app.queue.items.is_empty());
}
