//! Queue snapshots retain images and adopt in-flight prompts by message identity.

use std::sync::Arc;

use vibe_rs::app::App;
use vibe_rs::message_queue::{self, QueueItem, ReplacementOutcome};
use vibe_rs::server::{Client, ImageAttachment, ImageSource, PreparedPrompt};

fn queued_turn() -> serde_json::Value {
    serde_json::json!({
        "items": [{
            "id": "queue-1",
            "entries": [{
                "annotations": {},
                "content": [
                    {"type": "text", "text": "inspect"},
                    {
                        "type": "image",
                        "uri": "file:///tmp/diagram.png",
                        "mediaType": "image/png",
                        "altText": "diagram.png"
                    }
                ],
                "entryId": "message-1",
                "role": "user"
            }]
        }],
        "paused": false
    })
}

#[test]
fn restores_images_from_a_server_queue_snapshot() {
    let mut app = App::default();

    message_queue::sync(&mut app, &queued_turn());

    assert_eq!(app.queue.items.len(), 1);
    assert_eq!(app.queue.items[0].images.len(), 1);
    assert_eq!(app.queue.items[0].images[0].alias, "diagram.png");
    assert!(matches!(
        app.queue.items[0].images[0].source,
        ImageSource::File { .. }
    ));
}

#[test]
fn snapshot_adopts_the_matching_in_flight_prompt_without_duplication() {
    let mut app = App::default();
    app.queue.items.push(QueueItem {
        queue_item_id: None,
        message_id: "message-1".to_owned(),
        server_message_id: "message-1".to_owned(),
        text: "inspect".to_owned(),
        images: Vec::new(),
        sent: true,
        ever_sent: true,
        revision: 1,
        replacing: false,
    });

    let mut snapshot = queued_turn();
    snapshot["items"][0]["entries"][0]["content"][0]["text"] = serde_json::json!("prepared");
    message_queue::sync(&mut app, &snapshot);

    assert_eq!(app.queue.items.len(), 1);
    assert_eq!(app.queue.items[0].queue_item_id.as_deref(), Some("queue-1"));
    assert_eq!(app.queue.items[0].text, "inspect");
    assert_eq!(app.queue.items[0].images.len(), 1);
}

#[test]
fn accepted_replacement_commits_the_new_text_and_images() {
    let mut app = App::default();
    message_queue::sync(&mut app, &queued_turn());
    let images = app.queue.items[0].images.clone();
    app.queue.items[0].text = "inspect closely".to_owned();
    app.queue.items[0].sent = false;
    app.queue.items[0].replacing = true;
    let client = Arc::new(Client::stub());

    message_queue::apply_event(
        &mut app,
        &client,
        message_queue::QueueEvent::GroupReplaced {
            server_message_id: "message-1".to_owned(),
            revision: 1,
            covered: vec![("message-1".to_owned(), "inspect closely".to_owned(), images)],
            delivered: vec!["message-1".to_owned()],
            outcome: ReplacementOutcome::Replaced,
        },
    );

    assert_eq!(app.queue.items[0].text, "inspect closely");
    assert_eq!(app.queue.items[0].images.len(), 1);
    assert!(!app.queue.items[0].replacing);
}

#[test]
fn merged_replacement_restores_images_to_their_visible_prompts() {
    let mut app = App::default();
    for (message_id, path) in [
        ("message-1", "/tmp/first.png"),
        ("message-2", "/tmp/second.png"),
    ] {
        app.queue.items.push(QueueItem {
            queue_item_id: Some("queue-1".to_owned()),
            message_id: message_id.to_owned(),
            server_message_id: "message-1".to_owned(),
            text: format!("@{path} inspect"),
            images: Vec::new(),
            sent: false,
            ever_sent: false,
            revision: 1,
            replacing: true,
        });
    }
    let images: Vec<ImageAttachment> = ["first.png", "second.png"]
        .into_iter()
        .map(|alias| ImageAttachment {
            source: ImageSource::File {
                path: format!("/session/{alias}"),
            },
            alias: format!("/tmp/{alias}"),
            mime_type: "image/png".to_owned(),
        })
        .collect();
    let client = Arc::new(Client::stub());

    message_queue::apply_event(
        &mut app,
        &client,
        message_queue::QueueEvent::GroupReplaced {
            server_message_id: "message-1".to_owned(),
            revision: 1,
            covered: vec![
                (
                    "message-1".to_owned(),
                    "@/tmp/first.png inspect".to_owned(),
                    vec![images[0].clone()],
                ),
                (
                    "message-2".to_owned(),
                    "@/tmp/second.png inspect".to_owned(),
                    vec![images[1].clone()],
                ),
            ],
            delivered: vec!["message-1".to_owned(), "message-2".to_owned()],
            outcome: ReplacementOutcome::Replaced,
        },
    );

    assert_eq!(app.queue.items[0].images[0].alias, "/tmp/first.png");
    assert_eq!(app.queue.items[1].images[0].alias, "/tmp/second.png");
    assert!(app.queue.items.iter().all(|item| item.sent));
    assert!(app.queue.items.iter().all(|item| !item.replacing));
}

#[test]
fn a_prompt_cannot_be_edited_while_its_replacement_is_pending() {
    let mut app = App::default();
    message_queue::sync(&mut app, &queued_turn());
    app.queue.selected = Some("message-1".to_owned());
    app.queue.items[0].replacing = true;

    message_queue::edit_selected(&mut app);

    assert!(!app.queue.editing);
    assert!(app.chat_input.input.is_empty());
}

#[test]
fn a_failed_replacement_allows_the_prompt_to_be_edited_again() {
    let mut app = App::default();
    message_queue::sync(&mut app, &queued_turn());
    app.queue.selected = Some("message-1".to_owned());
    app.queue.items[0].replacing = true;
    let client = Arc::new(Client::stub());

    message_queue::apply_event(
        &mut app,
        &client,
        message_queue::QueueEvent::GroupReplaced {
            server_message_id: "message-1".to_owned(),
            revision: 1,
            covered: Vec::new(),
            delivered: vec!["message-1".to_owned()],
            outcome: ReplacementOutcome::Failed,
        },
    );
    message_queue::edit_selected(&mut app);

    assert!(app.queue.editing);
    assert_eq!(app.chat_input.input, "@/tmp/diagram.png inspect");
}

#[test]
fn editing_a_snapshot_prompt_restores_file_mentions() {
    let mut app = App::default();
    message_queue::sync(&mut app, &queued_turn());
    app.queue.selected = Some("message-1".to_owned());

    message_queue::edit_selected(&mut app);

    assert_eq!(app.chat_input.input, "@/tmp/diagram.png inspect");
}

#[test]
fn editing_does_not_duplicate_an_existing_image_mention() {
    let mut app = App::default();
    message_queue::sync(&mut app, &queued_turn());
    app.queue.items[0].text = "@diagram.png inspect".to_owned();
    app.queue.selected = Some("message-1".to_owned());

    message_queue::edit_selected(&mut app);

    assert_eq!(app.chat_input.input, "@diagram.png inspect");
}

#[test]
fn replacement_keeps_mentioned_and_inline_images_without_duplicates() {
    let file = ImageAttachment {
        source: ImageSource::File {
            path: "/original/diagram.png".to_owned(),
        },
        alias: "diagram.png".to_owned(),
        mime_type: "image/png".to_owned(),
    };
    let inline = ImageAttachment {
        source: ImageSource::Inline {
            data: "aGVsbG8=".to_owned(),
        },
        alias: "clipboard.png".to_owned(),
        mime_type: "image/png".to_owned(),
    };
    let mut prepared = PreparedPrompt::from_text("inspect".to_owned());
    prepared.images.push(ImageAttachment {
        source: ImageSource::File {
            path: "/session/attachments/snapshot.png".to_owned(),
        },
        alias: "/original/diagram.png".to_owned(),
        mime_type: "image/png".to_owned(),
    });

    message_queue::merge_edit_images(
        &mut prepared,
        &[file, inline],
        "@/original/diagram.png inspect",
    );

    assert_eq!(prepared.images.len(), 2);
    assert!(matches!(
        prepared.images[0].source,
        ImageSource::File { ref path } if path == "/session/attachments/snapshot.png"
    ));
    assert_eq!(prepared.images[1].alias, "clipboard.png");
}

#[test]
fn replacement_drops_a_file_image_when_its_mention_is_removed() {
    let image = ImageAttachment {
        source: ImageSource::File {
            path: "/tmp/diagram.png".to_owned(),
        },
        alias: "diagram.png".to_owned(),
        mime_type: "image/png".to_owned(),
    };
    let mut prepared = PreparedPrompt::from_text("inspect".to_owned());

    message_queue::merge_edit_images(&mut prepared, &[image], "inspect");

    assert!(prepared.images.is_empty());
}
