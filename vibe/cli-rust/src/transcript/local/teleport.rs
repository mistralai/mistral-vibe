//! Client-owned rows of a `/teleport` run.

use super::{add_message, Transcript};

pub const TELEPORTING: &str = "Teleporting...";

/// Mount a `&<prompt>` echo (Python `TeleportUserMessage`).
pub fn add_teleport_prompt(transcript: &mut Transcript, id: &str, text: &str) {
    add_message(transcript, id, "teleport_user", text);
}

/// Mount the teleport status row (Python `TeleportMessage`), spinning.
pub fn add_teleport_status(transcript: &mut Transcript, id: &str) {
    transcript.add(&serde_json::json!({
        "entry": {
            "id": id,
            "type": "message",
            "role": "teleport_status",
            "content": [{"type": "text", "text": TELEPORTING}],
            "generationStatus": "in_progress",
            "local": true,
        }
    }));
}

pub fn set_teleport_status(transcript: &mut Transcript, id: &str, text: &str) {
    transcript.patch_local(id, |entry| {
        entry["content"] = serde_json::json!([{"type": "text", "text": text}]);
    });
}

/// Settle the teleport row: the Vibe Code Web link, or cancelled when `None`.
pub fn settle_teleport(transcript: &mut Transcript, id: &str, url: Option<&str>) {
    transcript.patch_local(id, |entry| {
        let (role, text) = match url {
            Some(url) => ("teleport_complete", url),
            None => ("teleport_status", "Teleport cancelled"),
        };
        entry["role"] = serde_json::json!(role);
        entry["content"] = serde_json::json!([{"type": "text", "text": text}]);
        entry["generationStatus"] = serde_json::json!("completed");
    });
}
