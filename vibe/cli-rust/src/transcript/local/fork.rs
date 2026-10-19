//! The settled notices printed once a session is forked (`/branch`, fork rewind).

use super::Transcript;

/// Mount the branch notice, naming the source session and its resumable copy.
pub fn add_branch_created(
    transcript: &mut Transcript,
    id: &str,
    old_session: &str,
    new_session: &str,
) {
    let new_session = short_session(new_session);
    let text = format!(
        "Branched to a new session.\nsession: {} → {new_session}\nResume the copy with: vibe --resume {new_session}",
        short_session(old_session),
    );
    transcript.add(&serde_json::json!({
        "entry": {
            "id": id,
            "type": "checkpoint",
            "kind": "branch_created",
            "message": text,
            "local": true,
        }
    }));
}

/// Mount the settled status message a fork rewind prints, naming the
/// session before and after the fork.
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

/// The first 8 characters of a session id.
fn short_session(id: &str) -> &str {
    id.get(..8).unwrap_or(id)
}
