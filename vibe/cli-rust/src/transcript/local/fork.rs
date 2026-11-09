//! The settled notice printed once a fork rewind forks the session.

use super::Transcript;
use crate::resume_picker::short_id as short_session;

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
