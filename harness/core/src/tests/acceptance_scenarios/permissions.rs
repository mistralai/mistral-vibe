use super::*;
use crate::core::testing::{ToolOrigin, effect_id_for_operation, typescript_execution_id};

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

fn model_permission_denied(call_id: &str, name: &str) -> Value {
    json!({
        "role": "tool",
        "tool_call_id": call_id,
        "name": name,
        "outcome": "failure",
        "content": [{"type": "text", "text": PERMISSION_DENIED_REASON}],
    })
}

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
fn allows_direct_tool_call_with_runtime_dispatch() {
    let turn_id = "turn-allowed-direct-tool";
    let mut config_value = serde_json::to_value(config()).unwrap();
    config_value["settings"]["tools"]["permissions"] = json!({
        "rules": [{
            "tools": ["file_system.read_file"],
            "decision": "allow",
            "allowlist": [],
            "denylist": [],
            "sensitive": [],
        }],
        "default": "deny",
    });
    let harness_config = serde_json::from_value(config_value).expect("permission policy is valid");
    let mut runtime = SynchronousRuntime::new(harness_config);
    let first_completion = runtime.start_turn(turn_id, "Read this text file.");
    let calls = vec![tool_call(
        "allowed-read",
        "read_file",
        json!({"path": "public.txt"}),
    )];

    runtime
        .apply(
            tool_call_completion(action_id(&first_completion), calls.clone()),
            running(turn_id)
                .dispatch(runtime_tool(
                    "file_system.read_file",
                    json!({"path": "public.txt"}),
                ))
                .observe(assistant_tool_calls_committed(
                    turn_id,
                    &first_completion,
                    &calls,
                ))
                .observe(tool_execution_started(turn_id, "allowed-read")),
        )
        .only_action();
}

#[test]
fn denies_direct_tool_call_with_model_visible_failure_without_runtime_dispatch() {
    let turn_id = "turn-denied-direct-tool";
    let mut config_value = serde_json::to_value(config()).unwrap();
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
    let first_completion = runtime.start_turn(turn_id, "Read this text file if you can.");
    let calls = vec![tool_call(
        "denied-read",
        "read_file",
        json!({"path": "secrets.txt"}),
    )];
    let failure = permission_denied_failure();

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
                .observe(tool_execution_started(turn_id, "denied-read"))
                .observe(tool_execution_finished(turn_id, "denied-read", failure)),
        )
        .only_action();

    runtime.assert_last_model_message_update(
        &retry,
        "append",
        &[
            model_assistant_tool_calls(&calls),
            model_permission_denied("denied-read", "read_file"),
        ],
    );
}

#[test]
fn skips_post_tool_hook_for_denied_direct_tool_call() {
    let turn_id = "turn-denied-direct-tool-with-post-hook";
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
    let first_completion = runtime.start_turn(turn_id, "Read this text file if you can.");
    let calls = vec![tool_call(
        "denied-read-with-post-hook",
        "read_file",
        json!({"path": "secrets.txt"}),
    )];
    let failure = permission_denied_failure();

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
                .observe(tool_execution_started(
                    turn_id,
                    "denied-read-with-post-hook",
                ))
                .observe(tool_execution_finished(
                    turn_id,
                    "denied-read-with-post-hook",
                    failure,
                )),
        )
        .only_action();

    runtime.assert_last_model_message_update(
        &retry,
        "append",
        &[
            model_assistant_tool_calls(&calls),
            model_permission_denied("denied-read-with-post-hook", "read_file"),
        ],
    );
}

#[test]
fn fails_closed_for_direct_tool_call_requiring_approval() {
    let turn_id = "turn-approval-required-direct-tool";
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
    let first_completion = runtime.start_turn(turn_id, "Read this text file if permitted.");
    let calls = vec![tool_call(
        "approval-required-read",
        "read_file",
        json!({"path": "secrets.txt"}),
    )];
    let failure = permission_denied_failure();

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
                .observe(tool_execution_started(turn_id, "approval-required-read"))
                .observe(tool_execution_finished(
                    turn_id,
                    "approval-required-read",
                    failure,
                )),
        )
        .only_action();

    runtime.assert_last_model_message_update(
        &retry,
        "append",
        &[
            model_assistant_tool_calls(&calls),
            model_permission_denied("approval-required-read", "read_file"),
        ],
    );
}

#[test]
fn denies_direct_tool_call_when_pre_hook_supplies_denied_arguments() {
    let turn_id = "turn-pre-hook-denied-direct-tool";
    let mut config_value = serde_json::to_value(config()).unwrap();
    config_value["capabilities"]["hook_bindings"] = json!([
        {
            "id": "rewrite-read-path",
            "point": "pre_tool_call",
            "order": 0,
            "selector": {"type": "always"},
        },
        {
            "id": "observe-read-result",
            "point": "post_tool_call",
            "order": 1,
            "selector": {"type": "always"},
        },
    ]);
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
    let calls = vec![tool_call(
        "rewritten-read",
        "read_file",
        json!({"path": "public.txt"}),
    )];

    let pre_tool = runtime
        .apply(
            tool_call_completion(action_id(&first_completion), calls.clone()),
            running(turn_id)
                .dispatch(hook_call(
                    "pre_tool_call",
                    &["rewrite-read-path"],
                    json!({
                        "tool_call": {
                            "call_id": "rewritten-read",
                            "call": {
                                "type": "runtime_builtin",
                                "name": "file_system.read_file",
                                "arguments": {"path": "public.txt"},
                            },
                        },
                    }),
                ))
                .observe(assistant_tool_calls_committed(
                    turn_id,
                    &first_completion,
                    &calls,
                ))
                .observe(tool_execution_started(turn_id, "rewritten-read")),
        )
        .only_action();

    let failure = permission_denied_failure();
    let retry = runtime
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
                .observe(tool_execution_finished(turn_id, "rewritten-read", failure)),
        )
        .only_action();

    runtime.assert_last_model_message_update(
        &retry,
        "append",
        &[
            model_assistant_tool_calls(&calls),
            model_permission_denied("rewritten-read", "read_file"),
        ],
    );
}

#[test]
fn fails_closed_when_pre_hook_arguments_require_approval() {
    let turn_id = "turn-pre-hook-approval-required-direct-tool";
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
    let calls = vec![tool_call(
        "approval-required-rewritten-read",
        "read_file",
        json!({"path": "public.txt"}),
    )];

    let pre_tool = runtime
        .apply(
            tool_call_completion(action_id(&first_completion), calls.clone()),
            running(turn_id)
                .dispatch(hook_call(
                    "pre_tool_call",
                    &["rewrite-read-path"],
                    json!({
                        "tool_call": {
                            "call_id": "approval-required-rewritten-read",
                            "call": {
                                "type": "runtime_builtin",
                                "name": "file_system.read_file",
                                "arguments": {"path": "public.txt"},
                            },
                        },
                    }),
                ))
                .observe(assistant_tool_calls_committed(
                    turn_id,
                    &first_completion,
                    &calls,
                ))
                .observe(tool_execution_started(
                    turn_id,
                    "approval-required-rewritten-read",
                )),
        )
        .only_action();

    let failure = permission_denied_failure();
    let retry = runtime
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
                .observe(tool_execution_finished(
                    turn_id,
                    "approval-required-rewritten-read",
                    failure,
                )),
        )
        .only_action();

    runtime.assert_last_model_message_update(
        &retry,
        "append",
        &[
            model_assistant_tool_calls(&calls),
            model_permission_denied("approval-required-rewritten-read", "read_file"),
        ],
    );
}

#[test]
fn resolves_allowed_and_denied_direct_tool_calls_independently() {
    let turn_id = "turn-mixed-permission-direct-tools";
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
    let first_completion = runtime.start_turn(turn_id, "Read both text files.");
    let calls = vec![
        tool_call(
            "denied-batch-read",
            "read_file",
            json!({"path": "secrets.txt"}),
        ),
        tool_call(
            "allowed-batch-read",
            "read_file",
            json!({"path": "public.txt"}),
        ),
    ];
    let failure = permission_denied_failure();

    let allowed_action = runtime
        .apply(
            tool_call_completion(action_id(&first_completion), calls.clone()),
            running(turn_id)
                .dispatch(runtime_tool(
                    "file_system.read_file",
                    json!({"path": "public.txt"}),
                ))
                .observe(assistant_tool_calls_committed(
                    turn_id,
                    &first_completion,
                    &calls,
                ))
                .observe(tool_execution_started(turn_id, "denied-batch-read"))
                .observe(tool_execution_started(turn_id, "allowed-batch-read"))
                .observe(tool_execution_finished(
                    turn_id,
                    "denied-batch-read",
                    failure,
                )),
        )
        .only_action();

    let allowed_result = text_tool_success(&allowed_action, "public contents");
    let retry = runtime
        .apply(
            allowed_result.clone(),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe_completed_tool_result(&allowed_action, &allowed_result),
        )
        .only_action();

    runtime.assert_last_model_message_update(
        &retry,
        "append",
        &[
            model_assistant_tool_calls(&calls),
            model_permission_denied("denied-batch-read", "read_file"),
            model_tool_text("allowed-batch-read", "read_file", "public contents"),
        ],
    );
}

#[test]
fn allows_programmatic_tool_call_with_runtime_dispatch() {
    let turn_id = "turn-allowed-programmatic-tool";
    let call_id = "program-allowed-read";
    let source =
        "async function main() { return tools.file_system.read_file({ path: 'public.txt' }); }";
    let mut config_value = serde_json::to_value(config()).unwrap();
    config_value["settings"]["tools"]["permissions"] = json!({
        "rules": [{
            "tools": ["file_system.read_file"],
            "decision": "allow",
            "allowlist": [],
            "denylist": [],
            "sensitive": [],
        }],
        "default": "deny",
    });
    let harness_config = serde_json::from_value(config_value).expect("permission policy is valid");
    let mut runtime = SynchronousRuntime::new(harness_config);
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
fn rejects_programmatic_tool_call_with_permission_denied() {
    let turn_id = "turn-denied-programmatic-tool";
    let call_id = "program-denied-read";
    let source = "async function main() { try { await tools.file_system.read_file({ path: 'secrets.txt' }); return 'unexpected'; } catch (error) { return { code: error.name, reason: error.message }; } }";
    let mut config_value = serde_json::to_value(config()).unwrap();
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
    let mut config_value = serde_json::to_value(config()).unwrap();
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
    let mut config_value = serde_json::to_value(config()).unwrap();
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

#[test]
fn fails_closed_for_programmatic_tool_call_requiring_approval() {
    let turn_id = "turn-approval-required-programmatic-tool";
    let call_id = "program-approval-required-read";
    let source = "async function main() { try { await tools.file_system.read_file({ path: 'secrets.txt' }); return 'unexpected'; } catch (error) { return { code: error.name, reason: error.message }; } }";
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
    let first_completion = runtime.start_turn(turn_id, "Read the file if permission allows it.");

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
