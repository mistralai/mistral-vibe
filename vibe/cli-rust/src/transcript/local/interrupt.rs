//! The local interrupt marker Esc leaves, and its `/retry` hint.

use super::Transcript;
use crate::server::HistoryEntry;

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

/// Whether the interrupt marker `id` shows the `/retry` hint; `None` for any other row.
fn interrupt_retryable(transcript: &Transcript, id: &str) -> Option<bool> {
    let index = *transcript.indices.get(id)?;
    match &transcript.entries[index].typed {
        HistoryEntry::Interrupt(marker) => Some(marker.retryable),
        _ => None,
    }
}

/// Whether `id` is an interrupt marker.
pub fn is_interrupt(transcript: &Transcript, id: &str) -> bool {
    interrupt_retryable(transcript, id).is_some()
}

/// Show or hide the `/retry` hint on an interrupt marker.
pub fn set_interrupt_retryable(transcript: &mut Transcript, id: &str, retryable: bool) {
    if interrupt_retryable(transcript, id).is_some_and(|current| current != retryable) {
        transcript.patch_local(id, |entry| {
            entry["retryable"] = serde_json::json!(retryable)
        });
    }
}
