use std::sync::Arc;

use vibe_rs::app::App;
use vibe_rs::message_queue::{self, QueueEvent, QueueItem, ReplacementOutcome};
use vibe_rs::server::Client;

fn queued(message_id: &str, text: &str) -> QueueItem {
    QueueItem {
        queue_item_id: Some("queue-1".to_string()),
        message_id: message_id.to_string(),
        server_message_id: message_id.to_string(),
        text: text.to_string(),
        images: Vec::new(),
        sent: true,
        ever_sent: true,
        revision: 1,
        replacing: false,
    }
}

#[test]
fn prompt_added_while_a_promoted_replace_settles_keeps_fifo_order() {
    let mut app = App::default();
    let client = Arc::new(Client::stub());
    app.queue.items.push(queued("message-1", "committed"));
    let mut unsent = queued("message-2", "second");
    unsent.server_message_id = "message-1".to_string();
    unsent.sent = false;
    unsent.replacing = true;
    app.queue.items.push(unsent);

    assert!(message_queue::turn_started(&mut app, &client, "queue-1"));
    message_queue::enqueue_prompt(&mut app, &client, "third".to_string());
    assert_eq!(app.queue.items[1].server_message_id, "message-1");

    message_queue::apply_event(
        &mut app,
        &client,
        QueueEvent::GroupReplaced {
            server_message_id: "message-1".to_string(),
            revision: 1,
            covered: vec![("message-2".to_string(), "second".to_string(), Vec::new())],
            delivered: vec!["message-1".to_string(), "message-2".to_string()],
            outcome: ReplacementOutcome::Consumed,
        },
    );

    assert_eq!(
        app.queue
            .items
            .iter()
            .map(|item| item.text.as_str())
            .collect::<Vec<_>>(),
        ["second", "third"]
    );
    assert_ne!(app.queue.items[0].server_message_id, "message-1");
    assert_eq!(
        app.queue.items[1].server_message_id,
        app.queue.items[0].server_message_id
    );
}
