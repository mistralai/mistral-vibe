//! Client-owned transcript messages preserved until the server supersedes them.

use serde_json::Value;

use crate::server::{HistoryEntry, MessageContent};

use super::{entry_id, Transcript};
use crate::server::ImageAttachment;

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

pub fn preserve(transcript: &Transcript) -> Vec<(String, Value)> {
    transcript
        .entries
        .iter()
        .map(|entry| &entry.raw)
        .filter(|entry| entry.get("local").and_then(Value::as_bool) == Some(true))
        .filter_map(|entry| entry_id(entry).map(|id| (id, entry.clone())))
        .collect()
}

pub fn restore(transcript: &mut Transcript, entries: Vec<(String, Value)>) {
    for (id, entry) in entries {
        let Some(&index) = transcript.indices.get(&id) else {
            transcript.insert(entry, false);
            continue;
        };
        let stored = &mut transcript.entries[index];
        if preserve_file_image_links(&entry, &mut stored.raw) {
            stored.typed = crate::server::HistoryEntry::from_value(&stored.raw);
            transcript.next_rev += 1;
            stored.rev = transcript.next_rev;
        }
    }
}

/// Keep clickable file metadata when the server echoes the same images as inline bytes.
pub(super) fn preserve_file_image_links(local: &Value, server: &mut Value) -> bool {
    if local.get("role").and_then(Value::as_str) != Some("user")
        || server.get("role").and_then(Value::as_str) != Some("user")
    {
        return false;
    }
    let Some(local_content) = local.get("content").and_then(Value::as_array) else {
        return false;
    };
    let local_images: Vec<Value> = local_content
        .iter()
        .filter(|block| block.get("type").and_then(Value::as_str) == Some("image"))
        .map(|block| block["attachment"].clone())
        .collect();
    let Some(server_content) = server.get_mut("content").and_then(Value::as_array_mut) else {
        return false;
    };
    let server_image_count = server_content
        .iter()
        .filter(|block| block.get("type").and_then(Value::as_str) == Some("image"))
        .count();
    if local_images.len() != server_image_count {
        return false;
    }
    let mut changed = false;
    for (block, attachment) in server_content
        .iter_mut()
        .filter(|block| block.get("type").and_then(Value::as_str) == Some("image"))
        .zip(local_images)
    {
        let local_is_file =
            attachment.pointer("/source/kind").and_then(Value::as_str) == Some("file");
        let server_is_inline = block
            .pointer("/attachment/source/kind")
            .and_then(Value::as_str)
            == Some("inline");
        if local_is_file && server_is_inline {
            block["attachment"] = attachment;
            changed = true;
        }
    }
    changed
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

/// Mount a queued user prompt (Python `UserMessage(pending=True)`).
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

/// Mount a client-owned command result (Python `UserCommandMessage`), the
/// bordered block a slash command prints under its echo.
pub fn add_command_result(transcript: &mut Transcript, id: &str, text: &str) {
    add_message(transcript, id, "command_result", text);
}

/// Mount a settled one-line status (Python `StatusMessage`).
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

/// Insert the bordered error result used by failed slash commands (Python
/// `ErrorMessage`, which sanitizes and prefixes its body at render time).
pub fn add_command_error(transcript: &mut Transcript, id: &str, text: &str) {
    add_message(transcript, id, "command_error", text);
}

/// Mount the settled status message a fork rewind prints (Python
/// `RewindForkMessage`), naming the session before and after the fork.
pub fn add_rewind_fork(
    transcript: &mut Transcript,
    id: &str,
    old_session: &str,
    new_session: &str,
) {
    let text = format!(
        "Forked to a new session.\nsession: {} (before rewind) → {} (after rewind)",
        short_session(old_session),
        short_session(new_session),
    );
    transcript.add(&serde_json::json!({
        "entry": {
            "id": id,
            "type": "checkpoint",
            "kind": "rewind_fork",
            "message": text,
            "local": true,
        }
    }));
}

/// Python `shorten_session_id`: the first 8 characters of a session id.
fn short_session(id: &str) -> &str {
    id.get(..8).unwrap_or(id)
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

/// Mount the local interrupt marker (Python `InterruptMessage`), shown after a
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

/// Mount a borderless warning row (Python `WarningMessage`): plain text in
/// `$warning`, appended to the messages area like Python's `_mount_and_scroll`.
pub fn add_warning(transcript: &mut Transcript, id: &str, text: &str) {
    add_message(transcript, id, "warning", text);
}

/// Mount the what's-new body (Python `WhatsNewMessage`), pinned below every
/// message Python's messages area will ever hold, with its `after-history`
/// top margin decided by the history that exists at mount time.
pub fn add_whats_new(transcript: &mut Transcript, id: &str, text: &str, after_history: bool) {
    transcript.add(&serde_json::json!({
        "entry": {
            "id": id,
            "type": "message",
            "role": "whats_new",
            "content": [{"type": "text", "text": text}],
            "generationStatus": "completed",
            "local": true,
            "pinnedBottom": true,
            "afterHistory": after_history,
        }
    }));
}

/// Mount the custom-tools deprecation banner (Python
/// `CustomToolsDeprecationMessage`), which composes its markdown from the
/// sorted custom tool names and the replacement noun their count picks.
pub fn add_custom_tools_deprecation(transcript: &mut Transcript, id: &str, names: &[String]) {
    let sorted = {
        let mut sorted = names.to_vec();
        sorted.sort();
        sorted
    };
    let backticked: Vec<String> = sorted.iter().map(|name| format!("`{name}`")).collect();
    let replacement = if names.len() == 1 {
        "a skill"
    } else {
        "skills"
    };
    let text = format!(
        "**Support for custom tools will be deprecated soon.** Ask Vibe to help replace yours ({}) with {}.",
        backticked.join(", "),
        replacement
    );
    add_message(transcript, id, "custom_tools_deprecation", &text);
}

/// Mount the compact status indicator (Python `CompactMessage`): a single
/// status row that starts spinning and settles to ✓/✕ in place.
pub fn add_compact_status(transcript: &mut Transcript, id: &str) {
    transcript.add(&serde_json::json!({
        "entry": {
            "id": id,
            "type": "message",
            "role": "compact_status",
            "content": [{"type": "text", "text": "Compacting conversation history..."}],
            "generationStatus": "in_progress",
            "local": true,
        }
    }));
}

/// Settle the compact status indicator: ✓ on success, ✕ on error (Python
/// `CompactMessage.set_complete` / `set_error`).
pub fn settle_compact_status(transcript: &mut Transcript, id: &str, error: Option<&str>) {
    let text = match error {
        Some(msg) => format!("Error: {msg}"),
        None => "Compaction completed.".to_owned(),
    };
    transcript.patch_local(id, |entry| {
        entry["content"] = serde_json::json!([{"type": "text", "text": text}]);
        entry["generationStatus"] = serde_json::json!("completed");
    });
}
