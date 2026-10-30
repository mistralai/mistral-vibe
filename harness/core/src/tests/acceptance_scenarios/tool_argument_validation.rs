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
    let error = schema_error("at /customer_id: 7 is not of type \"string\"");
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
/// *Prepare*: A turn has the direct Runtime-owned `read_file` tool, which rejects unknown fields.
/// *Do*: The model calls it with `file_path` instead of `path`.
/// *Assert*: The error lists the fields the tool accepts and names the one the model most likely meant.
///
#[test]
fn unknown_tool_argument_names_the_allowed_fields_and_the_likely_one() {
    // Prepare
    let turn_id = "turn-unknown-argument";
    let mut runtime = SynchronousRuntime::new(config());
    let first_completion = runtime.start_turn(turn_id, "read the file");
    let calls = vec![tool_call(
        "unknown-argument-call",
        "read_file",
        json!({"file_path": "notes.txt"}),
    )];

    // Do
    let error = schema_error(
        "Additional properties are not allowed ('file_path' was unexpected). Allowed fields: path, offset, limit. Use \"path\" instead of \"file_path\".",
    );
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
                .observe(tool_execution_started(turn_id, "unknown-argument-call"))
                .observe(tool_execution_finished(
                    turn_id,
                    "unknown-argument-call",
                    invalid_tool_call_result(&error),
                )),
        )
        .only_action();

    // Assert
    let expected_messages = [
        model_assistant_tool_calls(&calls),
        model_invalid_tool_call("unknown-argument-call", "read_file", &error),
    ];
    runtime.assert_last_model_message_update(&retry, "append", &expected_messages);
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
    let error = schema_error("at /path: 7 is not of type \"string\"");
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
    let error = schema_error("at /path: 7 is not of type \"string\"");
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
    let invalid_error = schema_error("at /path: 7 is not of type \"string\"");
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

///
/// *Prepare*: A direct provided tool takes no arguments.
/// *Do*: The model calls it twice, once with empty arguments and once with only whitespace, then the tools return.
/// *Assert*: Core runs both calls with `{}` and commits and replays `{}` as their arguments.
///
#[test]
fn empty_tool_arguments_run_the_tool_with_an_empty_object() {
    // Prepare
    let turn_id = "turn-empty-tool-arguments";
    let mut harness_config = config();
    harness_config.capabilities.tool_groups = serde_json::from_value(json!([{
        "name": "customers",
        "description": "Customer tools",
        "tools": [{
            "name": "list_customers",
            "description": "List all customers",
            "input_schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": false,
            },
            "exposure": "direct",
        }],
    }]))
    .expect("provided tool configuration is valid");
    let mut runtime = SynchronousRuntime::new(harness_config);
    let first_completion = runtime.start_turn(turn_id, "list the customers");
    let sent_calls = ["", " \n"]
        .iter()
        .zip(["empty-arguments-call", "blank-arguments-call"])
        .map(|(arguments_json, id)| {
            json!({
                "type": "tool_call",
                "id": id,
                "name": "list_customers",
                "arguments_json": arguments_json,
            })
        })
        .collect::<Vec<_>>();
    let committed_calls = vec![
        tool_call("empty-arguments-call", "list_customers", json!({})),
        tool_call("blank-arguments-call", "list_customers", json!({})),
    ];
    let list_customers = json!({
        "type": "provided_tool_call",
        "call": {
            "type": "provided",
            "group_name": "customers",
            "tool_name": "list_customers",
            "arguments": {},
        },
    });

    // Do
    let [first_tool, second_tool]: [Value; 2] = runtime
        .apply(
            tool_call_completion(action_id(&first_completion), sent_calls),
            running(turn_id)
                .dispatch(list_customers.clone())
                .dispatch(list_customers)
                .observe(assistant_tool_calls_committed(
                    turn_id,
                    &first_completion,
                    &committed_calls,
                ))
                .observe(tool_execution_started(turn_id, "empty-arguments-call"))
                .observe(tool_execution_started(turn_id, "blank-arguments-call")),
        )
        .actions()
        .try_into()
        .expect("both calls dispatch the tool");
    let first_result = text_tool_success(&first_tool, "Ada");
    runtime.apply(
        first_result.clone(),
        running(turn_id)
            .keep(&second_tool)
            .observe_completed_tool_result(&first_tool, &first_result),
    );
    let second_result = text_tool_success(&second_tool, "Ada");
    let next_completion = runtime
        .apply(
            second_result.clone(),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe_completed_tool_result(&second_tool, &second_result),
        )
        .only_action();

    // Assert
    runtime.assert_last_model_message_update(
        &next_completion,
        "append",
        &[
            model_assistant_tool_calls(&committed_calls),
            model_tool_text("empty-arguments-call", "list_customers", "Ada"),
            model_tool_text("blank-arguments-call", "list_customers", "Ada"),
        ],
    );
}

///
/// *Prepare*: A session has programmatic tool calling and one discoverable tool.
/// *Do*: The model calls `search_tool_functions` four times with a mode name as an argument name: the query as the value, nested arguments, the function list as the value next to a valid `mode`, and two mode names in one call.
/// *Assert*: Every call fails with the schema error for the first rejected field, followed by the call shape for that mode; no call runs a search.
///
#[test]
fn search_tool_functions_rejects_a_mode_name_used_as_an_argument_name() {
    // Prepare
    let turn_id = "turn-search-mode-as-argument";
    let mut harness_config = config();
    harness_config.capabilities.tool_groups = serde_json::from_value(json!([{
        "name": "github",
        "description": "GitHub tools",
        "tools": [{
            "name": "search_issues",
            "description": "Search GitHub issues",
            "input_schema": {"type": "object"},
            "exposure": "programmatic",
        }],
    }]))
    .expect("tool discovery configuration is valid");
    let mut runtime = SynchronousRuntime::new(harness_config);
    let first_completion = runtime.start_turn(turn_id, "find GitHub issues");
    let calls = vec![
        tool_call(
            "query-as-value",
            "search_tool_functions",
            json!({"best_match": "search issues"}),
        ),
        tool_call(
            "nested-arguments",
            "search_tool_functions",
            json!({"best_match": {"query": "search issues"}}),
        ),
        tool_call(
            "functions-as-value",
            "search_tool_functions",
            json!({"details": ["github.search_issues"], "mode": "details"}),
        ),
        tool_call(
            "two-mode-names",
            "search_tool_functions",
            json!({"all_connector_capabilities": null, "best_match": "issues"}),
        ),
    ];
    let unexpected = |field: &str| {
        schema_error(&format!(
            "Additional properties are not allowed ('{field}' was unexpected). Allowed fields: mode, query, functions, functionNames, connectors."
        ))
    };
    let modes = "\"best_match\", \"details\" and \"all_connector_capabilities\" are values of \"mode\", not argument names.";
    let best_match_error = format!(
        "{} {modes} Call search_tool_functions with {{\"mode\": \"best_match\", \"query\": \"<keywords>\"}}.",
        unexpected("best_match")
    );
    let details_error = format!(
        "{} {modes} Call search_tool_functions with {{\"mode\": \"details\", \"functions\": [\"<connector>.<function>\"]}}.",
        unexpected("details")
    );
    let two_modes_error = format!(
        "{} {modes} Call search_tool_functions with {{\"mode\": \"all_connector_capabilities\", \"connectors\": [\"<connector>\"]}}.",
        unexpected("all_connector_capabilities")
    );

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
                .observe(tool_execution_started(turn_id, "query-as-value"))
                .observe(tool_execution_started(turn_id, "nested-arguments"))
                .observe(tool_execution_started(turn_id, "functions-as-value"))
                .observe(tool_execution_started(turn_id, "two-mode-names"))
                .observe_tool_execution_finished(
                    "query-as-value",
                    invalid_tool_call_result(&best_match_error),
                )
                .observe_tool_execution_finished(
                    "nested-arguments",
                    invalid_tool_call_result(&best_match_error),
                )
                .observe_tool_execution_finished(
                    "functions-as-value",
                    invalid_tool_call_result(&details_error),
                )
                .observe_tool_execution_finished(
                    "two-mode-names",
                    invalid_tool_call_result(&two_modes_error),
                ),
        )
        .only_action();

    // Assert
    let expected_messages = [
        model_assistant_tool_calls(&calls),
        model_invalid_tool_call("query-as-value", "search_tool_functions", &best_match_error),
        model_invalid_tool_call(
            "nested-arguments",
            "search_tool_functions",
            &best_match_error,
        ),
        model_invalid_tool_call(
            "functions-as-value",
            "search_tool_functions",
            &details_error,
        ),
        model_invalid_tool_call("two-mode-names", "search_tool_functions", &two_modes_error),
    ];
    runtime.assert_last_model_message_update(&retry, "append", &expected_messages);
    assert_exact_appended_model_input(&retry, &expected_messages);
}

///
/// *Prepare*: A session has programmatic tool calling enabled.
/// *Do*: The model calls `search_tool_functions` with a valid query and an unknown field that is not a mode name.
/// *Assert*: The call fails with the schema error alone, without a mode hint, and runs no search.
///
#[test]
fn search_tool_functions_rejects_an_unknown_argument() {
    // Prepare
    let turn_id = "turn-search-unknown-argument";
    let mut runtime = SynchronousRuntime::new(config());
    let first_completion = runtime.start_turn(turn_id, "find GitHub issues");
    let calls = vec![tool_call(
        "unknown-search-argument",
        "search_tool_functions",
        json!({"query": "search issues", "limit": 5}),
    )];
    let error = schema_error(
        "Additional properties are not allowed ('limit' was unexpected). Allowed fields: mode, query, functions, functionNames, connectors.",
    );

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
                .observe(tool_execution_started(turn_id, "unknown-search-argument"))
                .observe(tool_execution_finished(
                    turn_id,
                    "unknown-search-argument",
                    invalid_tool_call_result(&error),
                )),
        )
        .only_action();

    // Assert
    let expected_messages = [
        model_assistant_tool_calls(&calls),
        model_invalid_tool_call("unknown-search-argument", "search_tool_functions", &error),
    ];
    runtime.assert_last_model_message_update(&retry, "append", &expected_messages);
    assert_exact_appended_model_input(&retry, &expected_messages);
}
