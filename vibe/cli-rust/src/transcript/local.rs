//! Client-owned transcript messages preserved until the server supersedes them.

mod fork;
mod positions;
pub mod teleport;

pub use fork::{add_branch_created, add_rewind_fork};
pub(super) use positions::preserve_file_image_links;
pub use positions::{preserve, restore, restore_positions};

use serde_json::Value;

use crate::server::{HistoryEntry, ImageAttachment, MessageContent};

use super::Transcript;

pub(super) fn user_projection_for_echo(
    transcript: &Transcript,
    id: &str,
    raw: &Value,
    incoming: &HistoryEntry,
) -> Option<(HistoryEntry, bool, bool)> {
    if raw.get("local").and_then(Value::as_bool).unwrap_or(false) || !is_user_message(incoming) {
        return None;
    }
    let stored = transcript
        .indices
        .get(id)
        .and_then(|&index| transcript.entries.get(index))?;
    (stored.local && is_user_message(&stored.typed)).then(|| {
        (
            stored.typed.clone(),
            stored.follows_user,
            stored.followed_by_user,
        )
    })
}

fn is_user_message(entry: &HistoryEntry) -> bool {
    matches!(entry, HistoryEntry::Message(message) if message.role == "user")
}

/// Insert a client-owned message immediately. A server echo with the same id
/// replaces it, while snapshots preserve it until then.
pub fn add_message(transcript: &mut Transcript, id: &str, role: &str, text: &str) {
    transcript.add(&serde_json::json!({
        "entry": {
            "id": id,
            "type": "message",
            "role": role,
            "content": [{"type": "text", "text": text}],
            "generationStatus": "completed",
            "local": true,
        }
    }));
}

/// Mount a user prompt immediately, including prepared image attachments.
pub fn add_prompt(
    transcript: &mut Transcript,
    id: &str,
    text: &str,
    images: &[ImageAttachment],
    pending: bool,
) {
    transcript.add(&serde_json::json!({
        "entry": {
            "id": id,
            "type": "message",
            "role": "user",
            "content": prompt_content(text, images),
            "generationStatus": "completed",
            "local": true,
            "pending": pending,
        }
    }));
}

/// Mount a queued user prompt.
pub fn add_pending_prompt(transcript: &mut Transcript, id: &str, text: &str) {
    add_prompt(transcript, id, text, &[], true);
}

/// Promote one merged queue run while keeping its prompts visually packed.
pub fn promote_prompts(transcript: &mut Transcript, ids: &[String]) {
    for (index, id) in ids.iter().enumerate() {
        let Some(&entry_index) = transcript.indices.get(id) else {
            continue;
        };
        if !transcript.entries[entry_index].local {
            transcript.next_rev += 1;
            let stored = &mut transcript.entries[entry_index];
            stored.pending = false;
            stored.follows_user = index > 0;
            stored.followed_by_user = index + 1 < ids.len();
            stored.rev = transcript.next_rev;
            continue;
        }
        transcript.patch_local(id, |entry| {
            entry["pending"] = Value::Bool(false);
            entry["followsUser"] = Value::Bool(index > 0);
            entry["followedByUser"] = Value::Bool(index + 1 < ids.len());
        });
    }
}

/// Rewrite a queued prompt's text and prepared attachments.
pub fn set_prompt(transcript: &mut Transcript, id: &str, text: &str, images: &[ImageAttachment]) {
    let Some(&index) = transcript.indices.get(id) else {
        return;
    };
    if !transcript.entries[index].local {
        transcript.next_rev += 1;
        let stored = &mut transcript.entries[index];
        if let HistoryEntry::Message(message) = &mut stored.typed {
            let mut content = vec![MessageContent::Text {
                text: text.to_owned(),
            }];
            content.extend(
                images
                    .iter()
                    .cloned()
                    .map(|attachment| MessageContent::Image { attachment }),
            );
            message.content = content;
            stored.rev = transcript.next_rev;
        }
        return;
    }
    transcript.patch_local(id, |entry| {
        entry["content"] = prompt_content(text, images);
    });
}

pub fn set_text(transcript: &mut Transcript, id: &str, text: &str) {
    set_prompt(transcript, id, text, &[]);
}

fn prompt_content(text: &str, images: &[ImageAttachment]) -> Value {
    let mut content = vec![serde_json::json!({"type": "text", "text": text})];
    content.extend(
        images
            .iter()
            .map(|attachment| serde_json::json!({"type": "image", "attachment": attachment})),
    );
    Value::Array(content)
}

/// Mount a content command's markdown body, rendered like an assistant reply.
pub fn add_command_result(transcript: &mut Transcript, id: &str, text: &str) {
    add_message(transcript, id, "command_result", text);
}

/// Mount an action outcome: a `✓` row with a markdown body.
pub fn add_command_status(transcript: &mut Transcript, id: &str, text: &str) {
    add_message(transcript, id, "command_status", text);
}

/// Mount an outcome that needs the user's attention: a `⚠` status row.
pub fn add_command_warning(transcript: &mut Transcript, id: &str, text: &str) {
    add_message(transcript, id, "command_warning", text);
}

/// Mount a settled one-line status.
pub fn add_status(transcript: &mut Transcript, id: &str, text: &str) {
    transcript.add(&serde_json::json!({
        "entry": {
            "id": id,
            "type": "checkpoint",
            "kind": "status",
            "message": text,
            "local": true,
        }
    }));
}

/// Mount a `✕` error row; the body is sanitized and prefixed at render time.
pub fn add_command_error(transcript: &mut Transcript, id: &str, text: &str) {
    add_message(transcript, id, "command_error", text);
}

/// Mount a client-owned notice (e.g. app-server crash) preserved across snapshots.
pub fn add_notice(transcript: &mut Transcript, id: &str, message: &str) {
    transcript.add(&serde_json::json!({
        "entry": {
            "id": id,
            "type": "notice",
            "level": "error",
            "message": message,
            "local": true,
        }
    }));
}

/// Mount the local interrupt marker, shown after a
/// turn is cancelled with Escape. It is client-owned and preserved across
/// snapshots like other local entries.
pub fn add_interrupt(transcript: &mut Transcript, id: &str) {
    transcript.add(&serde_json::json!({
        "entry": {
            "id": id,
            "type": "interrupt",
            "generationStatus": "completed",
            "local": true,
        }
    }));
}

/// Mount a borderless warning row: plain text in `$warning`, appended to the
/// messages area.
pub fn add_warning(transcript: &mut Transcript, id: &str, text: &str) {
    add_message(transcript, id, "warning", text);
}

/// Mount the what's-new body of a fresh session, pinned below every message
/// the transcript will ever hold.
pub fn add_whats_new(transcript: &mut Transcript, id: &str, text: &str) {
    transcript.add(&serde_json::json!({
        "entry": {
            "id": id,
            "type": "message",
            "role": "whats_new",
            "content": [{"type": "text", "text": text}],
            "generationStatus": "completed",
            "local": true,
            "pinnedBottom": true,
            "afterHistory": false,
        }
    }));
}

/// Mount the compact status indicator: a single
/// status row that starts spinning and settles to ✓/✕ in place.
pub fn add_compact_status(transcript: &mut Transcript, id: &str) {
    add_progress_status(transcript, id, "Compacting conversation history...");
}

/// Mount a status row that spins until [`settle_status`] resolves it.
pub fn add_progress_status(transcript: &mut Transcript, id: &str, text: &str) {
    transcript.add(&serde_json::json!({
        "entry": {
            "id": id,
            "type": "message",
            "role": "progress_status",
            "content": [{"type": "text", "text": text}],
            "generationStatus": "in_progress",
            "local": true,
        }
    }));
}

/// Settle the compact status indicator: ✓ on success, ✕ on error.
pub fn settle_compact_status(transcript: &mut Transcript, id: &str, error: Option<&str>) {
    match error {
        Some(msg) => settle_status(transcript, id, &format!("Error: {msg}")),
        None => settle_status(transcript, id, "Compaction completed."),
    }
}

/// Settle a progress status row in place: ✕ for an `Error:` text, ✓ otherwise.
pub fn settle_status(transcript: &mut Transcript, id: &str, text: &str) {
    transcript.patch_local(id, |entry| {
        entry["content"] = serde_json::json!([{"type": "text", "text": text}]);
        entry["generationStatus"] = serde_json::json!("completed");
    });
}

#[cfg(test)]
mod tests;
