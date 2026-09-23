//! Preserve client-owned entries and their positions across snapshot rebuilds.

use serde_json::Value;

use crate::transcript::Transcript;

/// A local entry anchored by server-entry count because snapshots can replace ids.
pub struct PreservedEntry {
    id: String,
    raw: Value,
    /// Server-owned entries that preceded it (Python's mount order).
    server_before: usize,
    /// Trailing local rows stay below rebuilt history.
    followed_by_server: bool,
}

pub fn preserve(transcript: &Transcript) -> Vec<PreservedEntry> {
    let mut server_before = 0;
    let mut preserved: Vec<PreservedEntry> = Vec::new();
    for entry in &transcript.entries {
        if !entry.local {
            server_before += 1;
            for pending in &mut preserved {
                pending.followed_by_server = true;
            }
            continue;
        }
        preserved.push(PreservedEntry {
            id: entry.id.clone(),
            raw: entry.raw.clone(),
            server_before,
            followed_by_server: false,
        });
    }
    preserved
}

pub fn restore(transcript: &mut Transcript, entries: Vec<PreservedEntry>) {
    restore_entries(transcript, entries, false);
}

pub fn restore_positions(transcript: &mut Transcript, entries: Vec<PreservedEntry>) {
    restore_entries(transcript, entries, true);
}

fn restore_entries(
    transcript: &mut Transcript,
    entries: Vec<PreservedEntry>,
    restore_positions: bool,
) {
    for entry in entries {
        let Some(&index) = transcript.indices.get(&entry.id) else {
            transcript.insert(entry.raw, false);
            if restore_positions && entry.followed_by_server {
                restore_position(transcript, &entry.id, entry.server_before);
            }
            continue;
        };
        let stored = &mut transcript.entries[index];
        if preserve_file_image_links(&entry.raw, &mut stored.raw) {
            stored.typed = crate::server::HistoryEntry::from_value(&stored.raw);
            transcript.next_rev += 1;
            stored.rev = transcript.next_rev;
        }
    }
}

/// Restore a local row before its server anchor without moving queued or pinned rows.
fn restore_position(transcript: &mut Transcript, id: &str, server_before: usize) {
    let Some(from) = transcript.indices.get(id).copied() else {
        return;
    };
    let stored = &transcript.entries[from];
    if stored.pinned_bottom || stored.pending {
        return;
    }
    let mut seen = 0;
    let to = transcript
        .entries
        .iter()
        .position(|entry| {
            if entry.local {
                return false;
            }
            seen += 1;
            seen > server_before
        })
        .unwrap_or(transcript.entries.len());
    if from <= to {
        return;
    }
    let stored = transcript.entries.remove(from);
    transcript.entries.insert(to, stored);
    transcript.reindex(to);
}

/// Keep clickable file metadata when the server echoes the same images as inline bytes.
pub(in crate::transcript) fn preserve_file_image_links(local: &Value, server: &mut Value) -> bool {
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
