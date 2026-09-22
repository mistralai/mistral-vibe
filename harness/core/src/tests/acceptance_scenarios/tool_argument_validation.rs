use super::*;

const SCHEMA_ERROR_PREFIX: &str = "tool arguments do not satisfy the declared input schema: ";

fn schema_error(detail: &str) -> String {
    format!("{SCHEMA_ERROR_PREFIX}{detail}")
}

fn assert_exact_appended_model_input(action: &Value, messages: &[Value]) {
    assert_eq!(
        action["model_input"],
        json!({
            "messages": {
                "type": "append",
                "base_revision": 1,
                "revision": 2,
                "messages": messages,
            },
            "tool_catalog": {"type": "keep", "revision": 0},
        })
    );
}

fn assert_exact_replaced_model_input(action: &Value, messages: &[Value], tools: &Value) {
    assert_eq!(
        action["model_input"],
        json!({
            "messages": {
                "type": "replace",
                "revision": 1,
                "messages": messages,
            },
            "tool_catalog": {
                "type": "replace",
                "revision": 0,
                "tools": tools,
            },
        })
    );
}

///
/// *Prepare*: A direct provided tool declares a required string argument.
/// *Do*: The model calls it with a number instead.
/// *Assert*: Core emits only the retry model Action and appends the exact MCP-shaped failure without dispatching the provided tool.
///
#[test]
fn invalid_direct_provided_tool_arguments_return_to_model_context_without_dispatch() {
    // Prepare
    let turn_id = "turn-invalid-provided-tool";
    let mut harness_config = config();
    harness_config.capabilities.tool_groups = serde_json::from_value(json!([{
        "name": "customers",
        "description": "Customer tools",
        "tools": [{
            "name": "select_customer",
            "description": "Select one customer",
            "input_schema": {
                "type": "object",
                "properties": {"customer_id": {"type": "string"}},
                "required": ["customer_id"],
                "additionalProperties": false,
            },
            "exposure": "direct",
        }],
    }]))
    .expect("provided tool configuration is valid");
    let mut runtime = SynchronousRuntime::new(harness_config);
    let first_completion = runtime.start_turn(turn_id, "select the customer");
    let calls = vec![tool_call(
        "invalid-provided-call",
        "select_customer",
        json!({"customer_id": 7}),
    )];

    // Do
    let error = schema_error("7 is not of type \"string\"");
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
                .observe(tool_execution_started(turn_id, "invalid-provided-call"))
                .observe(tool_execution_finished(
                    turn_id,
                    "invalid-provided-call",
                    invalid_tool_call_result(&error),
                )),
        )
        .only_action();

    // Assert
    let expected_messages = [
        model_assistant_tool_calls(&calls),
        model_invalid_tool_call("invalid-provided-call", "select_customer", &error),
    ];
    runtime.assert_last_model_message_update(&retry, "append", &expected_messages);
    assert_exact_appended_model_input(&retry, &expected_messages);
}

///
/// *Prepare*: A turn has the direct Runtime-owned `read_file` tool.
/// *Do*: The model supplies a numeric path that violates the compiled filesystem schema.
/// *Assert*: Core emits only the retry model Action and never dispatches a Runtime built-in Action.
///
#[test]
fn invalid_direct_runtime_builtin_arguments_return_to_model_context_without_dispatch() {
    // Prepare
    let turn_id = "turn-invalid-runtime-tool";
    let mut runtime = SynchronousRuntime::new(config());
    let first_completion = runtime.start_turn(turn_id, "read the file");
    let calls = vec![tool_call(
        "invalid-runtime-call",
        "read_file",
        json!({"path": 7}),
    )];

    // Do
    let error = schema_error("7 is not of type \"string\"");
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
                .observe(tool_execution_started(turn_id, "invalid-runtime-call"))
                .observe(tool_execution_finished(
                    turn_id,
                    "invalid-runtime-call",
                    invalid_tool_call_result(&error),
                )),
        )
        .only_action();

    // Assert
    let expected_messages = [
        model_assistant_tool_calls(&calls),
        model_invalid_tool_call("invalid-runtime-call", "read_file", &error),
    ];
    runtime.assert_last_model_message_update(&retry, "append", &expected_messages);
    assert_exact_appended_model_input(&retry, &expected_messages);
}

///
/// *Prepare*: A matching pre-tool hook is configured for filesystem reads.
/// *Do*: The model calls `read_file` with schema-invalid arguments.
/// *Assert*: Validation completes the call as a model-visible failure before Core emits any hook or Runtime tool Action.
///
#[test]
fn invalid_direct_call_does_not_enter_a_matching_pre_tool_hook() {
    // Prepare
    let turn_id = "turn-invalid-hooked-tool";
    let mut harness_config = config();
    harness_config.capabilities.hook_bindings = serde_json::from_value(json!([{
        "id": "observe-file-reads",
        "point": "pre_tool_call",
        "order": 0,
        "selector": {
            "type": "tool_keys",
            "tool_keys": [{
                "target": "filesystem",
                "qualified_name": "file_system.read_file",
            }],
        },
    }]))
    .expect("filesystem hook configuration is valid");
    let mut runtime = SynchronousRuntime::new(harness_config);
    let first_completion = runtime.start_turn(turn_id, "read through the hook");
    let calls = vec![tool_call(
        "invalid-hooked-call",
        "read_file",
        json!({"path": 7}),
    )];

    // Do
    let error = schema_error("7 is not of type \"string\"");
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
                .observe(tool_execution_started(turn_id, "invalid-hooked-call"))
                .observe(tool_execution_finished(
                    turn_id,
                    "invalid-hooked-call",
                    invalid_tool_call_result(&error),
                )),
        )
        .only_action();

    // Assert
    let expected_messages = [
        model_assistant_tool_calls(&calls),
        model_invalid_tool_call("invalid-hooked-call", "read_file", &error),
    ];
    runtime.assert_last_model_message_update(&retry, "append", &expected_messages);
    assert_exact_appended_model_input(&retry, &expected_messages);
}

///
/// *Prepare*: One model completion contains an invalid filesystem call followed by a valid filesystem call.
/// *Do*: Core dispatches only the valid call, checkpoints with both execution states, restores, and accepts the valid result.
/// *Assert*: The restored batch requests the next model completion with both tool results in original call order and commits only the executed result.
///
#[test]
fn mixed_valid_and_invalid_batch_dispatches_valid_work_and_preserves_result_order() {
    // Prepare
    let turn_id = "turn-mixed-tool-validation";
    let mut runtime = SynchronousRuntime::new(config());
    let first_completion = runtime.start_turn(turn_id, "read both files");
    let calls = vec![
        tool_call("invalid-batch-call", "read_file", json!({"path": 7})),
        tool_call(
            "valid-batch-call",
            "read_file",
            json!({"path": "valid.txt"}),
        ),
    ];
    let invalid_error = schema_error("7 is not of type \"string\"");
    let valid_action = runtime
        .apply(
            tool_call_completion(action_id(&first_completion), calls.clone()),
            running(turn_id)
                .dispatch(runtime_tool(
                    "file_system.read_file",
                    json!({"path": "valid.txt"}),
                ))
                .observe(assistant_tool_calls_committed(
                    turn_id,
                    &first_completion,
                    &calls,
                ))
                .observe(tool_execution_started(turn_id, "invalid-batch-call"))
                .observe(tool_execution_started(turn_id, "valid-batch-call"))
                .observe(tool_execution_finished(
                    turn_id,
                    "invalid-batch-call",
                    invalid_tool_call_result(&invalid_error),
                )),
        )
        .only_action();
    runtime.restart_from_checkpoint();
    let valid_result = file_read_success(&valid_action, "valid contents");

    // Do
    let retry = runtime
        .apply(
            valid_result.clone(),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe_completed_tool_result(&valid_action, &valid_result),
        )
        .only_action();

    // Assert
    let expected_messages = [
        first_completion["model_input"]["messages"]["messages"][0].clone(),
        model_user_text("read both files"),
        model_assistant_tool_calls(&calls),
        model_invalid_tool_call("invalid-batch-call", "read_file", &invalid_error),
        model_tool_text(
            "valid-batch-call",
            "read_file",
            &valid_result["result"]["structured_content"].to_string(),
        ),
    ];
    runtime.assert_last_model_message_update(&retry, "replace", &expected_messages);
    assert_exact_replaced_model_input(
        &retry,
        &expected_messages,
        &first_completion["model_input"]["tool_catalog"]["tools"],
    );
}
