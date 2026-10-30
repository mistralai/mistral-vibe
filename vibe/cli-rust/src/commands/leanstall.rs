//! `/leanstall` and `/unleanstall`: install or remove the lean agent.

use std::sync::Arc;

use super::submission::new_message_id;
use super::CommandEvent;
use crate::agents;
use crate::app::App;
use crate::input::deliver;
use crate::server::request_error::describe;
use crate::server::{method, AgentInstallParams, AgentsListResponse, Client};
use crate::transcript::local;

const LEAN: &str = "lean";
pub const SWITCHING: &str = "Wait for the agent switch to finish, then run the command again.";

/// A settled `agents/install` (`installed`) or `agents/uninstall`.
pub struct Event {
    pub installed: bool,
    pub result: Result<AgentsListResponse, String>,
}

pub fn install_lean(app: &mut App, client: &Arc<Client>) {
    set_installed(app, client, true);
}

pub fn uninstall_lean(app: &mut App, client: &Arc<Client>) {
    set_installed(app, client, false);
}

/// The notice that answers the command without a round-trip, if any.
pub fn short_circuit(app: &App, installed: bool) -> Option<&'static str> {
    let present = app.agents.all.iter().any(|agent| agent.name == LEAN);
    match (installed, present) {
        (true, true) => Some("Lean agent is already installed."),
        (false, false) => Some("Lean agent is not installed."),
        _ => None,
    }
}

fn set_installed(app: &mut App, client: &Arc<Client>, installed: bool) {
    if let Some(notice) = short_circuit(app, installed) {
        local::add_command_status(&mut app.view.transcript, &new_message_id(), notice);
        return;
    }
    // Replayed at ready, a deferred command can race the Shift+Tab switch sent just before it.
    if app.agents.switch_active {
        local::add_command_error(&mut app.view.transcript, &new_message_id(), SWITCHING);
        return;
    }
    let Some(session_id) = app.session.session_id.clone() else {
        return;
    };
    let tx = app.command_tx.clone();
    let pending = app.commit_started();
    let client = client.clone();
    app.agents.installing = true;
    tokio::spawn(async move {
        let result = request(&client, session_id, installed).await;
        let event = Event { installed, result };
        deliver(tx, CommandEvent::Leanstall(Box::new(event)), &pending).await;
    });
}

async fn request(
    client: &Client,
    session_id: String,
    installed: bool,
) -> Result<AgentsListResponse, String> {
    let method = if installed {
        method::AGENTS_INSTALL
    } else {
        method::AGENTS_UNINSTALL
    };
    let params = AgentInstallParams {
        session_id,
        agent_name: LEAN.to_owned(),
    };
    let params = serde_json::to_value(params).map_err(|error| error.to_string())?;
    let value = client
        .request(method, params)
        .await
        .map_err(|error| describe(&error))?;
    serde_json::from_value(value).map_err(|error| error.to_string())
}

pub fn apply_event(app: &mut App, event: Event) {
    app.agents.installing = false;
    let id = new_message_id();
    let response = match event.result {
        Ok(response) => response,
        Err(error) => {
            local::add_command_error(&mut app.view.transcript, &id, &error);
            return;
        }
    };
    app.agents.all = response.agents;
    agents::set_active(app, response.active);
    let notice = if event.installed {
        "Lean agent installed."
    } else {
        "Lean agent uninstalled."
    };
    local::add_command_status(&mut app.view.transcript, &id, notice);
}
