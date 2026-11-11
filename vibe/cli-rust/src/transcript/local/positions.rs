//! Preserve client-owned entries and their positions across snapshot rebuilds.

use serde_json::Value;

use crate::transcript::Transcript;

/// A local entry anchored by the server entries around it, or by server-entry count when a snapshot replaced both ids.
pub struct PreservedEntry {
    id: String,
    raw: Value,
    /// Server-owned entries that preceded it (mount order).
    server_before: usize,
    /// Trailing local rows stay below rebuilt history.
    followed_by_server: bool,
    /// The first server entry below it, which a snapshot keeps when it drops or pages older rows.
    next_server_id: Option<String>,
    /// The last server entry above it, the anchor when the one below is gone.
    prev_server_id: Option<String>,
}

pub fn preserve(transcript: &Transcript) -> Vec<PreservedEntry> {
    let mut server_before = 0;
    let mut after_server = false;
    let mut prev_server_id: Option<&str> = None;
    let mut preserved: Vec<PreservedEntry> = Vec::new();
    for entry in &transcript.entries {
        // A merged follow-up's text is already in its echoed head's server entry.
        if entry.local && entry.follows_user && after_server {
            continue;
        }
        after_server = !entry.local;
        if !entry.local {
            server_before += 1;
            for pending in &mut preserved {
                pending.followed_by_server = true;
                pending
                    .next_server_id
                    .get_or_insert_with(|| entry.id.clone());
            }
            prev_server_id = Some(&entry.id);
            continue;
        }
        preserved.push(PreservedEntry {
            id: entry.id.clone(),
            raw: entry.raw.clone(),
            server_before,
            followed_by_server: false,
            next_server_id: None,
            prev_server_id: prev_server_id.map(str::to_owned),
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
    let mut head_in_history = false;
    // A restored row never lands above the row restored before it, nor above that row's anchor when it followed it.
    let mut floor = 0;
    let mut floor_anchor: Option<String> = None;
    for mut entry in entries {
        // The rebuilt history already merged this follow-up into its head.
        let follow_up = entry.raw.get("followsUser").and_then(Value::as_bool) == Some(true);
        if follow_up && head_in_history {
            continue;
        }
        head_in_history = transcript.indices.contains_key(&entry.id);
        let Some(&index) = transcript.indices.get(&entry.id) else {
            transcript.insert(std::mem::take(&mut entry.raw), false);
            if restore_positions && entry.followed_by_server {
                if floor_anchor != entry.next_server_id {
                    let anchor = floor_anchor
                        .as_ref()
                        .and_then(|id| transcript.indices.get(id));
                    floor = anchor.map_or(floor, |&anchor| floor.max(anchor + 1));
                }
                if let Some(index) = restore_position(transcript, &entry, floor) {
                    floor = index + 1;
                    floor_anchor = entry.next_server_id.take();
                }
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

/// Restore a local row before its server anchor, not above `floor`, without moving queued or pinned rows.
fn restore_position(
    transcript: &mut Transcript,
    entry: &PreservedEntry,
    floor: usize,
) -> Option<usize> {
    let from = transcript.indices.get(&entry.id).copied()?;
    let stored = &transcript.entries[from];
    if stored.pinned_bottom || stored.pending {
        return None;
    }
    let index_of = |id: &Option<String>| id.as_ref().and_then(|id| transcript.indices.get(id));
    let to = index_of(&entry.next_server_id)
        .copied()
        .or_else(|| index_of(&entry.prev_server_id).map(|index| index + 1))
        .unwrap_or_else(|| nth_server_index(transcript, entry.server_before))
        .max(floor);
    if from <= to {
        return Some(from);
    }
    let stored = transcript.entries.remove(from);
    transcript.entries.insert(to, stored);
    transcript.reindex(to);
    Some(to)
}

/// Index of the server entry that follows `server_before` server entries, or the end.
fn nth_server_index(transcript: &Transcript, server_before: usize) -> usize {
    let mut seen = 0;
    transcript
        .entries
        .iter()
        .position(|entry| {
            if entry.local {
                return false;
            }
            seen += 1;
            seen > server_before
        })
        .unwrap_or(transcript.entries.len())
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
