//! File-backed image links survive the server's inline history projection.

use vibe_rs::server::{
    HistoryEntry, ImageSource, MessageContent, PublicSession, PublicSessionState,
};
use vibe_rs::transcript::Transcript;

fn local_message() -> serde_json::Value {
    serde_json::json!({
        "id": "message-1",
        "type": "message",
        "role": "user",
        "content": [
            {"type": "text", "text": "inspect"},
            {
                "type": "image",
                "attachment": {
                    "source": {"kind": "file", "path": "/session/diagram.png"},
                    "alias": "diagram.png",
                    "mimeType": "image/png"
                }
            }
        ],
        "local": true
    })
}

fn server_message() -> serde_json::Value {
    serde_json::json!({
        "id": "message-1",
        "type": "message",
        "role": "user",
        "content": [
            {"type": "text", "text": "inspect"},
            {
                "type": "image",
                "attachment": {
                    "source": {"kind": "inline", "data": "aW1hZ2U="},
                    "alias": "image",
                    "mimeType": "image/png"
                }
            }
        ]
    })
}

fn assert_file_image(transcript: &Transcript) {
    let entry = transcript.entry(0).expect("server echo");
    assert!(!entry.local);
    let HistoryEntry::Message(message) = entry.entry else {
        panic!("expected user message");
    };
    let MessageContent::Image { attachment } = &message.content[1] else {
        panic!("expected image");
    };
    assert_eq!(attachment.alias, "diagram.png");
    assert!(matches!(
        attachment.source,
        ImageSource::File { ref path } if path == "/session/diagram.png"
    ));
}

#[test]
fn server_echo_keeps_local_file_image_metadata() {
    let mut transcript = Transcript::default();
    transcript.add(&serde_json::json!({"entry": local_message()}));

    transcript.add(&serde_json::json!({"entry": server_message()}));

    assert_file_image(&transcript);
}

#[test]
fn live_snapshot_keeps_local_file_image_metadata() {
    let mut transcript = Transcript::default();
    transcript.add(&serde_json::json!({"entry": local_message()}));
    let state = PublicSessionState {
        event_id: 1,
        session: PublicSession {
            id: "session-1".to_owned(),
            title: None,
            cwd: None,
            token_usage: None,
        },
        history: Some(vec![server_message()]),
        turns: None,
        turn_queue: None,
        retrying: None,
    };

    transcript.load_live_snapshot(&state);

    assert_file_image(&transcript);
}
