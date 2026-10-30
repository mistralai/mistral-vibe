//! Post-readiness MCP notices.

use serde_json::Value;

use crate::app::App;
use crate::commands::submission::new_message_id;
use crate::mcp::state_at;
use crate::transcript::local;

/// Seconds a failed connector or MCP load stays up.
pub(crate) const INTEGRATIONS_UNAVAILABLE_TOAST_SECS: u64 = 10;

/// Mount the OAuth-required notice. Failed loads arrive as `warning`
/// notifications instead, whenever the background loads end.
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
    local::add_command_warning(&mut app.view.transcript, &new_message_id(), &message);
}
