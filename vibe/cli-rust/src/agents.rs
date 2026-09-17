//! Agent modes: Shift+Tab cycles the primary agents and switches the session.

use std::sync::Arc;
use std::time::{Duration, Instant};

use serde_json::Value;

use crate::app::{App, Status};
use crate::server::{method, AgentSafety, AgentSummary, AgentSwitchParams, AgentType, Client};
use crate::utils::startup_cache::StartupConfig;

/// Delay before the switch spinner replaces the prompt marker (Python
/// `MODE_SWITCH_SPINNER_DELAY`).
pub const MODE_SWITCH_SPINNER_DELAY: Duration = Duration::from_millis(500);

/// Prompt spinner frames while a switch is slow (Python `BrailleSpinner.FRAMES`).
const SPINNER_FRAMES: [char; 10] = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];

/// A settled `session/agent/update`, applied on the main thread.
pub struct Event {
    /// The agent this round-trip asked for (Python `_drain_agent_switches`'s `applied`).
    pub target: String,
    /// The runtime snapshot the switch returned, or `None` when it failed.
    pub runtime: Option<Value>,
}

/// Seed the agents the UI paints and cycles before the app server answers,
/// from the startup projection (Python has no equivalent: it paints on ready).
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

/// Refresh the agent snapshot from a runtime value (`{runtime: {...}}`), like
/// Python's `ClientSessionState.apply_runtime`. A pending switch keeps the agent
/// it painted: a snapshot that predates it must not repaint the previous one.
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
        app.agents.active = active;
    }
}

/// Whether every tool call is auto-approved for the agent being painted
/// (Python `runtime.bypass_tool_permissions`, predicted while switching).
pub fn bypass_tool_permissions(app: &App) -> bool {
    app.agents.force_bypass_tool_permissions || displayed(app).safety == AgentSafety::Yolo
}

/// The agent the chat input paints: the optimistic target of a switch in flight,
/// else the server's active one (Python repaints the widgets before the RPC).
pub fn displayed(app: &App) -> &AgentSummary {
    app.agents
        .desired
        .as_deref()
        .and_then(|name| app.agents.all.iter().find(|agent| agent.name == name))
        .unwrap_or(&app.agents.active)
}

/// Chat input label: the lowercased display name, marked when every tool call is
/// auto-approved (Python `_indicator_agent_name`).
pub fn indicator_agent_name(agent: &AgentSummary, bypass_tool_permissions: bool) -> String {
    let name = agent.display_name.to_lowercase();
    if bypass_tool_permissions && agent.safety != AgentSafety::Yolo {
        return format!("{name} · auto-approve");
    }
    name
}

/// Safety driving the chat input border (Python `_indicator_safety`).
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

/// True while a switch is in flight; the chat input then refuses to submit
/// (Python `ChatInputBody.on_chat_text_area_submitted`).
pub fn switching(app: &App) -> bool {
    app.agents.switch_active
}

/// Shift+Tab: paint the next primary agent at once and ask the server to switch
/// (Python `action_cycle_mode` -> `_request_next_agent`). Presses coalesce: a
/// switch already in flight cycles from the pending target, not the active one.
pub fn cycle(app: &mut App, client: &Arc<Client>) {
    let base = app
        .agents
        .desired
        .clone()
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

/// Name of the primary agent after `current`, wrapping at the end (Python
/// `ClientSessionState.next_agent`); subagents never cycle.
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

/// Issue one `session/agent/update` and arm the delayed prompt spinner (Python
/// `_switch_to_agent`).
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
            .ok();
        let event = Event {
            target: agent_name,
            runtime,
        };
        crate::input::deliver(tx, event, &pending).await;
    });
}

/// Apply a settled switch: adopt the server's runtime, then chase the last
/// Shift+Tab that landed meanwhile (Python `_drain_agent_switches`).
pub fn apply_event(app: &mut App, client: &Arc<Client>, event: Event) {
    // Settling first lets this runtime repaint the label; a chased switch keeps
    // painting its own target. A failed switch clears it, so the label falls
    // back to the server's agent (Python's `finally: _refresh_profile_widgets`).
    app.agents.desired = app
        .agents
        .desired
        .take()
        .filter(|desired| *desired != event.target);
    app.agents.switch_active = false;
    app.agents.spinner_at = None;
    app.agents.switching_indicator = false;
    if let Some(runtime) = &event.runtime {
        apply_runtime(app, runtime);
    }
    send_desired(app, client);
    app.commit_finished();
}

/// The armed spinner is due: replace the prompt marker (Python `_show_switch_spinner`).
pub fn show_switch_spinner(app: &mut App) {
    app.agents.spinner_at = None;
    app.agents.switching_indicator = app.agents.switch_active;
}

#[cfg(test)]
mod tests {
    use super::*;

    fn runtime(active: &str, safety: &str, bypass: bool) -> Value {
        serde_json::json!({
            "runtime": {
                "activeAgent": {"name": active, "displayName": active, "safety": safety},
                "agents": [
                    {"name": "lean", "displayName": "Lean", "safety": "neutral"},
                    {"name": "auto-approve", "displayName": "Auto Approve", "safety": "yolo"},
                ],
                "bypassToolPermissions": bypass,
            }
        })
    }

    #[test]
    fn a_yolo_agents_own_auto_approve_does_not_outlive_it() {
        let mut app = App::default();
        apply_runtime(&mut app, &runtime("auto-approve", "yolo", true));
        assert!(bypass_tool_permissions(&app));
        app.agents.desired = Some("lean".into());
        assert!(!bypass_tool_permissions(&app));
        assert_eq!(displayed(&app).name, "lean");
    }

    #[test]
    fn a_session_wide_auto_approve_survives_a_switch() {
        let mut app = App::default();
        apply_runtime(&mut app, &runtime("lean", "neutral", true));
        app.agents.desired = Some("auto-approve".into());
        assert!(bypass_tool_permissions(&app));
        app.agents.desired = Some("lean".into());
        assert!(bypass_tool_permissions(&app));
    }
}
