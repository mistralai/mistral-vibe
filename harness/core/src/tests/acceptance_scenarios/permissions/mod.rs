use super::*;

mod direct;
mod programmatic;

const PERMISSION_DENIED_REASON: &str = "Tool call denied by permission policy.";

fn permission_denied_failure() -> Value {
    json!({
        "type": "failure",
        "content": [{"type": "text", "text": PERMISSION_DENIED_REASON}],
        "error": {
            "code": "permission_denied",
            "message": PERMISSION_DENIED_REASON,
            "retryable": false,
            "details": null,
        },
    })
}

fn runtime_with_read_file_policy(decision: &str, default: &str) -> SynchronousRuntime {
    let mut config_value = serde_json::to_value(config()).unwrap();
    config_value["settings"]["tools"]["permissions"] = json!({
        "rules": [{
            "tools": ["file_system.read_file"],
            "decision": decision,
            "allowlist": [],
            "denylist": [],
            "sensitive": [],
        }],
        "default": default,
    });
    let harness_config = serde_json::from_value(config_value).expect("permission policy is valid");

    SynchronousRuntime::new(harness_config)
}

fn expected_approval(call_id: &str) -> Value {
    json!({
        "type": "approval",
        "call_id": call_id,
    })
}

fn approval_completed(approval_action: &Value, outcome: &str) -> Value {
    json!({
        "type": "approval_completed",
        "action_id": action_id(approval_action),
        "outcome": outcome,
    })
}

fn approve(approval_action: &Value) -> Value {
    approval_completed(approval_action, "approve")
}

fn reject(approval_action: &Value) -> Value {
    approval_completed(approval_action, "reject")
}
