use super::*;

fn runtime_with_renamed_direct_tool(rule_decision: &str) -> SynchronousRuntime {
    let mut harness_config = config();
    harness_config.capabilities.tool_groups = serde_json::from_value(json!([{
        "name": "inventory",
        "description": "Inventory tools",
        "tools": [{
            "name": "delete_item",
            "description": "Delete an inventory item",
            "input_schema": {
                "type": "object",
                "properties": {"item_id": {"type": "string"}},
                "required": ["item_id"],
                "additionalProperties": false,
            },
            "exposure": "direct",
            "direct_name": "inventory_delete_item",
        }],
    }]))
    .expect("provided tool configuration is valid");
    let mut config_value = serde_json::to_value(harness_config).unwrap();
    config_value["settings"]["tools"]["permissions"] = json!({
        "rules": [{
            "tools": ["inventory.delete_item"],
            "decision": rule_decision,
            "allowlist": [],
            "denylist": [],
            "sensitive": [],
        }],
        "default": "allow",
    });
    SynchronousRuntime::new(
        serde_json::from_value(config_value).expect("permission policy is valid"),
    )
}

fn delete_item_call() -> Value {
    json!({
        "type": "provided",
        "group_name": "inventory",
        "tool_name": "delete_item",
        "arguments": {"item_id": "A42"},
    })
}

///
/// *Prepare*: A direct tool has the stable ID `inventory.delete_item` and the direct function name `inventory_delete_item`; a rule allows the stable ID.
/// *Do*: The model calls `inventory_delete_item`.
/// *Assert*: Core dispatches the provided tool `inventory.delete_item`.
///
#[test]
fn dispatches_direct_tool_called_by_its_direct_name() {
    // Prepare
    let turn_id = "turn-renamed-direct-tool";
    let mut runtime = runtime_with_renamed_direct_tool("allow");
    let first_completion = runtime.start_turn(turn_id, "Delete item A42.");
    let calls = vec![tool_call(
        "delete-a42",
        "inventory_delete_item",
        json!({"item_id": "A42"}),
    )];

    // Do / Assert
    runtime
        .apply(
            tool_call_completion(action_id(&first_completion), calls.clone()),
            running(turn_id)
                .dispatch(json!({"type": "provided_tool_call", "call": delete_item_call()}))
                .observe(assistant_tool_calls_committed(
                    turn_id,
                    &first_completion,
                    &calls,
                ))
                .observe(tool_execution_started(turn_id, "delete-a42")),
        )
        .only_action();
}

///
/// *Prepare*: A direct tool has the stable ID `inventory.delete_item` and the direct function name `inventory_delete_item`; a rule denies the stable ID.
/// *Do*: The model calls `inventory_delete_item`.
/// *Assert*: Core denies the call without dispatching it and returns the denial under the direct function name.
///
#[test]
fn denies_direct_tool_by_its_stable_id_when_called_by_its_direct_name() {
    // Prepare
    let turn_id = "turn-denied-renamed-direct-tool";
    let mut runtime = runtime_with_renamed_direct_tool("deny");
    let first_completion = runtime.start_turn(turn_id, "Delete item A42.");
    let calls = vec![tool_call(
        "delete-a42",
        "inventory_delete_item",
        json!({"item_id": "A42"}),
    )];
    let failure = permission_denied_failure();

    // Do
    let retry = runtime
        .apply(
            tool_call_completion(action_id(&first_completion), calls.clone()),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(assistant_tool_calls_committed(
                    turn_id,
                    &first_completion,
                    &calls,
                ))
                .observe(tool_execution_started(turn_id, "delete-a42"))
                .observe(json!({
                    "type": "tool_result_committed",
                    "turn_id": turn_id,
                    "action_id": crate::core::testing::tool_action_id("top_level", "delete-a42"),
                    "call_id": "delete-a42",
                    "call": delete_item_call(),
                    "result": failure.clone(),
                }))
                .observe(tool_execution_finished(turn_id, "delete-a42", failure)),
        )
        .only_action();

    // Assert
    runtime.assert_last_model_message_update(
        &retry,
        "append",
        &[
            model_assistant_tool_calls(&calls),
            model_permission_failure(
                "delete-a42",
                "inventory_delete_item",
                PERMISSION_DENIED_REASON,
            ),
        ],
    );
}

///
/// *Prepare*: A direct tool has the stable ID `inventory.delete_item` and the direct function name `inventory_delete_item`; a rule asks for approval on the stable ID.
/// *Do*: The model calls `inventory_delete_item`, the Runtime restarts from the checkpoint, and the user approves.
/// *Assert*: The approval names the direct function and the stable ID, and the approved call dispatches the provided tool.
///
#[test]
fn keeps_approval_for_renamed_direct_tool_usable_after_checkpoint_restore() {
    // Prepare
    let turn_id = "turn-approval-renamed-direct-tool";
    let mut runtime = runtime_with_renamed_direct_tool("ask");
    let first_completion = runtime.start_turn(turn_id, "Delete item A42 if permitted.");
    let calls = vec![tool_call(
        "delete-a42",
        "inventory_delete_item",
        json!({"item_id": "A42"}),
    )];
    let approval_action = runtime
        .apply(
            tool_call_completion(action_id(&first_completion), calls.clone()),
            running(turn_id)
                .dispatch(json!({
                    "type": "approval",
                    "call_id": "delete-a42",
                    "grant_key": "738256e69a3966a65b19b55f58a78e6eb0d524c7b0619f106cdd0bc671dd480c", // gitleaks:allow
                    "tool_id": "inventory.delete_item",
                    "display_name": "inventory_delete_item",
                    "input": {"item_id": "A42"},
                }))
                .observe(assistant_tool_calls_committed(
                    turn_id,
                    &first_completion,
                    &calls,
                ))
                .observe(tool_execution_started(turn_id, "delete-a42")),
        )
        .only_action();

    // Do
    runtime.restart_from_checkpoint();

    // Assert
    runtime
        .apply(
            approve(&approval_action),
            running(turn_id)
                .dispatch(json!({"type": "provided_tool_call", "call": delete_item_call()})),
        )
        .only_action();
}
