use super::*;
use crate::core::testing::{ToolOrigin, effect_id_for_operation, typescript_execution_id};

fn programmatic_permission_denial_committed(
    turn_id: &str,
    parent_call_id: &str,
    round: u32,
    operation_index: u32,
) -> Value {
    let operation_id = format!(
        "{}:tool:{operation_index}",
        typescript_execution_id(3, parent_call_id, round)
    );
    let action = json!({
        "action_id": effect_id_for_operation(ToolOrigin::Programmatic, &operation_id),
        "call_id": operation_id,
    });
    tool_result_committed(turn_id, &action, permission_denied_failure())
}

#[test]
fn allows_programmatic_tool_call_with_runtime_dispatch() {
    let turn_id = "turn-allowed-programmatic-tool";
    let call_id = "program-allowed-read";
    let source =
        "async function main() { return tools.file_system.read_file({ path: 'public.txt' }); }";
    let mut runtime = runtime_with_read_file_policy("allow", "deny");
    let first_completion = runtime.start_turn(turn_id, "Read the public file.");

    runtime
        .complete_with_typescript_program(
            turn_id,
            &first_completion,
            call_id,
            source,
            [runtime_tool(
                "file_system.read_file",
                json!({"path": "public.txt"}),
            )],
        )
        .only_action();
}

#[test]
fn restores_pending_programmatic_approval_and_continues_after_approval() {
    let turn_id = "turn-approval-required-programmatic-process";
    let call_id = "program-approval-required-process";
    let source = "async function main() { return tools.process.start({ command: 'sleep 10' }); }";
    let mut config_value = serde_json::to_value(config()).unwrap();
    config_value["settings"]["tools"]["background_processes"] = json!({"mode": "enabled"});
    config_value["settings"]["tools"]["permissions"] = json!({
        "rules": [{
            "tools": ["process.start"],
            "decision": "ask",
            "allowlist": [],
            "denylist": [],
            "sensitive": [],
        }],
        "default": "allow",
    });
    let harness_config = serde_json::from_value(config_value).expect("permission policy is valid");
    let mut runtime = SynchronousRuntime::new(harness_config);
    let first_completion = runtime.start_turn(turn_id, "Start the process if permitted.");
    let operation_id = format!("{}:tool:0", typescript_execution_id(3, call_id, 0));

    let approval = runtime
        .apply(
            run_typescript_completion(action_id(&first_completion), call_id, source),
            running(turn_id)
                .dispatch(json!({
                    "type": "approval",
                    "call_id": operation_id,
                    "grant_key": "2ca49405e588555bcc25454a0491655ed8cfb5ddd7c76d416e823d31a29745e5", // gitleaks:allow
                    "tool_id": "process.start",
                    "display_name": "tools.process.start",
                    "input": {
                        "command": "sleep 10",
                    },
                }))
                .observe(tool_execution_started(turn_id, call_id)),
        )
        .only_action();

    runtime.restart_from_checkpoint();
    runtime.receive_steering(turn_id, "Continue after approval.", &[&approval]);

    runtime
        .apply(
            approval_completed(&approval, "approve"),
            running(turn_id).dispatch(runtime_tool(
                "process.start",
                json!({"command": "sleep 10"}),
            )),
        )
        .only_action();
}

#[test]
fn continues_programmatic_tool_call_after_approval() {
    let turn_id = "turn-approved-programmatic-process";
    let call_id = "program-approved-process";
    let source = "async function main() { return tools.process.start({ command: 'sleep 10' }); }";
    let mut config_value = serde_json::to_value(config()).unwrap();
    config_value["settings"]["tools"]["background_processes"] = json!({"mode": "enabled"});
    config_value["settings"]["tools"]["permissions"] = json!({
        "rules": [{
            "tools": ["process.start"],
            "decision": "ask",
            "allowlist": [],
            "denylist": [],
            "sensitive": [],
        }],
        "default": "allow",
    });
    let harness_config = serde_json::from_value(config_value).expect("permission policy is valid");
    let mut runtime = SynchronousRuntime::new(harness_config);
    let first_completion = runtime.start_turn(turn_id, "Start the process if permitted.");
    let operation_id = format!("{}:tool:0", typescript_execution_id(3, call_id, 0));
    let approval = runtime
        .complete_with_typescript_program(
            turn_id,
            &first_completion,
            call_id,
            source,
            [expected_approval(&operation_id)],
        )
        .only_action();

    runtime
        .apply(
            approval_completed(&approval, "approve"),
            running(turn_id).dispatch(runtime_tool(
                "process.start",
                json!({"command": "sleep 10"}),
            )),
        )
        .only_action();
}

#[test]
fn does_not_run_post_tool_hook_when_programmatic_tool_approval_is_rejected() {
    let turn_id = "turn-rejected-programmatic-approval";
    let call_id = "program-rejected-approval";
    let source = "async function main() { try { await tools.file_system.read_file({ path: 'secrets.txt' }); return 'unexpected'; } catch (error) { return { code: error.name, reason: error.message }; } }";
    let mut config_value = serde_json::to_value(config()).unwrap();
    config_value["capabilities"]["hook_bindings"] = json!([{
        "id": "observe-read-result",
        "point": "post_tool_call",
        "order": 0,
        "selector": {"type": "always"},
    }]);
    config_value["settings"]["tools"]["permissions"] = json!({
        "rules": [{
            "tools": ["file_system.read_file"],
            "decision": "ask",
            "allowlist": [],
            "denylist": [],
            "sensitive": [],
        }],
        "default": "allow",
    });
    let harness_config = serde_json::from_value(config_value).expect("permission policy is valid");
    let mut runtime = SynchronousRuntime::new(harness_config);
    let first_completion = runtime.start_turn(turn_id, "Read the secret file if permitted.");
    let operation_id = format!("{}:tool:0", typescript_execution_id(3, call_id, 0));
    let approval = runtime
        .complete_with_typescript_program(
            turn_id,
            &first_completion,
            call_id,
            source,
            [expected_approval(&operation_id)],
        )
        .only_action();
    let denial = json!({
        "code": "permission_denied",
        "reason": PERMISSION_DENIED_REASON,
    });
    let model_denial = denial.to_string();
    let outer_result = run_typescript_success(denial);

    let next_completion = runtime
        .apply(
            approval_completed(&approval, "reject"),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(programmatic_permission_denial_committed(
                    turn_id, call_id, 0, 0,
                ))
                .observe(tool_execution_finished(turn_id, call_id, outer_result)),
        )
        .only_action();

    runtime.assert_last_model_message_update(
        &next_completion,
        "append",
        &[
            model_assistant_typescript(call_id, source),
            model_tool_text(call_id, "run_typescript", &model_denial),
        ],
    );
}

#[test]
fn does_not_run_post_tool_hook_when_programmatic_tool_approval_times_out() {
    let turn_id = "turn-timed-out-programmatic-approval";
    let call_id = "program-timed-out-approval";
    let source = "async function main() { try { await tools.file_system.read_file({ path: 'secrets.txt' }); return 'unexpected'; } catch (error) { return { code: error.name, reason: error.message }; } }";
    let mut config_value = serde_json::to_value(config()).unwrap();
    config_value["capabilities"]["hook_bindings"] = json!([{
        "id": "observe-read-result",
        "point": "post_tool_call",
        "order": 0,
        "selector": {"type": "always"},
    }]);
    config_value["settings"]["tools"]["permissions"] = json!({
        "rules": [{
            "tools": ["file_system.read_file"],
            "decision": "ask",
            "allowlist": [],
            "denylist": [],
            "sensitive": [],
        }],
        "default": "allow",
    });
    let harness_config = serde_json::from_value(config_value).expect("permission policy is valid");
    let mut runtime = SynchronousRuntime::new(harness_config);
    let first_completion = runtime.start_turn(turn_id, "Read the secret file if permitted.");
    let operation_id = format!("{}:tool:0", typescript_execution_id(3, call_id, 0));
    let operation = json!({
        "action_id": effect_id_for_operation(ToolOrigin::Programmatic, &operation_id),
        "call_id": operation_id,
    });
    let approval = runtime
        .complete_with_typescript_program(
            turn_id,
            &first_completion,
            call_id,
            source,
            [expected_approval(&operation_id)],
        )
        .only_action();
    let failure = json!({
        "type": "failure",
        "content": [{"type": "text", "text": "Tool approval timed out."}],
        "error": {
            "code": "approval_failed",
            "message": "Tool approval timed out.",
            "retryable": false,
            "details": null,
        },
    });
    let program_result = json!({
        "code": "approval_failed",
        "reason": "Tool approval timed out.",
    });
    let model_result = program_result.to_string();
    let outer_result = run_typescript_success(program_result);

    let next_completion = runtime
        .apply(
            json!({
                "type": "approval_failed",
                "action_id": action_id(&approval),
                "reason": "timeout",
            }),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(tool_result_committed(turn_id, &operation, failure))
                .observe(tool_execution_finished(turn_id, call_id, outer_result)),
        )
        .only_action();

    runtime.assert_last_model_message_update(
        &next_completion,
        "append",
        &[
            model_assistant_typescript(call_id, source),
            model_tool_text(call_id, "run_typescript", &model_result),
        ],
    );
}

#[test]
fn runs_post_tool_hook_after_programmatic_approval() {
    let turn_id = "turn-approved-programmatic-tool-with-post-hook";
    let call_id = "program-approved-read-with-post-hook";
    let source = "async function main() { return (await tools.file_system.read_file({ path: 'secrets.txt' })).content; }";
    let mut config_value = serde_json::to_value(config()).unwrap();
    config_value["capabilities"]["hook_bindings"] = json!([{
        "id": "replace-read-result",
        "point": "post_tool_call",
        "order": 0,
        "selector": {"type": "always"},
    }]);
    config_value["settings"]["tools"]["permissions"] = json!({
        "rules": [{
            "tools": ["file_system.read_file"],
            "decision": "ask",
            "allowlist": [],
            "denylist": [],
            "sensitive": [],
        }],
        "default": "allow",
    });
    let harness_config = serde_json::from_value(config_value).expect("permission policy is valid");
    let mut runtime = SynchronousRuntime::new(harness_config);
    let first_completion = runtime.start_turn(turn_id, "Read the secret file if permitted.");
    let operation_id = format!("{}:tool:0", typescript_execution_id(3, call_id, 0));
    let approval = runtime
        .complete_with_typescript_program(
            turn_id,
            &first_completion,
            call_id,
            source,
            [expected_approval(&operation_id)],
        )
        .only_action();
    let tool_action = runtime
        .apply(
            approval_completed(&approval, "approve"),
            running(turn_id).dispatch(runtime_tool(
                "file_system.read_file",
                json!({"path": "secrets.txt"}),
            )),
        )
        .only_action();
    let tool_result = file_read_success(&tool_action, "raw contents");
    let post_tool = runtime
        .apply(
            tool_result.clone(),
            running(turn_id).dispatch(hook_call(
                "post_tool_call",
                &["replace-read-result"],
                json!({
                    "tool_call": {
                        "call_id": operation_id,
                        "call": {
                            "type": "runtime_builtin",
                            "name": "file_system.read_file",
                            "arguments": {"path": "secrets.txt"},
                        },
                    },
                    "tool_result": tool_result["result"],
                }),
            )),
        )
        .only_action();
    let hooked_result = json!({
        "type": "success",
        "content": [],
        "structured_content": {
            "path": "secrets.txt",
            "content": "hooked contents",
            "file_size_bytes": 15,
        },
    });
    let outer_result = run_typescript_success(json!("hooked contents"));

    runtime
        .apply(
            hook_completed(
                &post_tool,
                "post_tool_call",
                json!({"tool_result": hooked_result.clone()}),
            ),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(tool_result_committed(turn_id, &tool_action, hooked_result))
                .observe(tool_execution_finished(turn_id, call_id, outer_result)),
        )
        .only_action();
}

#[test]
fn resolves_identical_programmatic_tool_approvals_independently() {
    let turn_id = "turn-parallel-identical-programmatic-approvals";
    let call_id = "program-parallel-identical-approvals";
    let source = r#"
async function main() {
    const results = await Promise.allSettled([
        tools.file_system.read_file({ path: "shared.txt" }),
        tools.file_system.read_file({ path: "shared.txt" }),
    ]);

    return results.map((result) => result.status === "fulfilled"
        ? result.value.content
        : result.reason.name);
}
"#;
    let mut config_value = serde_json::to_value(config()).unwrap();
    config_value["settings"]["tools"]["permissions"] = json!({
        "rules": [{
            "tools": ["file_system.read_file"],
            "decision": "ask",
            "allowlist": [],
            "denylist": [],
            "sensitive": [],
        }],
        "default": "allow",
    });
    let harness_config = serde_json::from_value(config_value).expect("permission policy is valid");
    let mut runtime = SynchronousRuntime::new(harness_config);
    let first_completion = runtime.start_turn(turn_id, "Read the same file twice if permitted.");
    let first_operation_id = format!("{}:tool:0", typescript_execution_id(3, call_id, 0));
    let second_operation_id = format!("{}:tool:1", typescript_execution_id(3, call_id, 0));
    let approvals = runtime
        .complete_with_typescript_program(
            turn_id,
            &first_completion,
            call_id,
            source,
            [
                expected_approval(&first_operation_id),
                expected_approval(&second_operation_id),
            ],
        )
        .actions();
    let [first_approval, second_approval]: [Value; 2] = approvals
        .try_into()
        .expect("two identical calls require separate approvals");

    assert_eq!(first_approval["grant_key"], second_approval["grant_key"]);
    assert_ne!(action_id(&first_approval), action_id(&second_approval));

    let first_tool = runtime
        .apply(
            approval_completed(&first_approval, "approve"),
            running(turn_id)
                .dispatch(runtime_tool(
                    "file_system.read_file",
                    json!({"path": "shared.txt"}),
                ))
                .keep(&second_approval),
        )
        .only_action();
    let first_result = file_read_success(&first_tool, "approved contents");
    runtime.apply(
        first_result.clone(),
        running(turn_id)
            .keep(&second_approval)
            .observe_tool_result(&first_tool, &first_result),
    );

    let program_result = json!(["approved contents", "permission_denied"]);
    let model_result = program_result.to_string();
    let next_completion = runtime
        .apply(
            approval_completed(&second_approval, "reject"),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(programmatic_permission_denial_committed(
                    turn_id, call_id, 0, 1,
                ))
                .observe(tool_execution_finished(
                    turn_id,
                    call_id,
                    run_typescript_success(program_result),
                )),
        )
        .only_action();

    runtime.assert_last_model_message_update(
        &next_completion,
        "append",
        &[
            model_assistant_typescript(call_id, source),
            model_tool_text(call_id, "run_typescript", &model_result),
        ],
    );
}

#[test]
fn keeps_programmatic_tool_approval_usable_after_steering() {
    let turn_id = "turn-steered-programmatic-approval";
    let call_id = "program-steered-approval";
    let source = "async function main() { return tools.process.start({ command: 'sleep 10' }); }";
    let mut config_value = serde_json::to_value(config()).unwrap();
    config_value["settings"]["tools"]["background_processes"] = json!({"mode": "enabled"});
    config_value["settings"]["tools"]["permissions"] = json!({
        "rules": [{
            "tools": ["process.start"],
            "decision": "ask",
            "allowlist": [],
            "denylist": [],
            "sensitive": [],
        }],
        "default": "allow",
    });
    let harness_config = serde_json::from_value(config_value).expect("permission policy is valid");
    let mut runtime = SynchronousRuntime::new(harness_config);
    let first_completion = runtime.start_turn(turn_id, "Start the process if permitted.");
    let operation_id = format!("{}:tool:0", typescript_execution_id(3, call_id, 0));
    let approval = runtime
        .complete_with_typescript_program(
            turn_id,
            &first_completion,
            call_id,
            source,
            [expected_approval(&operation_id)],
        )
        .only_action();

    runtime.receive_steering(
        turn_id,
        "Use the process output in the final answer.",
        &[&approval],
    );

    runtime
        .apply(
            approval_completed(&approval, "approve"),
            running(turn_id).dispatch(runtime_tool(
                "process.start",
                json!({"command": "sleep 10"}),
            )),
        )
        .only_action();
}

#[test]
fn interrupt_abandons_all_pending_programmatic_approvals() {
    let turn_id = "turn-interrupted-programmatic-approvals";
    let call_id = "program-interrupted-approvals";
    let reason = "user stopped the turn";
    let source = r#"
async function main() {
    return Promise.all([
        tools.file_system.read_file({ path: "first.txt" }),
        tools.file_system.read_file({ path: "second.txt" }),
    ]);
}
"#;
    let mut config_value = serde_json::to_value(config()).unwrap();
    config_value["settings"]["tools"]["permissions"] = json!({
        "rules": [{
            "tools": ["file_system.read_file"],
            "decision": "ask",
            "allowlist": [],
            "denylist": [],
            "sensitive": [],
        }],
        "default": "allow",
    });
    let harness_config = serde_json::from_value(config_value).expect("permission policy is valid");
    let mut runtime = SynchronousRuntime::new(harness_config);
    let first_completion = runtime.start_turn(turn_id, "Read both files if permitted.");
    let first_operation_id = format!("{}:tool:0", typescript_execution_id(3, call_id, 0));
    let second_operation_id = format!("{}:tool:1", typescript_execution_id(3, call_id, 0));
    let approvals = runtime
        .complete_with_typescript_program(
            turn_id,
            &first_completion,
            call_id,
            source,
            [
                expected_approval(&first_operation_id),
                expected_approval(&second_operation_id),
            ],
        )
        .actions();
    let [first_approval, second_approval]: [Value; 2] = approvals
        .try_into()
        .expect("two programmatic approvals are pending");

    runtime.apply(
        json!({
            "type": "interrupt",
            "expected_turn_id": turn_id,
            "reason": reason,
        }),
        interrupted(turn_id, reason)
            .observe(turn_interrupted(turn_id, reason))
            .observe(action_abandoned(turn_id, &first_approval, "interrupt"))
            .observe(action_abandoned(turn_id, &second_approval, "interrupt")),
    );
}

#[test]
fn rejects_tool_result_targeting_programmatic_expected_approval() {
    let turn_id = "turn-tool-result-for-programmatic-approval";
    let call_id = "program-tool-result-for-approval";
    let source =
        "async function main() { return tools.file_system.read_file({ path: 'secrets.txt' }); }";
    let mut config_value = serde_json::to_value(config()).unwrap();
    config_value["settings"]["tools"]["permissions"] = json!({
        "rules": [{
            "tools": ["file_system.read_file"],
            "decision": "ask",
            "allowlist": [],
            "denylist": [],
            "sensitive": [],
        }],
        "default": "allow",
    });
    let harness_config = serde_json::from_value(config_value).expect("permission policy is valid");
    let mut runtime = SynchronousRuntime::new(harness_config);
    let first_completion = runtime.start_turn(turn_id, "Read the file if permitted.");
    let operation_id = format!("{}:tool:0", typescript_execution_id(3, call_id, 0));
    let approval = runtime
        .complete_with_typescript_program(
            turn_id,
            &first_completion,
            call_id,
            source,
            [expected_approval(&operation_id)],
        )
        .only_action();

    runtime.reject(
        text_tool_success(&approval, "unexpected contents"),
        json!({
            "code": "invalid_state",
            "command_type": "tool_succeeded",
            "state": "running",
        }),
    );

    runtime
        .apply(
            approval_completed(&approval, "approve"),
            running(turn_id).dispatch(runtime_tool(
                "file_system.read_file",
                json!({"path": "secrets.txt"}),
            )),
        )
        .only_action();
}

#[test]
fn rejects_approval_result_targeting_programmatic_tool_action() {
    let turn_id = "turn-approval-result-for-programmatic-tool";
    let call_id = "program-approval-result-for-tool";
    let source = "async function main() { return (await tools.file_system.read_file({ path: 'secrets.txt' })).content; }";
    let mut config_value = serde_json::to_value(config()).unwrap();
    config_value["settings"]["tools"]["permissions"] = json!({
        "rules": [{
            "tools": ["file_system.read_file"],
            "decision": "ask",
            "allowlist": [],
            "denylist": [],
            "sensitive": [],
        }],
        "default": "allow",
    });
    let harness_config = serde_json::from_value(config_value).expect("permission policy is valid");
    let mut runtime = SynchronousRuntime::new(harness_config);
    let first_completion = runtime.start_turn(turn_id, "Read the file if permitted.");
    let operation_id = format!("{}:tool:0", typescript_execution_id(3, call_id, 0));
    let approval = runtime
        .complete_with_typescript_program(
            turn_id,
            &first_completion,
            call_id,
            source,
            [expected_approval(&operation_id)],
        )
        .only_action();
    let tool_action = runtime
        .apply(
            approval_completed(&approval, "approve"),
            running(turn_id).dispatch(runtime_tool(
                "file_system.read_file",
                json!({"path": "secrets.txt"}),
            )),
        )
        .only_action();

    runtime.reject(
        approval_completed(&tool_action, "approve"),
        json!({
            "code": "invalid_command",
            "error": {
                "code": "invalid_command",
                "message": format!(
                    "pending approval action {:?} was not found",
                    action_id(&tool_action),
                ),
                "retryable": false,
                "details": null,
            },
        }),
    );

    let tool_result = file_read_success(&tool_action, "file contents");
    runtime
        .apply(
            tool_result.clone(),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe_tool_result(&tool_action, &tool_result)
                .observe(tool_execution_finished(
                    turn_id,
                    call_id,
                    run_typescript_success(json!("file contents")),
                )),
        )
        .only_action();
}

#[test]
fn rejects_repeated_programmatic_approval_result_without_redispatching_tool() {
    let turn_id = "turn-repeated-programmatic-approval-result";
    let call_id = "program-repeated-approval-result";
    let source = "async function main() { return (await tools.file_system.read_file({ path: 'secrets.txt' })).content; }";
    let mut config_value = serde_json::to_value(config()).unwrap();
    config_value["settings"]["tools"]["permissions"] = json!({
        "rules": [{
            "tools": ["file_system.read_file"],
            "decision": "ask",
            "allowlist": [],
            "denylist": [],
            "sensitive": [],
        }],
        "default": "allow",
    });
    let harness_config = serde_json::from_value(config_value).expect("permission policy is valid");
    let mut runtime = SynchronousRuntime::new(harness_config);
    let first_completion = runtime.start_turn(turn_id, "Read the file if permitted.");
    let operation_id = format!("{}:tool:0", typescript_execution_id(3, call_id, 0));
    let approval = runtime
        .complete_with_typescript_program(
            turn_id,
            &first_completion,
            call_id,
            source,
            [expected_approval(&operation_id)],
        )
        .only_action();
    let approval_result = approval_completed(&approval, "approve");
    let tool_action = runtime
        .apply(
            approval_result.clone(),
            running(turn_id).dispatch(runtime_tool(
                "file_system.read_file",
                json!({"path": "secrets.txt"}),
            )),
        )
        .only_action();

    runtime.reject(
        approval_result,
        json!({
            "code": "invalid_correlation",
            "received_action_id": action_id(&approval),
            "pending_action_ids": [action_id(&tool_action)],
        }),
    );

    let tool_result = file_read_success(&tool_action, "file contents");
    runtime
        .apply(
            tool_result.clone(),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe_tool_result(&tool_action, &tool_result)
                .observe(tool_execution_finished(
                    turn_id,
                    call_id,
                    run_typescript_success(json!("file contents")),
                )),
        )
        .only_action();
}

#[test]
fn uses_model_visible_name_for_programmatic_provided_tool_approval() {
    let turn_id = "turn-approval-required-programmatic-provided-tool";
    let call_id = "program-approval-required-create-issue";
    let source = "async function main() { return tools.github.create_issue({ title: 'Bug' }); }";
    let mut harness_config = config();
    harness_config.capabilities.tool_groups = serde_json::from_value(json!([{
        "name": "github",
        "description": "GitHub tools",
        "tools": [{
            "name": "create_issue",
            "description": "Create a GitHub issue",
            "input_schema": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
                "additionalProperties": false,
            },
            "exposure": "programmatic",
        }],
    }]))
    .expect("provided tool configuration is valid");
    let mut config_value = serde_json::to_value(harness_config).unwrap();
    config_value["settings"]["tools"]["permissions"] = json!({
        "rules": [{
            "tools": ["github.create_issue"],
            "decision": "ask",
            "allowlist": [],
            "denylist": [],
            "sensitive": [],
        }],
        "default": "allow",
    });
    let harness_config = serde_json::from_value(config_value).expect("permission policy is valid");
    let mut runtime = SynchronousRuntime::new(harness_config);
    let first_completion = runtime.start_turn(turn_id, "Create the issue if permitted.");
    let operation_id = format!("{}:tool:0", typescript_execution_id(3, call_id, 0));

    runtime
        .complete_with_typescript_program(
            turn_id,
            &first_completion,
            call_id,
            source,
            [json!({
                "type": "approval",
                "call_id": operation_id,
                "tool_id": "github.create_issue",
                "display_name": "tools.github.create_issue",
                "input": {"title": "Bug"},
            })],
        )
        .only_action();
}

#[test]
fn rejects_programmatic_tool_call_with_permission_denied() {
    let turn_id = "turn-denied-programmatic-tool";
    let call_id = "program-denied-read";
    let source = "async function main() { try { await tools.file_system.read_file({ path: 'secrets.txt' }); return 'unexpected'; } catch (error) { return { code: error.name, reason: error.message }; } }";
    let mut runtime = runtime_with_read_file_policy("deny", "allow");
    let first_completion = runtime.start_turn(turn_id, "Read the secret file.");

    let denial = json!({
        "code": "permission_denied",
        "reason": PERMISSION_DENIED_REASON,
    });
    let model_denial = denial.to_string();
    let outer_result = run_typescript_success(denial);
    let next_completion = runtime
        .apply(
            run_typescript_completion(action_id(&first_completion), call_id, source),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(tool_execution_started(turn_id, call_id))
                .observe(programmatic_permission_denial_committed(
                    turn_id, call_id, 0, 0,
                ))
                .observe(tool_execution_finished(turn_id, call_id, outer_result)),
        )
        .only_action();

    runtime.assert_last_model_message_update(
        &next_completion,
        "append",
        &[
            model_assistant_typescript(call_id, source),
            model_tool_text(call_id, "run_typescript", &model_denial),
        ],
    );
}

#[test]
fn completes_many_sequential_denied_programmatic_tool_calls() {
    let turn_id = "turn-many-denied-programmatic-tools";
    let call_id = "program-many-denied-reads";
    let source = r#"
async function main() {
    let denialCount = 0;

    for (let index = 0; index < 100; index += 1) {
        try {
            await tools.file_system.read_file({ path: "secrets.txt" });
        } catch (error) {
            if (error.name !== "permission_denied") throw error;
            denialCount += 1;
        }
    }

    return denialCount;
}
"#;
    let mut runtime = runtime_with_read_file_policy("deny", "allow");
    let first_completion = runtime.start_turn(turn_id, "Try every read.");

    let outer_result = run_typescript_success(json!(100));
    let expected = (0..100).fold(
        running(turn_id)
            .dispatch(llm_call(1))
            .observe(tool_execution_started(turn_id, call_id)),
        |expected, index| {
            expected.observe(programmatic_permission_denial_committed(
                turn_id, call_id, index, index,
            ))
        },
    );
    let next_completion = runtime
        .apply(
            run_typescript_completion(action_id(&first_completion), call_id, source),
            expected.observe(tool_execution_finished(turn_id, call_id, outer_result)),
        )
        .only_action();

    runtime.assert_last_model_message_update(
        &next_completion,
        "append",
        &[
            model_assistant_typescript(call_id, source),
            model_tool_text(call_id, "run_typescript", "100"),
        ],
    );
}

#[test]
fn skips_post_tool_hook_for_denied_programmatic_tool_call() {
    let turn_id = "turn-denied-programmatic-tool-with-post-hook";
    let call_id = "program-denied-read-with-post-hook";
    let source = "async function main() { try { await tools.file_system.read_file({ path: 'secrets.txt' }); return 'unexpected'; } catch (error) { return error.name; } }";
    let mut config_value = serde_json::to_value(config()).unwrap();
    config_value["capabilities"]["hook_bindings"] = json!([{
        "id": "observe-read-result",
        "point": "post_tool_call",
        "order": 0,
        "selector": {"type": "always"},
    }]);
    config_value["settings"]["tools"]["permissions"] = json!({
        "rules": [{
            "tools": ["file_system.read_file"],
            "decision": "deny",
            "allowlist": [],
            "denylist": [],
            "sensitive": [],
        }],
        "default": "allow",
    });
    let harness_config = serde_json::from_value(config_value).expect("permission policy is valid");
    let mut runtime = SynchronousRuntime::new(harness_config);
    let first_completion = runtime.start_turn(turn_id, "Read the secret file.");

    let outer_result = run_typescript_success(json!("permission_denied"));
    let next_completion = runtime
        .apply(
            run_typescript_completion(action_id(&first_completion), call_id, source),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(tool_execution_started(turn_id, call_id))
                .observe(programmatic_permission_denial_committed(
                    turn_id, call_id, 0, 0,
                ))
                .observe(tool_execution_finished(turn_id, call_id, outer_result)),
        )
        .only_action();

    runtime.assert_last_model_message_update(
        &next_completion,
        "append",
        &[
            model_assistant_typescript(call_id, source),
            model_tool_text(call_id, "run_typescript", r#""permission_denied""#),
        ],
    );
}

#[test]
fn uses_arguments_rewritten_by_pre_tool_hook_for_programmatic_approval_and_dispatch() {
    let turn_id = "turn-pre-hook-approval-required-programmatic-tool";
    let call_id = "program-approval-required-rewritten-read";
    let source =
        "async function main() { return tools.file_system.read_file({ path: 'public.txt' }); }";
    let mut config_value = serde_json::to_value(config()).unwrap();
    config_value["capabilities"]["hook_bindings"] = json!([{
        "id": "rewrite-read-path",
        "point": "pre_tool_call",
        "order": 0,
        "selector": {"type": "always"},
    }]);
    config_value["settings"]["tools"]["permissions"] = json!({
        "rules": [{
            "tools": ["file_system.read_file"],
            "decision": "allow",
            "allowlist": [],
            "denylist": [],
            "sensitive": ["*secrets.txt*"],
        }],
        "default": "allow",
    });
    let harness_config = serde_json::from_value(config_value).expect("permission policy is valid");
    let mut runtime = SynchronousRuntime::new(harness_config);
    let first_completion = runtime.start_turn(turn_id, "Read the public file.");
    let operation_id = format!("{}:tool:0", typescript_execution_id(3, call_id, 0));

    let pre_tool = runtime
        .complete_with_typescript_program(
            turn_id,
            &first_completion,
            call_id,
            source,
            [hook_call(
                "pre_tool_call",
                &["rewrite-read-path"],
                Value::Null,
            )],
        )
        .only_action();

    runtime.restart_from_checkpoint();

    let approval = runtime
        .apply(
            hook_completed(
                &pre_tool,
                "pre_tool_call",
                json!({
                    "type": "continue",
                    "effective_arguments": {"path": "secrets.txt"},
                }),
            ),
            running(turn_id).dispatch(json!({
                "type": "approval",
                "call_id": operation_id,
                "grant_key": "d0c83402d8ec77621bad96390d436d26f8fc3f3cb3dfa1e655528c411175b9c3", // gitleaks:allow
                "tool_id": "file_system.read_file",
                "display_name": "tools.file_system.read_file",
                "input": {
                    "path": "secrets.txt",
                },
            })),
        )
        .only_action();

    runtime
        .apply(
            approval_completed(&approval, "approve"),
            running(turn_id).dispatch(runtime_tool(
                "file_system.read_file",
                json!({"path": "secrets.txt"}),
            )),
        )
        .only_action();
}

#[test]
fn rejects_programmatic_tool_call_when_pre_hook_supplies_denied_arguments() {
    let turn_id = "turn-pre-hook-denied-programmatic-tool";
    let call_id = "program-rewritten-read";
    let source = "async function main() { try { await tools.file_system.read_file({ path: 'public.txt' }); return 'unexpected'; } catch (error) { return error.name; } }";
    let mut config_value = serde_json::to_value(config()).unwrap();
    config_value["capabilities"]["hook_bindings"] = json!([{
        "id": "rewrite-read-path",
        "point": "pre_tool_call",
        "order": 0,
        "selector": {"type": "always"},
    }]);
    config_value["settings"]["tools"]["permissions"] = json!({
        "rules": [{
            "tools": ["file_system.read_file"],
            "decision": "allow",
            "allowlist": [],
            "denylist": ["*secrets.txt*"],
            "sensitive": [],
        }],
        "default": "allow",
    });
    let harness_config = serde_json::from_value(config_value).expect("permission policy is valid");
    let mut runtime = SynchronousRuntime::new(harness_config);
    let first_completion = runtime.start_turn(turn_id, "Read the public file.");

    let pre_tool = runtime
        .complete_with_typescript_program(
            turn_id,
            &first_completion,
            call_id,
            source,
            [hook_call(
                "pre_tool_call",
                &["rewrite-read-path"],
                Value::Null,
            )],
        )
        .only_action();

    let outer_result = run_typescript_success(json!("permission_denied"));
    let next_completion = runtime
        .apply(
            hook_completed(
                &pre_tool,
                "pre_tool_call",
                json!({
                    "type": "continue",
                    "effective_arguments": {"path": "secrets.txt"},
                }),
            ),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(programmatic_permission_denial_committed(
                    turn_id, call_id, 0, 0,
                ))
                .observe(tool_execution_finished(turn_id, call_id, outer_result)),
        )
        .only_action();

    runtime.assert_last_model_message_update(
        &next_completion,
        "append",
        &[
            model_assistant_typescript(call_id, source),
            model_tool_text(call_id, "run_typescript", r#""permission_denied""#),
        ],
    );
}

#[test]
fn resolves_allowed_and_denied_programmatic_tool_calls_independently() {
    let turn_id = "turn-mixed-programmatic-tools";
    let call_id = "program-mixed-reads";
    let source = r#"
async function main() {
    const paths = ["secrets.txt", "public.txt"];
    const results = await Promise.allSettled(
        paths.map((path) => tools.file_system.read_file({ path })),
    );

    return results.map((result, index) => ({
        path: paths[index],
        outcome: result.status === "fulfilled"
            ? result.value.content
            : result.reason.name,
    }));
}
"#;
    let mut config_value = serde_json::to_value(config()).unwrap();
    config_value["settings"]["tools"]["permissions"] = json!({
        "rules": [{
            "tools": ["file_system.read_file"],
            "decision": "allow",
            "allowlist": [],
            "denylist": ["*secrets.txt*"],
            "sensitive": [],
        }],
        "default": "deny",
    });
    let harness_config = serde_json::from_value(config_value).expect("permission policy is valid");
    let mut runtime = SynchronousRuntime::new(harness_config);
    let first_completion = runtime.start_turn(turn_id, "Read every accessible file.");

    let allowed_tool = runtime
        .apply(
            run_typescript_completion(action_id(&first_completion), call_id, source),
            running(turn_id)
                .dispatch(runtime_tool(
                    "file_system.read_file",
                    json!({"path": "public.txt"}),
                ))
                .observe(tool_execution_started(turn_id, call_id))
                .observe(programmatic_permission_denial_committed(
                    turn_id, call_id, 0, 0,
                )),
        )
        .only_action();

    let program_result = json!([
        {"path": "secrets.txt", "outcome": "permission_denied"},
        {"path": "public.txt", "outcome": "public contents"},
    ]);
    let model_result = program_result.to_string();
    let outer_result = run_typescript_success(program_result);
    let allowed_result = file_read_success(&allowed_tool, "public contents");
    let expected = running(turn_id)
        .dispatch(llm_call(1))
        .observe_tool_result(&allowed_tool, &allowed_result)
        .observe_tool_execution_finished(call_id, outer_result);

    let next_completion = runtime.apply(allowed_result, expected).only_action();

    runtime.assert_last_model_message_update(
        &next_completion,
        "append",
        &[
            model_assistant_typescript(call_id, source),
            model_tool_text(call_id, "run_typescript", &model_result),
        ],
    );
}
#[test]
fn returns_each_parallel_programmatic_denial_to_typescript() {
    let turn_id = "turn-parallel-denied-programmatic-tools";
    let call_id = "program-parallel-denied-reads";
    let source = r#"
async function main() {
    const paths = ["secrets.txt", "credentials.txt"];
    const results = await Promise.allSettled(
        paths.map((path) => tools.file_system.read_file({ path })),
    );

    return results.map((result) =>
        result.status === "rejected" ? result.reason.name : "unexpected"
    );
}
"#;
    let mut runtime = runtime_with_read_file_policy("deny", "allow");
    let first_completion = runtime.start_turn(turn_id, "Read every file you can.");

    let program_result = json!(["permission_denied", "permission_denied"]);
    let model_result = program_result.to_string();
    let outer_result = run_typescript_success(program_result);
    let next_completion = runtime
        .apply(
            run_typescript_completion(action_id(&first_completion), call_id, source),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(tool_execution_started(turn_id, call_id))
                .observe(programmatic_permission_denial_committed(
                    turn_id, call_id, 0, 0,
                ))
                .observe(programmatic_permission_denial_committed(
                    turn_id, call_id, 0, 1,
                ))
                .observe(tool_execution_finished(turn_id, call_id, outer_result)),
        )
        .only_action();

    runtime.assert_last_model_message_update(
        &next_completion,
        "append",
        &[
            model_assistant_typescript(call_id, source),
            model_tool_text(call_id, "run_typescript", &model_result),
        ],
    );
}
