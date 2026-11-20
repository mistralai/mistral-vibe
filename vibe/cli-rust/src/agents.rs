//! Agents and approval policies: Shift+Tab cycles the approval policies and switches the session.

use std::sync::Arc;
use std::time::Duration;

use serde::Deserialize;
use serde_json::Value;

use crate::app::{App, Severity, Status};
use crate::server::{
    method, AgentSafety, AgentSummary, AgentType, ApprovalPolicySummary,
    ApprovalPolicySwitchParams, Client, RuntimeSnapshot,
};
use crate::utils::startup_cache::StartupConfig;

/// Delay before the switch spinner replaces the prompt marker.
pub const MODE_SWITCH_SPINNER_DELAY: Duration = Duration::from_millis(500);

/// Braille prompt spinner frames while a switch is slow.
const SPINNER_FRAMES: [char; 10] = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];

/// A settled `session/approvalPolicy/update`, applied on the main thread.
pub struct Event {
    /// The approval policy this round-trip asked for.
    pub target: String,
    /// The runtime snapshot the switch returned, or the server's reason when
    /// it refused.
    pub runtime: Result<Value, String>,
}

/// Seed the agents and policies the UI paints and cycles before the app server
/// answers, from the startup projection.
pub fn show_startup_agents(app: &mut App, config: &StartupConfig) {
    app.agents.all = config.agents.clone();
    app.agents.policies = config.approval_policies.clone();
    // First-frame seed only: later snapshots must not reset the live choices.
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
    app.agents.active_policy = app
        .agents
        .policies
        .iter()
        .find(|policy| policy.name == config.default_approval_policy)
        .or(app.agents.policies.first())
        .cloned()
        .unwrap_or_default();
}

/// Refresh the agent and policy snapshot from a runtime value (`{runtime: {...}}`).
/// A pending switch keeps the policy it painted: a snapshot that predates it must
/// not repaint the previous one.
pub fn apply_runtime(app: &mut App, response: &Value) {
    let Some(runtime) = response.get("runtime") else {
        return;
    };
    if let Some(snapshot) = RuntimeSnapshot::read(response) {
        if let Some(agents) = snapshot.agents {
            app.agents.all = agents;
        }
        if let Some(policies) = snapshot.approval_policies {
            app.agents.policies = policies;
        }
    }
    if let Some(agent) = runtime
        .get("activeAgent")
        .and_then(|active| AgentSummary::deserialize(active).ok())
    {
        app.agents.active = agent;
    }
    let Some(active) = runtime
        .get("activeApprovalPolicy")
        .and_then(|active| ApprovalPolicySummary::deserialize(active).ok())
    else {
        return;
    };
    // The server folds the session-wide flag and the policy's own into one
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

/// Adopt the server's running policy. A refused switch was a step away from the
/// policy that ran then: once another one runs, Shift+Tab cycles from it, and a
/// snapshot that keeps the same policy keeps the refusal.
pub fn set_active(app: &mut App, active: ApprovalPolicySummary) {
    if active.name != app.agents.active_policy.name {
        app.agents.refused = None;
    }
    app.agents.active_policy = active;
}

/// Whether every tool call is auto-approved for the policy being painted
/// (`runtime.bypass_tool_permissions`, predicted while switching).
pub fn bypass_tool_permissions(app: &App) -> bool {
    app.agents.force_bypass_tool_permissions || displayed(app).safety == AgentSafety::Yolo
}

/// The policy the chat input paints: the optimistic target of a switch in flight
/// (painted before the RPC answers), else the server's active one.
pub fn displayed(app: &App) -> &ApprovalPolicySummary {
    app.agents
        .desired
        .as_deref()
        .and_then(|name| {
            app.agents
                .policies
                .iter()
                .find(|policy| policy.name == name)
        })
        .unwrap_or(&app.agents.active_policy)
}

/// Chat input label: the lowercased display name, marked when every tool call is
/// auto-approved.
pub fn indicator_policy_name(
    policy: &ApprovalPolicySummary,
    bypass_tool_permissions: bool,
) -> String {
    let name = policy.display_name.to_lowercase();
    if bypass_tool_permissions && policy.safety != AgentSafety::Yolo {
        return format!("{name} · auto-approve");
    }
    name
}

/// Safety driving the chat input's mode label color.
pub fn indicator_safety(
    policy: &ApprovalPolicySummary,
    bypass_tool_permissions: bool,
) -> AgentSafety {
    if bypass_tool_permissions {
        AgentSafety::Yolo
    } else {
        policy.safety
    }
}

/// The agent the chat input names before the policy, `None` for the default agent.
pub fn indicator_agent_name(app: &App) -> Option<String> {
    let agent = &app.agents.active;
    (!agent.name.is_empty() && agent.name != "default").then(|| agent.display_name.to_lowercase())
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

/// Shift+Tab: paint the next approval policy at once and ask the server to switch.
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
        .unwrap_or_else(|| app.agents.active_policy.name.clone());
    let Some(target) = next_policy(app, &base) else {
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

/// Send the policy picked while the session was still starting (nothing to do
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

/// Name of the policy after `current`, wrapping at the end.
fn next_policy(app: &App, current: &str) -> Option<String> {
    let policies = &app.agents.policies;
    let index = policies.iter().position(|policy| policy.name == current);
    let next = index.map_or(0, |index| (index + 1) % policies.len());
    policies.get(next).map(|policy| policy.name.clone())
}

/// Issue one `session/approvalPolicy/update` and arm the delayed prompt spinner.
fn switch(app: &mut App, client: &Arc<Client>, approval_policy: String) {
    let Some(session_id) = app.session.session_id.clone() else {
        app.agents.switch_active = false;
        return;
    };
    app.agents.spinner_at = Some(crate::clock::now() + MODE_SWITCH_SPINNER_DELAY);
    let client = client.clone();
    crate::ui_event::spawn(app, async move {
        let params = ApprovalPolicySwitchParams {
            session_id,
            approval_policy: approval_policy.clone(),
        };
        let params = serde_json::to_value(params).unwrap_or_default();
        let runtime = client
            .request(method::SESSION_APPROVAL_POLICY_UPDATE, params)
            .await
            .map_err(|error| crate::server::request_error::describe(&error));
        Event {
            target: approval_policy,
            runtime,
        }
    });
}

/// Apply a settled switch: adopt the server's runtime, then chase the last
/// Shift+Tab that landed meanwhile.
pub fn apply_event(app: &mut App, client: &Arc<Client>, event: Event) {
    // Settling first lets this runtime repaint the label; a chased switch keeps
    // painting its own target. A failed switch clears it, so the label falls
    // back to the server's policy.
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
        // The server keeps the running policy: say why, and remember the
        // target so the next Shift+Tab steps past it instead of asking again.
        Err(error) => {
            app.agents.refused = Some(event.target.clone());
            let message = format!("Failed to switch approval policy: {error}");
            crate::notices::inline(app, Severity::Error, &message);
        }
    }
    send_desired(app, client);
}

/// The armed spinner is due: replace the prompt marker.
pub fn show_switch_spinner(app: &mut App) {
    app.agents.spinner_at = None;
    app.agents.switching_indicator = app.agents.switch_active;
}

#[cfg(test)]
mod tests;
