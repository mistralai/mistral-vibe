//! `shared_only` API keys: the `/mcp` warning and the connectors they block.

use crate::server::{MCPSourceKind, MCPSourceStatus, MCPSourceSummary, MCPState};

use super::rows::Row;

pub const SHARED_ONLY_WARNING: &str = "Your API key only gives access to shared connectors: your private connectors are hidden, and connectors that need your login are blocked. To use them, run `vibe --setup` and create a new API key from the link it shows.";
pub const BLOCKED_STATUS: &str = "needs a personal API key";

/// Connecting would create a personal connection that a `shared_only` key can't use.
pub fn blocked(state: &MCPState, source: &MCPSourceSummary) -> bool {
    state.private_connectors_hidden
        && source.kind == MCPSourceKind::Connector
        && source.status == MCPSourceStatus::NeedsAuth
}

/// Detail view of a blocked connector, in place of its auth flow.
pub fn blocked_hint() -> Row {
    Row::Hint {
        before:
            "This connector needs your login, but your API key only gives access to shared connectors. Run "
                .to_owned(),
        key: "vibe --setup".to_owned(),
        after: " and create a new API key from the link it shows.".to_owned(),
    }
}

/// Transcript message when `/mcp` has no source to browse.
pub fn empty_message(state: &MCPState) -> String {
    if state.private_connectors_hidden {
        format!("No MCP servers or connectors available. {SHARED_ONLY_WARNING}")
    } else {
        "No MCP servers or connectors configured.".to_owned()
    }
}
