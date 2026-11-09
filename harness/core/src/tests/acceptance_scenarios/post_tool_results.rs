use super::*;
use crate::core::{ProvidedToolDefinition, ProvidedToolExposure, ToolGroupDefinition};

/// *Prepare*: A post-tool hook is bound, and a direct MCP-style call returns its value as JSON text and as structured content.
/// *Do*: The Runtime restarts from a checkpoint while the hook runs; the hook rewrites the text and returns the original structured content unchanged.
/// *Assert*: Core warns that the two views disagree and commits the result exactly as the hook returned it.
#[test]
fn post_tool_hook_content_only_rewrite_is_kept_and_warns() {
    // Prepare
    let turn_id = "turn-redact-direct";
    let call_id = "lookup";
    let mut runtime = SynchronousRuntime::new(customer_config(&["redact"]));
    let completion = runtime.start_turn(turn_id, "look up the customer");
    let calls = vec![tool_call(call_id, "get_customer", json!({"id": 7}))];
    let lookup = runtime
        .apply(
            tool_call_completion(action_id(&completion), calls.clone()),
            running(turn_id)
                .dispatch(customer_action(json!({"id": 7})))
                .observe(assistant_tool_calls_committed(turn_id, &completion, &calls))
                .observe(tool_execution_started(turn_id, call_id)),
        )
        .only_action();
    let raw_result = mcp_result(json!({"ssn": "123-45-6789", "name": "Ada"}));
    let post_hook = runtime
        .apply(
            tool_succeeded(&lookup, &raw_result),
            running(turn_id).dispatch(hook_call(
                "post_tool_call",
                &["redact"],
                json!({
                    "tool_call": {"call_id": call_id, "call": lookup["call"]},
                    "tool_result": raw_result,
                }),
            )),
        )
        .only_action();

    // Do
    runtime.restart_from_checkpoint();
    let hooked_result = json!({
        "type": "success",
        "content": [{"type": "text", "text": r#"{"ssn":"***","name":"Ada"}"#}],
        "structured_content": raw_result["structured_content"],
    });
    let final_completion = runtime
        .apply(
            hook_completed(
                &post_hook,
                "post_tool_call",
                json!({"tool_result": hooked_result}),
            ),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(inconsistent_result_warning(turn_id, call_id, &["redact"]))
                .observe(tool_result_committed(
                    turn_id,
                    &lookup,
                    hooked_result.clone(),
                ))
                .observe(tool_execution_finished(turn_id, call_id, hooked_result)),
        )
        .only_action();

    // Assert
    runtime.assert_last_model_message_update(
        &final_completion,
        "replace",
        &[
            model_system(),
            model_user_text("look up the customer"),
            model_assistant_tool_calls(&calls),
            model_tool_text(call_id, "get_customer", r#"{"ssn":"***","name":"Ada"}"#),
        ],
    );
    runtime.finish_turn_with_text(turn_id, &final_completion, "redacted");
}

/// *Prepare*: A post-tool hook is bound, and a direct MCP-style call returns its value as JSON text and as structured content.
/// *Do*: The hook appends a note as a new text block and keeps the JSON text and the structured content.
/// *Assert*: Core commits the result as returned, with no warning.
#[test]
fn post_tool_hook_note_after_the_json_text_gets_no_warning() {
    // Prepare
    let turn_id = "turn-note-direct";
    let call_id = "lookup";
    let mut runtime = SynchronousRuntime::new(customer_config(&["note"]));
    let completion = runtime.start_turn(turn_id, "look up the customer");
    let calls = vec![tool_call(call_id, "get_customer", json!({"id": 7}))];
    let lookup = runtime
        .apply(
            tool_call_completion(action_id(&completion), calls.clone()),
            running(turn_id)
                .dispatch(customer_action(json!({"id": 7})))
                .observe(assistant_tool_calls_committed(turn_id, &completion, &calls))
                .observe(tool_execution_started(turn_id, call_id)),
        )
        .only_action();
    let raw_result = mcp_result(json!({"name": "Ada"}));
    let post_hook = runtime
        .apply(
            tool_succeeded(&lookup, &raw_result),
            running(turn_id).dispatch(hook_call(
                "post_tool_call",
                &["note"],
                json!({
                    "tool_call": {"call_id": call_id, "call": lookup["call"]},
                    "tool_result": raw_result,
                }),
            )),
        )
        .only_action();

    // Do
    let noted_result = json!({
        "type": "success",
        "content": [
            raw_result["content"][0],
            {"type": "text", "text": "The customer asked for no calls."},
        ],
        "structured_content": raw_result["structured_content"],
    });
    let final_completion = runtime
        .apply(
            hook_completed(
                &post_hook,
                "post_tool_call",
                json!({"tool_result": noted_result}),
            ),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(tool_result_committed(
                    turn_id,
                    &lookup,
                    noted_result.clone(),
                ))
                .observe(tool_execution_finished(turn_id, call_id, noted_result)),
        )
        .only_action();

    // Assert
    runtime.finish_turn_with_text(turn_id, &final_completion, "noted");
}

/// *Prepare*: Two post-tool hooks are bound, and a direct MCP-style call returns its value as JSON text and as structured content.
/// *Do*: The hooks change the structured content and keep the old JSON text.
/// *Assert*: Core warns, naming both bindings, and commits the result as returned.
#[test]
fn post_tool_hook_structured_only_rewrite_warns_and_names_every_binding() {
    // Prepare
    let turn_id = "turn-rewrite-structured";
    let call_id = "lookup";
    let mut runtime = SynchronousRuntime::new(customer_config(&["mask", "audit"]));
    let completion = runtime.start_turn(turn_id, "look up the customer");
    let calls = vec![tool_call(call_id, "get_customer", json!({"id": 7}))];
    let lookup = runtime
        .apply(
            tool_call_completion(action_id(&completion), calls.clone()),
            running(turn_id)
                .dispatch(customer_action(json!({"id": 7})))
                .observe(assistant_tool_calls_committed(turn_id, &completion, &calls))
                .observe(tool_execution_started(turn_id, call_id)),
        )
        .only_action();
    let raw_result = mcp_result(json!({"ssn": "123-45-6789"}));
    let post_hook = runtime
        .apply(
            tool_succeeded(&lookup, &raw_result),
            running(turn_id).dispatch(hook_call(
                "post_tool_call",
                &["mask", "audit"],
                json!({
                    "tool_call": {"call_id": call_id, "call": lookup["call"]},
                    "tool_result": raw_result,
                }),
            )),
        )
        .only_action();

    // Do
    let hooked_result = json!({
        "type": "success",
        "content": raw_result["content"],
        "structured_content": {"ssn": "***"},
    });
    let final_completion = runtime
        .apply(
            hook_completed(
                &post_hook,
                "post_tool_call",
                json!({"tool_result": hooked_result}),
            ),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(inconsistent_result_warning(
                    turn_id,
                    call_id,
                    &["mask", "audit"],
                ))
                .observe(tool_result_committed(
                    turn_id,
                    &lookup,
                    hooked_result.clone(),
                ))
                .observe(tool_execution_finished(turn_id, call_id, hooked_result)),
        )
        .only_action();

    // Assert
    runtime.finish_turn_with_text(turn_id, &final_completion, "looked up");
}

/// *Prepare*: A post-tool hook is bound, and a program looks up two customers whose MCP-style results hold JSON text and structured content.
/// *Do*: The hook rewrites only the text of the first result, and both the text and the structured content of the second.
/// *Assert*: Core warns only for the first result, and the program reads each structured content as the hook returned it.
#[test]
fn post_tool_hook_results_reach_the_program_as_returned() {
    // Prepare
    let turn_id = "turn-redact-program";
    let call_id = "program";
    let source = r#"async function main() {
      const first = await tools.crm.get_customer({ id: 1 });
      const second = await tools.crm.get_customer({ id: 2 });
      return [first, second];
    }"#;
    let mut runtime = SynchronousRuntime::new(customer_config(&["redact"]));
    let completion = runtime.start_turn(turn_id, "look up both customers");
    let first_lookup = runtime
        .apply(
            run_typescript_completion(action_id(&completion), call_id, source),
            running(turn_id)
                .dispatch(customer_action(json!({"id": 1})))
                .observe(core_tool_execution_started(turn_id, call_id)),
        )
        .only_action();
    let first_raw = mcp_result(json!({"ssn": "111-11-1111"}));
    let first_hook = runtime
        .apply(
            tool_succeeded(&first_lookup, &first_raw),
            running(turn_id).dispatch(hook_call(
                "post_tool_call",
                &["redact"],
                json!({
                    "tool_call": {"call_id": first_lookup["call_id"], "call": first_lookup["call"]},
                    "tool_result": first_raw,
                }),
            )),
        )
        .only_action();
    let first_hooked = json!({
        "type": "success",
        "content": [{"type": "text", "text": r#"{"ssn":"***"}"#}],
        "structured_content": first_raw["structured_content"],
    });

    // Do
    let second_lookup = runtime
        .apply(
            hook_completed(
                &first_hook,
                "post_tool_call",
                json!({"tool_result": first_hooked}),
            ),
            running(turn_id)
                .dispatch(customer_action(json!({"id": 2})))
                .observe(inconsistent_result_warning(
                    turn_id,
                    first_lookup["call_id"].as_str().expect("call id"),
                    &["redact"],
                ))
                .observe(tool_result_committed(
                    turn_id,
                    &first_lookup,
                    first_hooked.clone(),
                )),
        )
        .only_action();
    let second_raw = mcp_result(json!({"ssn": "222-22-2222"}));
    let second_hook = runtime
        .apply(
            tool_succeeded(&second_lookup, &second_raw),
            running(turn_id).dispatch(hook_call(
                "post_tool_call",
                &["redact"],
                json!({
                    "tool_call": {"call_id": second_lookup["call_id"], "call": second_lookup["call"]},
                    "tool_result": second_raw,
                }),
            )),
        )
        .only_action();
    let second_hooked = mcp_result(json!({"ssn": "***"}));
    let program_value = json!([{"ssn": "111-11-1111"}, {"ssn": "***"}]);
    let final_completion = runtime
        .apply(
            hook_completed(
                &second_hook,
                "post_tool_call",
                json!({"tool_result": second_hooked}),
            ),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(tool_result_committed(
                    turn_id,
                    &second_lookup,
                    second_hooked.clone(),
                ))
                .observe_tool_execution_finished(
                    call_id,
                    run_typescript_success(program_value.clone()),
                ),
        )
        .only_action();

    // Assert
    runtime.assert_last_model_message_update(
        &final_completion,
        "append",
        &[
            model_assistant_typescript(call_id, source),
            model_tool_text(call_id, "run_typescript", &program_value.to_string()),
        ],
    );
}

/// *Prepare*: A post-tool hook is bound, and a direct read returns its file only as structured content.
/// *Do*: The hook appends a note to the empty text.
/// *Assert*: The committed result keeps the file in the structured content next to the note, with no warning.
#[test]
fn post_tool_hook_can_append_a_note_to_a_structured_only_result() {
    // Prepare
    let turn_id = "turn-note-read";
    let call_id = "note-read";
    let mut config = config();
    config.capabilities.hook_bindings = serde_json::from_value(json!([
        {"id": "note", "point": "post_tool_call", "order": 0, "selector": {"type": "always"}}
    ]))
    .expect("post-tool hook configures");
    let mut runtime = SynchronousRuntime::new(config);
    let completion = runtime.start_turn(turn_id, "read the guide");
    let calls = vec![tool_call(call_id, "read_file", json!({"path": "guide.md"}))];
    let read = runtime
        .apply(
            tool_call_completion(action_id(&completion), calls.clone()),
            running(turn_id)
                .dispatch(runtime_tool(
                    "file_system.read_file",
                    json!({"path": "guide.md"}),
                ))
                .observe(assistant_tool_calls_committed(turn_id, &completion, &calls))
                .observe(tool_execution_started(turn_id, call_id)),
        )
        .only_action();
    let raw_result = json!({
        "type": "success",
        "content": [],
        "structured_content": read_file_output("guide.md", "the guide"),
    });
    let post_hook = runtime
        .apply(
            tool_succeeded(&read, &raw_result),
            running(turn_id).dispatch(hook_call(
                "post_tool_call",
                &["note"],
                json!({
                    "tool_call": {"call_id": call_id, "call": read["call"]},
                    "tool_result": raw_result,
                }),
            )),
        )
        .only_action();

    // Do
    let noted_result = json!({
        "type": "success",
        "content": [{"type": "text", "text": "<note>read the folder guide</note>"}],
        "structured_content": raw_result["structured_content"],
    });
    let final_completion = runtime
        .apply(
            hook_completed(
                &post_hook,
                "post_tool_call",
                json!({"tool_result": noted_result}),
            ),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(tool_result_committed(turn_id, &read, noted_result.clone()))
                .observe(tool_execution_finished(turn_id, call_id, noted_result)),
        )
        .only_action();

    // Assert
    runtime.finish_turn_with_text(turn_id, &final_completion, "read it");
}

/// A config with the `crm.get_customer` tool, callable directly and from
/// programs, and post-tool hooks bound to every tool, in order.
fn customer_config(binding_ids: &[&str]) -> HarnessConfig {
    let mut config = config();
    config.capabilities.tool_groups.push(ToolGroupDefinition {
        name: "crm".to_string(),
        description: "Customer records".to_string(),
        metadata: None,
        icon_url: None,
        tools: vec![ProvidedToolDefinition {
            name: "get_customer".to_string(),
            description: "Return one customer".to_string(),
            input_schema: json!({"type": "object"}),
            output_schema: None,
            exposure: ProvidedToolExposure::DirectAndProgrammatic,
            direct_name: None,
        }],
    });
    let bindings: Vec<Value> = binding_ids
        .iter()
        .enumerate()
        .map(|(order, id)| {
            json!({"id": id, "point": "post_tool_call", "order": order, "selector": {"type": "always"}})
        })
        .collect();
    config.capabilities.hook_bindings =
        serde_json::from_value(Value::Array(bindings)).expect("post-tool hooks configure");
    config
}

fn customer_action(arguments: Value) -> Value {
    json!({
        "type": "provided_tool_call",
        "call": {
            "type": "provided",
            "group_name": "crm",
            "tool_name": "get_customer",
            "arguments": arguments,
        },
    })
}

/// A result shaped as an MCP tool returns structured content: the value as
/// structured content and its JSON in a text block.
fn mcp_result(value: Value) -> Value {
    json!({
        "type": "success",
        "content": [{"type": "text", "text": value.to_string()}],
        "structured_content": value,
    })
}

fn tool_succeeded(action: &Value, result: &Value) -> Value {
    json!({
        "type": "tool_succeeded",
        "action_id": action["action_id"],
        "call_id": action["call_id"],
        "result": result,
    })
}

fn inconsistent_result_warning(turn_id: &str, call_id: &str, binding_ids: &[&str]) -> Value {
    json!({
        "type": "warning",
        "turn_id": turn_id,
        "code": "post_tool_call_result_inconsistent",
        "message": "A post_tool_call hook returned a result for `crm.get_customer` whose content no longer holds its structured_content. The model and programs can read different values; the result is used as returned.",
        "details": {
            "call_id": call_id,
            "tool": "crm.get_customer",
            "hook_binding_ids": binding_ids,
        },
    })
}

fn read_file_output(path: &str, content: &str) -> Value {
    json!({
        "path": path,
        "content": content,
        "file_size_bytes": content.len(),
        "returned_bytes": content.len(),
        "offset": 0,
        "lines_read": 1,
        "was_truncated": false,
        "total_lines": 1,
        "next_offset": null,
    })
}
