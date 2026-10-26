//! Agent modes: Shift+Tab cycles the primary agents and switches the session.

use std::sync::Arc;
use std::time::{Duration, Instant};

use serde_json::Value;

use crate::app::{App, Status, ToastSeverity};
use crate::model_picker::FAILED_TOAST_SECS;
use crate::server::{method, AgentSafety, AgentSummary, AgentSwitchParams, AgentType, Client};
use crate::utils::startup_cache::StartupConfig;

/// Delay before the switch spinner replaces the prompt marker.
pub const MODE_SWITCH_SPINNER_DELAY: Duration = Duration::from_millis(500);

/// Braille prompt spinner frames while a switch is slow.
const SPINNER_FRAMES: [char; 10] = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];

/// A settled `session/agent/update`, applied on the main thread.
pub struct Event {
    /// The agent this round-trip asked for.
    pub target: String,
    /// The runtime snapshot the switch returned, or the server's reason when
    /// it refused.
    pub runtime: Result<Value, String>,
}

/// Seed the agents the UI paints and cycles before the app server answers,
/// from the startup projection.
pub fn show_startup_agents(app: &mut App, config: &StartupConfig) {
    app.agents.all = config.agents.clone();
    // First-frame seed only: later snapshots must not reset the live agent.
    if !matches!(app.session.status, Status::Starting) {
        return;
    }
    app.agents.active = app
        .agents
        .all
        .iter()
        .find(|agent| agent.name == config.default_agent)
        .or_else(|| {
            app.agents
                .all
                .iter()
                .find(|agent| agent.agent_type == AgentType::Agent)
        })
        .cloned()
        .unwrap_or_default();
}

/// Refresh the agent snapshot from a runtime value (`{runtime: {...}}`). A pending
/// switch keeps the agent it painted: a snapshot that predates it must not repaint
/// the previous one.
pub fn apply_runtime(app: &mut App, response: &Value) {
    let Some(runtime) = response.get("runtime") else {
        return;
    };
    if let Some(agents) = runtime.get("agents").and_then(Value::as_array) {
        app.agents.all = agents
            .iter()
            .filter_map(|agent| serde_json::from_value(agent.clone()).ok())
            .collect();
    }
    let Some(active) = runtime
        .get("activeAgent")
        .and_then(|active| serde_json::from_value::<AgentSummary>(active.clone()).ok())
    else {
        return;
    };
    // The server folds the session-wide flag and the agent's own into one
    // `bypassToolPermissions`, so only a non-YOLO snapshot reveals the former.
    // Keeping them apart is what lets a switch paint its target in one frame.
    if active.safety != AgentSafety::Yolo {
        app.agents.force_bypass_tool_permissions = runtime
            .get("bypassToolPermissions")
            .and_then(Value::as_bool)
            .unwrap_or(false);
    }
    if app.agents.desired.is_none() {
        set_active(app, active);
    }
}

/// Adopt the server's running agent. A refused switch was a step away from the
/// agent that ran then: once another one runs, Shift+Tab cycles from it, and a
/// snapshot that keeps the same agent keeps the refusal.
pub fn set_active(app: &mut App, active: AgentSummary) {
    if active.name != app.agents.active.name {
        app.agents.refused = None;
    }
    app.agents.active = active;
}

/// Whether every tool call is auto-approved for the agent being painted
/// (`runtime.bypass_tool_permissions`, predicted while switching).
pub fn bypass_tool_permissions(app: &App) -> bool {
    app.agents.force_bypass_tool_permissions || displayed(app).safety == AgentSafety::Yolo
}

/// The agent the chat input paints: the optimistic target of a switch in flight
/// (painted before the RPC answers), else the server's active one.
pub fn displayed(app: &App) -> &AgentSummary {
    app.agents
        .desired
        .as_deref()
        .and_then(|name| app.agents.all.iter().find(|agent| agent.name == name))
        .unwrap_or(&app.agents.active)
}

/// Chat input label: the lowercased display name, marked when every tool call is
/// auto-approved.
pub fn indicator_agent_name(agent: &AgentSummary, bypass_tool_permissions: bool) -> String {
    let name = agent.display_name.to_lowercase();
    if bypass_tool_permissions && agent.safety != AgentSafety::Yolo {
        return format!("{name} · auto-approve");
    }
    name
}

/// Safety driving the chat input border.
pub fn indicator_safety(agent: &AgentSummary, bypass_tool_permissions: bool) -> AgentSafety {
    if bypass_tool_permissions {
        AgentSafety::Yolo
    } else {
        agent.safety
    }
}

/// Spinner glyph shown in place of the prompt marker, or `None` while hidden.
pub fn spinner_glyph(app: &App) -> Option<char> {
    app.agents
        .switching_indicator
        .then(|| SPINNER_FRAMES[app.agents.frame % SPINNER_FRAMES.len()])
}

/// True while a switch is in flight; the chat input then refuses to submit.
pub fn switching(app: &App) -> bool {
    app.agents.switch_active
}

/// Shift+Tab: paint the next primary agent at once and ask the server to switch.
/// Presses coalesce: a switch already in flight cycles from the pending target,
/// not the active one; after a refused switch, the press cycles from the
/// refused target so the cycle can pass it.
pub fn cycle(app: &mut App, client: &Arc<Client>) {
    if app.agents.installing {
        return;
    }
    let refused = app.agents.refused.take();
    let base = app
        .agents
        .desired
        .clone()
        .or(refused)
        .unwrap_or_else(|| app.agents.active.name.clone());
    let Some(target) = next_agent(app, &base) else {
        return;
    };
    app.agents.desired = Some(target);
    app.agents.switching_indicator = false;
    // Before the session is ready there is nothing to switch yet: the label
    // moves now and the last target is sent by `flush_pending`.
    if !app.agents.switch_active && app.session.session_id.is_some() {
        send_desired(app, client);
    }
}

/// Send the agent picked while the session was still starting (nothing to do
/// when Shift+Tab was never pressed).
pub fn flush_pending(app: &mut App, client: &Arc<Client>) {
    if !app.agents.switch_active {
        send_desired(app, client);
    }
}

fn send_desired(app: &mut App, client: &Arc<Client>) {
    let Some(target) = app.agents.desired.clone() else {
        return;
    };
    app.agents.switch_active = true;
    switch(app, client, target);
}

/// Name of the primary agent after `current`, wrapping at the end; subagents
/// never cycle.
fn next_agent(app: &App, current: &str) -> Option<String> {
    let primary: Vec<&AgentSummary> = app
        .agents
        .all
        .iter()
        .filter(|agent| agent.agent_type == AgentType::Agent)
        .collect();
    let index = primary.iter().position(|agent| agent.name == current);
    let next = index.map_or(0, |index| (index + 1) % primary.len());
    primary.get(next).map(|agent| agent.name.clone())
}

/// Issue one `session/agent/update` and arm the delayed prompt spinner.
fn switch(app: &mut App, client: &Arc<Client>, agent_name: String) {
    let Some(session_id) = app.session.session_id.clone() else {
        app.agents.switch_active = false;
        return;
    };
    app.agents.spinner_at = Some(Instant::now() + MODE_SWITCH_SPINNER_DELAY);
    let client = client.clone();
    let tx = app.agents.tx.clone();
    let pending = app.commit_started();
    tokio::spawn(async move {
        let params = AgentSwitchParams {
            session_id,
            agent_name: agent_name.clone(),
        };
        let params = serde_json::to_value(params).unwrap_or_default();
        let runtime = client
            .request(method::SESSION_AGENT_UPDATE, params)
            .await
            .map_err(|error| crate::server::request_error::describe(&error));
        let event = Event {
            target: agent_name,
            runtime,
        };
        crate::input::deliver(tx, event, &pending).await;
    });
}

/// Apply a settled switch: adopt the server's runtime, then chase the last
/// Shift+Tab that landed meanwhile.
pub fn apply_event(app: &mut App, client: &Arc<Client>, event: Event) {
    // Settling first lets this runtime repaint the label; a chased switch keeps
    // painting its own target. A failed switch clears it, so the label falls
    // back to the server's agent.
    app.agents.desired = app
        .agents
        .desired
        .take()
        .filter(|desired| *desired != event.target);
    app.agents.switch_active = false;
    app.agents.spinner_at = None;
    app.agents.switching_indicator = false;
    match &event.runtime {
        Ok(runtime) => {
            app.agents.refused = None;
            apply_runtime(app, runtime);
        }
        // The server keeps the running agent: say why, and remember the
        // target so the next Shift+Tab steps past it instead of asking again.
        Err(error) => {
            app.agents.refused = Some(event.target.clone());
            let message = format!("Failed to switch agent: {error}");
            app.show_toast(message, ToastSeverity::Error, FAILED_TOAST_SECS);
        }
    }
    send_desired(app, client);
    app.commit_finished();
}

/// The armed spinner is due: replace the prompt marker.
pub fn show_switch_spinner(app: &mut App) {
    app.agents.spinner_at = None;
    app.agents.switching_indicator = app.agents.switch_active;
}

#[cfg(test)]
mod tests;
