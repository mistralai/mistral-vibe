//! Post-readiness MCP notices.

use serde_json::Value;

use crate::app::App;
use crate::mcp::state_at;

/// File the OAuth-required hint with the startup warnings.
pub fn show_post_init_notices(app: &mut App, runtime: &Value) {
    let state = state_at(runtime, "/runtime/mcp").unwrap_or_default();
    let aliases = state.needs_auth();
    let Some(first) = aliases.first() else {
        return;
    };
    let command = format!("/mcp login {first}");
    let message = if aliases.len() > 1 {
        format!(
            "MCP servers need OAuth authentication: {}. Run `{command}` to start with '{first}'.",
            aliases.join(", ")
        )
    } else {
        format!("MCP server '{first}' needs OAuth authentication. Run `{command}` to authenticate.")
    };
    crate::notices::warn(app, &message);
}
