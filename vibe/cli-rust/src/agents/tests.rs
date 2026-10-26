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

const REFUSAL: &str = "Agent 'broken' in /home/agents/broken.toml: tools: tools.bash: \
                       default_timeout is set without max_timeout_seconds. Set both, or neither.";

/// `ask` is running; the cycle is ask -> broken -> auto-approve.
fn app_with_broken_profile() -> App {
    let mut app = App::default();
    apply_runtime(
        &mut app,
        &serde_json::json!({
            "runtime": {
                "activeAgent": {"name": "ask", "displayName": "Ask", "safety": "safe"},
                "agents": [
                    {"name": "ask", "displayName": "Ask", "safety": "safe"},
                    {"name": "broken", "displayName": "Broken", "safety": "neutral"},
                    {"name": "auto-approve", "displayName": "Auto Approve", "safety": "yolo"},
                ],
            }
        }),
    );
    app
}

/// Settle a Shift+Tab switch to `target` with the server's answer.
fn settle(app: &mut App, client: &Arc<Client>, target: &str, runtime: Result<Value, String>) {
    app.agents.desired = Some(target.into());
    app.agents.switch_active = true;
    app.commit_started();
    let event = Event {
        target: target.into(),
        runtime,
    };
    apply_event(app, client, event);
}

#[test]
fn a_refused_switch_shows_the_servers_reason_and_keeps_the_running_agent() {
    let client = Arc::new(Client::stub());
    let mut app = app_with_broken_profile();
    settle(&mut app, &client, "broken", Err(REFUSAL.into()));
    assert_eq!(displayed(&app).name, "ask");
    assert!(!switching(&app));
    let toast = app.overlays.toasts.back().expect("the refusal is shown");
    assert_eq!(toast.text, format!("Failed to switch agent: {REFUSAL}"));
    assert!(matches!(toast.severity, ToastSeverity::Error));
}

#[test]
fn the_next_shift_tab_steps_past_a_refused_agent() {
    let client = Arc::new(Client::stub());
    let mut app = app_with_broken_profile();
    settle(&mut app, &client, "broken", Err(REFUSAL.into()));
    cycle(&mut app, &client);
    assert_eq!(app.agents.desired.as_deref(), Some("auto-approve"));
    assert_eq!(app.agents.refused, None);
}

#[test]
fn a_switch_that_lands_cycles_from_the_new_agent_again() {
    let client = Arc::new(Client::stub());
    let mut app = app_with_broken_profile();
    settle(&mut app, &client, "broken", Err(REFUSAL.into()));
    let landed = serde_json::json!({
        "runtime": {"activeAgent": {"name": "ask", "displayName": "Ask", "safety": "safe"}}
    });
    settle(&mut app, &client, "ask", Ok(landed));
    assert_eq!(app.agents.refused, None);
    cycle(&mut app, &client);
    assert_eq!(app.agents.desired.as_deref(), Some("broken"));
}

#[test]
fn a_refusal_survives_a_snapshot_that_keeps_the_running_agent() {
    let client = Arc::new(Client::stub());
    let mut app = app_with_broken_profile();
    settle(&mut app, &client, "broken", Err(REFUSAL.into()));
    let unchanged = serde_json::json!({
        "runtime": {"activeAgent": {"name": "ask", "displayName": "Ask", "safety": "safe"}}
    });
    apply_runtime(&mut app, &unchanged);
    assert_eq!(app.agents.refused.as_deref(), Some("broken"));
    cycle(&mut app, &client);
    assert_eq!(app.agents.desired.as_deref(), Some("auto-approve"));
}

#[test]
fn a_refusal_is_dropped_once_another_agent_runs() {
    let client = Arc::new(Client::stub());
    let mut app = app_with_broken_profile();
    settle(&mut app, &client, "broken", Err(REFUSAL.into()));
    let moved = serde_json::json!({
        "runtime": {"activeAgent": {"name": "auto-approve", "displayName": "Auto Approve", "safety": "yolo"}}
    });
    apply_runtime(&mut app, &moved);
    assert_eq!(app.agents.refused, None);
    assert_eq!(displayed(&app).name, "auto-approve");
}
