//! Which startup failures are Vibe client bugs worth a Sentry event.

use crate::observability::sentry::{is_environmental, with_fingerprint};
use crate::server::{Client, RequestFailure};

/// A failure the server reported, such as a worktree it could not prepare.
#[derive(Debug)]
pub struct ServerFailure(pub String);

impl std::fmt::Display for ServerFailure {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for ServerFailure {}

/// `None` unless a client bug; grouped by category, never by message.
pub fn startup_fingerprint(error: &anyhow::Error) -> Option<[String; 2]> {
    if is_environmental(error) || error.downcast_ref::<ServerFailure>().is_some() {
        return None;
    }
    let category = match error.downcast_ref::<RequestFailure>() {
        Some(RequestFailure::Rpc(_)) => return None,
        Some(RequestFailure::AppServerClosed) => "app_server_closed",
        Some(RequestFailure::FrameTooLarge(_)) => "frame_too_large",
        None => "unclassified",
    };
    Some(["vibe-rs-startup".to_owned(), category.to_owned()])
}

/// Logs a failed handshake: fatal, so reported, only for a client bug.
pub(super) fn log(error: &anyhow::Error, client: &Client) {
    // Quitting mid-startup closes the app-server on purpose.
    match startup_fingerprint(error).filter(|_| !client.is_shutting_down()) {
        Some(fingerprint) => with_fingerprint(&fingerprint, || {
            tracing::error!(
                vibe_boundary = "startup",
                fatal = true,
                "startup failed: {error:#}"
            );
        }),
        None => tracing::error!(vibe_boundary = "startup", "startup failed: {error:#}"),
    }
}

/// Logs a failed app-server spawn; a broken environment is not reported.
pub fn log_spawn(error: &anyhow::Error) {
    if is_environmental(error) {
        tracing::warn!(vibe_boundary = "startup", "{error:#}");
    } else {
        tracing::error!(vibe_boundary = "startup", fatal = true, "{error:#}");
    }
}
