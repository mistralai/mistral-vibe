//! Post-readiness MCP notices (Python `_show_post_init_notices_once`).

use serde_json::Value;

use crate::app::{App, ToastSeverity};
use crate::mcp::{add_result, state_at};

/// Seconds a discovery-failure toast stays up (Python `notify(timeout=10)`).
const DISCOVERY_TOAST_SECS: u64 = 10;

/// Toast every failed discovery, then mount the OAuth-required notice.
pub fn show_post_init_notices(app: &mut App, runtime: &Value) {
    let state = state_at(runtime, "/runtime/mcp");
    for (name, error) in &state.discovery_errors {
        app.show_toast(
            format!("MCP server '{name}' failed to connect: {error}"),
            ToastSeverity::Warning,
            DISCOVERY_TOAST_SECS,
        );
    }
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
    add_result(app, &message);
}
