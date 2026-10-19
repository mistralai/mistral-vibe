//! The run's own usage: a reopened session's stats carry its earlier runs'.

use std::sync::Arc;

use anyhow::{Context, Result};
use serde_json::{json, Value};

use crate::server::{method, Client};

const TOKEN_FIELDS: [&str; 3] = [
    "sessionPromptTokens",
    "sessionCompletionTokens",
    "sessionCachedTokens",
];

/// The session's token counts as the run starts.
#[derive(Clone, Debug, Default)]
pub struct UsageBaseline(Value);

impl UsageBaseline {
    pub fn from_stats(stats: &Value) -> Self {
        Self(stats.clone())
    }

    /// The baseline of a resumed session, read once it is ready; a new session starts at zero.
    pub(super) async fn read(client: &Arc<Client>, session_id: &str) -> Result<Self> {
        let runtime = client
            .request(method::RUNTIME_READ, json!({ "sessionId": session_id }))
            .await
            .context("runtime/read")?;
        Ok(Self::from_stats(
            runtime.pointer("/runtime/stats").unwrap_or(&Value::Null),
        ))
    }

    /// `stats` with the token counts counted from the baseline.
    pub fn run_stats(&self, stats: &Value) -> Value {
        let mut stats = stats.clone();
        for field in TOKEN_FIELDS {
            let spent = |value: &Value| value.get(field).and_then(Value::as_u64).unwrap_or(0);
            let own = spent(&stats).saturating_sub(spent(&self.0));
            if let Some(slot) = stats.get_mut(field) {
                *slot = own.into();
            }
        }
        stats
    }
}
