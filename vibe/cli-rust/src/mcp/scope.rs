//! `shared_only` API keys: the `/mcp` warning and the connectors they block.

use crate::server::{MCPSourceKind, MCPSourceStatus, MCPSourceSummary, MCPState};

use super::rows::Row;

pub const SHARED_ONLY_WARNING: &str = "Your API key only gives access to shared connectors: your private connectors are hidden, and connectors that need your login are blocked. To use them, run `vibe --setup` and create a new API key from the link it shows.";
pub const BLOCKED_STATUS: &str = "needs a personal API key";

/// Connecting would create a personal connection that a `shared_only` key can't use.
pub fn blocked(state: &MCPState, source: &MCPSourceSummary) -> bool {
    state.private_connectors_hidden
        && source.kind == MCPSourceKind::Connector
        && matches!(
            source.status,
            MCPSourceStatus::NeedsAuth | MCPSourceStatus::NeedsConnect
        )
}

/// Detail view of a blocked connector, in place of its auth flow.
pub fn blocked_hint(source: &MCPSourceSummary) -> Row {
    let need = if source.status == MCPSourceStatus::NeedsConnect {
        "a personal connection"
    } else {
        "your login"
    };
    Row::Hint {
        before: format!(
            "This connector needs {need}, but your API key only gives access to shared connectors. Run "
        ),
        key: "vibe --setup".to_owned(),
        after: " and create a new API key from the link it shows.".to_owned(),
    }
}
