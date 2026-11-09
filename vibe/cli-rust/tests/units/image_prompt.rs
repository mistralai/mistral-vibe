//! Prepared image attachments survive conversion to app-server turn content.

use vibe_rs::server::{ContentBlock, HistoryEntry, MessageContent, PreparedPrompt};

#[test]
fn file_images_become_session_image_blocks() {
    let prepared: PreparedPrompt = serde_json::from_value(serde_json::json!({
        "displayText": "look at @shot.png",
        "promptText": "look at shot.png",
        "images": [{
            "source": {"kind": "file", "path": "/tmp/session image.png"},
            "alias": "shot.png",
            "mimeType": "image/png"
        }],
        "autoTitle": null,
        "mentions": {"count": 1, "contextTypes": {"image": 1}, "fileExtensions": {".png": 1}}
    }))
    .expect("deserialize prepared prompt");

    let content = prepared.content_blocks("fallback");
    assert_eq!(content.len(), 2);
    assert!(matches!(&content[0], ContentBlock::Text { text } if text == "look at shot.png"));
    assert_eq!(
        serde_json::to_value(&content[1]).expect("serialize image block"),
        serde_json::json!({
            "type": "image",
            "uri": "file:///tmp/session%20image.png",
            "mediaType": "image/png",
            "altText": "shot.png"
        })
    );
}

#[test]
fn inline_images_become_data_uris() {
    let prepared: PreparedPrompt = serde_json::from_value(serde_json::json!({
        "displayText": "image",
        "promptText": "image",
        "images": [{
            "source": {"kind": "inline", "data": "aGVsbG8="},
            "alias": "image",
            "mimeType": "image/webp"
        }]
    }))
    .expect("deserialize prepared prompt");

    assert_eq!(
        serde_json::to_value(&prepared.content_blocks("fallback")[1])
            .expect("serialize image block"),
        serde_json::json!({
            "type": "image",
            "uri": "data:image/webp;base64,aGVsbG8=",
            "mediaType": "image/webp",
            "altText": "image"
        })
    );
}

#[test]
fn missing_prompt_text_uses_the_raw_prompt_fallback() {
    let prepared = PreparedPrompt::from_response(
        &serde_json::json!({
            "prompt": {"displayText": "raw prompt", "images": []}
        }),
        "raw prompt",
    );

    assert!(matches!(
        &prepared.content_blocks("raw prompt")[0],
        ContentBlock::Text { text } if text == "raw prompt"
    ));
}

#[test]
fn malformed_prepared_images_are_skipped_individually() {
    let prepared: PreparedPrompt = serde_json::from_value(serde_json::json!({
        "promptText": "inspect",
        "images": [
            {"alias": "legacy.png", "mimeType": "image/png"},
            {
                "source": {"kind": "file", "path": "/tmp/good.png"},
                "alias": "good.png",
                "mimeType": "image/png"
            }
        ]
    }))
    .expect("deserialize with one malformed image");

    assert_eq!(prepared.images.len(), 1);
    assert_eq!(prepared.images[0].alias, "good.png");
}

#[test]
fn malformed_history_image_does_not_hide_valid_message_text() {
    let entry = HistoryEntry::from_value(&serde_json::json!({
        "type": "message",
        "role": "user",
        "content": [
            {"type": "text", "text": "still visible"},
            {"type": "image", "attachment": {"alias": "legacy.png"}}
        ]
    }));

    let HistoryEntry::Message(message) = entry else {
        panic!("message should remain visible");
    };
    assert!(matches!(
        &message.content[0],
        MessageContent::Text { text } if text == "still visible"
    ));
    assert_eq!(message.content.len(), 1);
}
