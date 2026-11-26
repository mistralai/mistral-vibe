//! `/agent`: list the primary agents, or switch the session to one by name.

use std::sync::Arc;

use super::simple::add_text;
use super::{spawn_session, CommandEvent};
use crate::app::App;
use crate::server::request_error::describe;
use crate::server::{method, AgentSwitchParams, AgentType, Client};

pub fn command(app: &mut App, client: &Arc<Client>, name: &str) {
    if name.is_empty() {
        add_text(app, &list(app));
        return;
    }
    let agent_name = name.to_owned();
    spawn_session(app, client, move |client, session_id| async move {
        let params = AgentSwitchParams {
            session_id,
            agent_name: agent_name.clone(),
        };
        let params = serde_json::to_value(params).unwrap_or_default();
        match client.request(method::SESSION_AGENT_UPDATE, params).await {
            Ok(runtime) => {
                CommandEvent::Runtime(runtime, format!("Switched to agent {agent_name}."))
            }
            Err(error) => {
                CommandEvent::Error(format!("Failed to switch agent: {}", describe(&error)))
            }
        }
    });
}

/// The primary agents as markdown, the active one marked.
pub fn list(app: &App) -> String {
    let mut lines = vec!["## Agents".to_owned(), String::new()];
    for agent in app
        .agents
        .all
        .iter()
        .filter(|agent| agent.agent_type == AgentType::Agent)
    {
        let active = if agent.name == app.agents.active.name {
            " (active)"
        } else {
            ""
        };
        lines.push(format!("- `{}`{active}: {}", agent.name, agent.description));
    }
    lines.push(String::new());
    lines.push("Switch with `/agent <name>`.".to_owned());
    lines.join("\n")
}
