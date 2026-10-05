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
