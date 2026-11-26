//! The text a failed request shows the user.

use super::RequestFailure;

/// The server's own message for an RPC error, else the whole error chain.
pub fn describe(error: &anyhow::Error) -> String {
    match error.downcast_ref::<RequestFailure>() {
        Some(RequestFailure::Rpc(rpc)) if !rpc.message.is_empty() => rpc.message.clone(),
        _ => format!("{error:#}"),
    }
}

/// The notice when a resume hits a session another Vibe process holds; the
/// same wording the desktop app shows for a busy session.
pub const SESSION_BUSY_MESSAGE: &str =
    "This session is open in another Vibe process. Close it there to continue here.";

/// The notice when a resume hits a store written by a newer Vibe.
pub const STORE_REQUIRES_NEWER_READER_MESSAGE: &str = "This session was created by a newer version of Vibe. Restart with --check-upgrade to update Vibe, then resume this session.";

/// The resume-failure notice: a self-contained instruction for the harness
/// codes with a known message, else the prefixed server message.
pub fn resume_failure_message(error: &anyhow::Error) -> String {
    let rpc = match error.downcast_ref::<RequestFailure>() {
        Some(RequestFailure::Rpc(rpc)) => rpc,
        _ => return format!("Failed to resume session: {}", describe(error)),
    };
    if rpc.is_session_busy() {
        return SESSION_BUSY_MESSAGE.to_owned();
    }
    if rpc.harness_code() == Some("store_requires_newer_reader") {
        return STORE_REQUIRES_NEWER_READER_MESSAGE.to_owned();
    }
    format!("Failed to resume session: {}", describe(error))
}
