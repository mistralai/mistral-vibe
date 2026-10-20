use super::*;
use crate::core::HarnessSettings;

mod direct;
mod orchestration;
mod programmatic;

const PERMISSION_DENIED_REASON: &str = "Tool call denied by permission policy.";
const APPROVAL_REJECTED_REASON: &str = "Tool call was rejected during approval.";

fn permission_denied_failure() -> Value {
    permission_failure(PERMISSION_DENIED_REASON)
}

fn direct_permission_result_committed(
    turn_id: &str,
    call_id: &str,
    arguments: Value,
    result: Value,
) -> Value {
    json!({
        "type": "tool_result_committed",
        "turn_id": turn_id,
        "action_id": crate::core::testing::tool_action_id("top_level", call_id),
        "call_id": call_id,
        "call": {
            "type": "runtime_builtin",
            "name": "file_system.read_file",
            "arguments": arguments,
        },
        "result": result,
    })
}

fn approval_rejected_failure() -> Value {
    permission_failure(APPROVAL_REJECTED_REASON)
}

fn permission_failure(reason: &str) -> Value {
    json!({
        "type": "failure",
        "content": [{"type": "text", "text": reason}],
        "error": {
            "code": "permission_denied",
            "message": reason,
            "retryable": false,
            "details": null,
        },
    })
}

fn settings_with_read_file_permission_policy(
    rule_decision: &str,
    default_decision: &str,
) -> HarnessSettings {
    let mut settings_json = serde_json::to_value(config().settings).unwrap();
    settings_json["tools"]["permissions"] = json!({
        "rules": [{
            "tools": ["file_system.read_file"],
            "decision": rule_decision,
            "allowlist": [],
            "denylist": [],
            "sensitive": [],
        }],
        "default": default_decision,
    });
    serde_json::from_value(settings_json).expect("permission policy settings are valid")
}

fn runtime_with_read_file_permission_policy(
    rule_decision: &str,
    default_decision: &str,
) -> SynchronousRuntime {
    let mut harness_config = config();
    harness_config.settings =
        settings_with_read_file_permission_policy(rule_decision, default_decision);

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

#[test]
fn rejects_permission_policy_replacement_removal_and_addition() {
    let without_policy = config().settings;
    let allow_policy = settings_with_read_file_permission_policy("allow", "deny");
    let deny_policy = settings_with_read_file_permission_policy("deny", "allow");

    for (initial_settings, changed_settings) in [
        (allow_policy.clone(), deny_policy.clone()),
        (allow_policy.clone(), without_policy.clone()),
        (without_policy, allow_policy),
    ] {
        let mut harness_config = config();
        harness_config.settings = initial_settings;
        let mut runtime = SynchronousRuntime::new(harness_config.clone());
        let mut supported_settings = harness_config.settings;
        supported_settings.turn.max_iterations = Some(16);

        runtime.reconfigure_settings(supported_settings.clone(), idle());

        let mut changed_settings = changed_settings;
        changed_settings.turn.max_iterations = supported_settings.turn.max_iterations;
        runtime.reject(
            json!({
                "type": "reconfigure",
                "changes": [{"type": "settings", "value": changed_settings}],
            }),
            json!({
                "code": "invalid_command",
                "error": {
                    "code": "invalid_command",
                    "message": "settings.tools.permissions cannot be reconfigured after session creation",
                    "retryable": false,
                    "details": null,
                },
            }),
        );
    }
}
