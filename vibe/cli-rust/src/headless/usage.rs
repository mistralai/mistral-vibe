//! The run's own usage: a reopened session's stats carry its earlier runs'.

use std::sync::Arc;

use anyhow::{Context, Result};
use serde_json::json;

use crate::server::{method, Client, RuntimeSnapshot, SessionStats};

/// The session's token counts as the run starts.
#[derive(Clone, Debug, Default)]
pub struct UsageBaseline(pub SessionStats);

impl UsageBaseline {
    /// The baseline of a resumed session, read once it is ready; a new session starts at zero.
    pub(super) async fn read(client: &Arc<Client>, session_id: &str) -> Result<Self> {
        let runtime = client
            .request(method::RUNTIME_READ, json!({ "sessionId": session_id }))
            .await
            .context("runtime/read")?;
        Ok(Self(
            RuntimeSnapshot::read(&runtime).unwrap_or_default().stats,
        ))
    }

    /// `stats` with the token counts counted from the baseline.
    pub fn run_stats(&self, stats: &SessionStats) -> SessionStats {
        let base = &self.0;
        SessionStats {
            session_prompt_tokens: stats
                .session_prompt_tokens
                .saturating_sub(base.session_prompt_tokens),
            session_completion_tokens: stats
                .session_completion_tokens
                .saturating_sub(base.session_completion_tokens),
            session_cached_tokens: stats
                .session_cached_tokens
                .saturating_sub(base.session_cached_tokens),
            ..stats.clone()
        }
    }
}
