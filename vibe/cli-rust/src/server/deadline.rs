//! How long a request may take before its caller gives up on it.

use std::future::Future;
use std::time::Duration;

use anyhow::Context;

/// The wait every one-shot request shares: config and MCP commands, and `/skills` reads.
pub const REQUEST_TIMEOUT: Duration = Duration::from_secs(30);

/// `answer`, unless it takes longer than `limit`: then an error saying `method` timed out.
pub async fn within<T>(
    method: &str,
    limit: Duration,
    answer: impl Future<Output = T>,
) -> anyhow::Result<T> {
    tokio::time::timeout(limit, answer)
        .await
        .with_context(|| format!("{method} timed out"))
}
