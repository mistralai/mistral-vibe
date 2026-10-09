use super::*;

fn model_permission_denied(call_id: &str, name: &str) -> Value {
    json!({
        "role": "tool",
        "tool_call_id": call_id,
        "name": name,
        "outcome": "failure",
        "content": [{"type": "text", "text": PERMISSION_DENIED_REASON}],
    })
}

#[test]
fn allows_direct_tool_call_with_runtime_dispatch() {
    let turn_id = "turn-allowed-direct-tool";
    let mut runtime = runtime_with_read_file_policy("allow", "deny");
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
    let mut runtime = runtime_with_read_file_policy("deny", "allow");
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
fn keeps_pending_approval_for_direct_tool_call_usable_after_checkpoint_restore() {
    let turn_id = "turn-approval-required-direct-tool";
    let mut runtime = runtime_with_read_file_policy("ask", "allow");
    let first_completion = runtime.start_turn(turn_id, "Read this text file if permitted.");
    let calls = vec![tool_call(
        "approval-required-read",
        "read_file",
        json!({"path": "secrets.txt"}),
    )];

    let approval_action = runtime
        .apply(
            tool_call_completion(action_id(&first_completion), calls.clone()),
            running(turn_id)
                .dispatch(json!({
                    "type": "approval",
                    "call_id": "approval-required-read",
                    "grant_key": "7895770ace3385dbcd849d8e6100fd169aace83163408417bf4d62f95a72bdbb", // gitleaks:allow
                    "tool_id": "file_system.read_file",
                    "display_name": "read_file",
                    "input": {
                        "path": "secrets.txt",
                    },
                }))
                .observe(assistant_tool_calls_committed(
                    turn_id,
                    &first_completion,
                    &calls,
                ))
                .observe(tool_execution_started(turn_id, "approval-required-read")),
        )
        .only_action();

    runtime.restart_from_checkpoint();

    runtime
        .apply(
            approve(&approval_action),
            running(turn_id).dispatch(runtime_tool(
                "file_system.read_file",
                json!({"path": "secrets.txt"}),
            )),
        )
        .only_action();
}

#[test]
fn runs_post_tool_hook_after_approved_direct_tool_call_completes() {
    let turn_id = "turn-approve-once-direct-tool";
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
    let first_completion = runtime.start_turn(turn_id, "Read this text file if permitted.");
    let calls = vec![tool_call(
        "approved-read",
        "read_file",
        json!({"path": "secrets.txt"}),
    )];
    let approval_action = runtime
        .complete_with_tool_calls(
            turn_id,
            &first_completion,
            &calls,
            [expected_approval("approved-read")],
        )
        .only_action();

    let tool_action = runtime
        .apply(
            approve(&approval_action),
            running(turn_id).dispatch(runtime_tool(
                "file_system.read_file",
                json!({"path": "secrets.txt"}),
            )),
        )
        .only_action();
    let tool_result = text_tool_success(&tool_action, "approved file contents");

    runtime
        .apply(
            tool_result.clone(),
            running(turn_id).dispatch(hook_call(
                "post_tool_call",
                &["observe-read-result"],
                json!({
                    "tool_call": {
                        "call_id": "approved-read",
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
}

#[test]
fn resolves_identical_direct_tool_approvals_independently() {
    let turn_id = "turn-parallel-identical-direct-approvals";
    let mut runtime = runtime_with_read_file_policy("ask", "allow");
    let first_completion = runtime.start_turn(turn_id, "Read the same text file twice.");
    let calls = vec![
        tool_call(
            "first-identical-read",
            "read_file",
            json!({"path": "shared.txt"}),
        ),
        tool_call(
            "second-identical-read",
            "read_file",
            json!({"path": "shared.txt"}),
        ),
    ];
    let approval_actions = runtime
        .complete_with_tool_calls(
            turn_id,
            &first_completion,
            &calls,
            [
                expected_approval("first-identical-read"),
                expected_approval("second-identical-read"),
            ],
        )
        .actions();
    let [first_approval_action, second_approval_action]: [Value; 2] = approval_actions
        .try_into()
        .expect("two identical calls require separate approvals");

    assert_eq!(
        first_approval_action["grant_key"],
        second_approval_action["grant_key"]
    );
    assert_ne!(
        action_id(&first_approval_action),
        action_id(&second_approval_action)
    );

    let first_tool = runtime
        .apply(
            approve(&first_approval_action),
            running(turn_id)
                .dispatch(runtime_tool(
                    "file_system.read_file",
                    json!({"path": "shared.txt"}),
                ))
                .keep(&second_approval_action),
        )
        .only_action();

    runtime
        .apply(
            approve(&second_approval_action),
            running(turn_id).keep(&first_tool).dispatch(runtime_tool(
                "file_system.read_file",
                json!({"path": "shared.txt"}),
            )),
        )
        .only_action();
}

#[test]
fn keeps_direct_tool_approval_usable_after_steering() {
    let turn_id = "turn-steered-direct-approval";
    let mut runtime = runtime_with_read_file_policy("ask", "allow");
    let first_completion = runtime.start_turn(turn_id, "Read this text file if permitted.");
    let calls = vec![tool_call(
        "steered-approval-read",
        "read_file",
        json!({"path": "secrets.txt"}),
    )];
    let approval_action = runtime
        .complete_with_tool_calls(
            turn_id,
            &first_completion,
            &calls,
            [expected_approval("steered-approval-read")],
        )
        .only_action();

    runtime.receive_steering(
        turn_id,
        "Use the contents in the final answer.",
        &[&approval_action],
    );

    runtime
        .apply(
            approve(&approval_action),
            running(turn_id).dispatch(runtime_tool(
                "file_system.read_file",
                json!({"path": "secrets.txt"}),
            )),
        )
        .only_action();
}

#[test]
fn interrupt_abandons_all_pending_direct_approvals() {
    let turn_id = "turn-interrupted-direct-approvals";
    let reason = "user stopped the turn";
    let mut runtime = runtime_with_read_file_policy("ask", "allow");
    let first_completion = runtime.start_turn(turn_id, "Read both files if permitted.");
    let calls = vec![
        tool_call(
            "first-interrupted-approval",
            "read_file",
            json!({"path": "first.txt"}),
        ),
        tool_call(
            "second-interrupted-approval",
            "read_file",
            json!({"path": "second.txt"}),
        ),
    ];
    let approval_actions = runtime
        .complete_with_tool_calls(
            turn_id,
            &first_completion,
            &calls,
            [
                expected_approval("first-interrupted-approval"),
                expected_approval("second-interrupted-approval"),
            ],
        )
        .actions();
    let [first_approval_action, second_approval_action]: [Value; 2] = approval_actions
        .try_into()
        .expect("two direct approvals are pending");

    runtime.apply(
        json!({
            "type": "interrupt",
            "expected_turn_id": turn_id,
            "reason": reason,
        }),
        interrupted(turn_id, reason)
            .observe(turn_interrupted(turn_id, reason))
            .observe(action_abandoned(
                turn_id,
                &first_approval_action,
                "interrupt",
            ))
            .observe(action_abandoned(
                turn_id,
                &second_approval_action,
                "interrupt",
            )),
    );
}

#[test]
fn rejects_tool_result_targeting_direct_approval_action() {
    let turn_id = "turn-tool-result-for-direct-approval";
    let mut runtime = runtime_with_read_file_policy("ask", "allow");
    let first_completion = runtime.start_turn(turn_id, "Read this text file if permitted.");
    let calls = vec![tool_call(
        "approval-pending-read",
        "read_file",
        json!({"path": "secrets.txt"}),
    )];
    let approval_action = runtime
        .complete_with_tool_calls(
            turn_id,
            &first_completion,
            &calls,
            [expected_approval("approval-pending-read")],
        )
        .only_action();

    runtime.reject(
        text_tool_success(&approval_action, "unexpected contents"),
        json!({
            "code": "invalid_state",
            "command_type": "tool_succeeded",
            "state": "running",
        }),
    );

    runtime
        .apply(
            approve(&approval_action),
            running(turn_id).dispatch(runtime_tool(
                "file_system.read_file",
                json!({"path": "secrets.txt"}),
            )),
        )
        .only_action();
}

#[test]
fn rejects_approval_result_targeting_direct_tool_action() {
    let turn_id = "turn-approval-result-for-direct-tool";
    let mut runtime = runtime_with_read_file_policy("ask", "allow");
    let first_completion = runtime.start_turn(turn_id, "Read this text file if permitted.");
    let calls = vec![tool_call(
        "tool-pending-read",
        "read_file",
        json!({"path": "secrets.txt"}),
    )];
    let approval_action = runtime
        .complete_with_tool_calls(
            turn_id,
            &first_completion,
            &calls,
            [expected_approval("tool-pending-read")],
        )
        .only_action();
    let tool_action = runtime
        .apply(
            approve(&approval_action),
            running(turn_id).dispatch(runtime_tool(
                "file_system.read_file",
                json!({"path": "secrets.txt"}),
            )),
        )
        .only_action();

    runtime.reject(
        approve(&tool_action),
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

    let tool_result = text_tool_success(&tool_action, "file contents");
    runtime
        .apply(
            tool_result.clone(),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe_completed_tool_result(&tool_action, &tool_result),
        )
        .only_action();
}

#[test]
fn rejects_repeated_direct_approval_result_without_redispatching_tool() {
    let turn_id = "turn-repeated-direct-approval-result";
    let mut runtime = runtime_with_read_file_policy("ask", "allow");
    let first_completion = runtime.start_turn(turn_id, "Read this text file if permitted.");
    let calls = vec![tool_call(
        "replayed-approval-read",
        "read_file",
        json!({"path": "secrets.txt"}),
    )];
    let approval_action = runtime
        .complete_with_tool_calls(
            turn_id,
            &first_completion,
            &calls,
            [expected_approval("replayed-approval-read")],
        )
        .only_action();
    let approval_command = approve(&approval_action);
    let tool_action = runtime
        .apply(
            approval_command.clone(),
            running(turn_id).dispatch(runtime_tool(
                "file_system.read_file",
                json!({"path": "secrets.txt"}),
            )),
        )
        .only_action();

    runtime.reject(
        approval_command,
        json!({
            "code": "invalid_correlation",
            "received_action_id": action_id(&approval_action),
            "pending_action_ids": [action_id(&tool_action)],
        }),
    );
}

#[test]
fn rejects_direct_tool_approval_without_running_post_tool_hook() {
    let turn_id = "turn-rejected-approval-direct-tool";
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
    let first_completion = runtime.start_turn(turn_id, "Read this text file if permitted.");
    let calls = vec![tool_call(
        "rejected-read",
        "read_file",
        json!({"path": "secrets.txt"}),
    )];
    let approval_action = runtime
        .complete_with_tool_calls(
            turn_id,
            &first_completion,
            &calls,
            [expected_approval("rejected-read")],
        )
        .only_action();
    let failure = permission_denied_failure();

    let retry = runtime
        .apply(
            reject(&approval_action),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(tool_execution_finished(turn_id, "rejected-read", failure)),
        )
        .only_action();

    runtime.assert_last_model_message_update(
        &retry,
        "append",
        &[
            model_assistant_tool_calls(&calls),
            model_permission_denied("rejected-read", "read_file"),
        ],
    );
}

#[test]
fn times_out_direct_tool_approval_without_running_post_tool_hook() {
    let turn_id = "turn-timed-out-approval-direct-tool";
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
    let first_completion = runtime.start_turn(turn_id, "Read this text file if permitted.");
    let calls = vec![tool_call(
        "timed-out-read",
        "read_file",
        json!({"path": "secrets.txt"}),
    )];
    let approval_action = runtime
        .complete_with_tool_calls(
            turn_id,
            &first_completion,
            &calls,
            [expected_approval("timed-out-read")],
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

    let retry = runtime
        .apply(
            json!({
                "type": "approval_failed",
                "action_id": action_id(&approval_action),
                "reason": "timeout",
            }),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(tool_execution_finished(turn_id, "timed-out-read", failure)),
        )
        .only_action();

    runtime.assert_last_model_message_update(
        &retry,
        "append",
        &[
            model_assistant_tool_calls(&calls),
            json!({
                "role": "tool",
                "tool_call_id": "timed-out-read",
                "name": "read_file",
                "outcome": "failure",
                "content": [{"type": "text", "text": "Tool approval timed out."}],
            }),
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
fn uses_arguments_rewritten_by_pre_tool_hook_for_approval_and_dispatch() {
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

    let approval_action = runtime
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
                "call_id": "approval-required-rewritten-read",
                "grant_key": "d0c83402d8ec77621bad96390d436d26f8fc3f3cb3dfa1e655528c411175b9c3", // gitleaks:allow
                "tool_id": "file_system.read_file",
                "display_name": "read_file",
                "input": {
                    "path": "secrets.txt",
                },
            })),
        )
        .only_action();

    runtime
        .apply(
            approve(&approval_action),
            running(turn_id).dispatch(runtime_tool(
                "file_system.read_file",
                json!({"path": "secrets.txt"}),
            )),
        )
        .only_action();
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
