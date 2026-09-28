use super::*;
use crate::core::{
    LargeOutputPolicy, ProvidedToolDefinition, ProvidedToolExposure, ToolGroupDefinition,
};

fn large_output_config(exposure: ProvidedToolExposure) -> HarnessConfig {
    let mut harness_config = config();
    harness_config.settings.tools.large_output = LargeOutputPolicy::Filesystem {
        max_output_tokens: 20,
        model_visible_output_tokens: 5,
    };
    harness_config
        .capabilities
        .tool_groups
        .push(ToolGroupDefinition {
            name: "data".to_string(),
            description: "Data tools".to_string(),
            metadata: None,
            icon_url: None,
            tools: vec![ProvidedToolDefinition {
                name: "large_lookup".to_string(),
                description: "Return lookup data".to_string(),
                input_schema: json!({"type": "object"}),
                output_schema: None,
                exposure,
            }],
        });
    harness_config
}

fn provided_action(arguments: Value) -> Value {
    json!({
        "type": "provided_tool_call",
        "call": {
            "type": "provided",
            "group_name": "data",
            "tool_name": "large_lookup",
            "arguments": arguments,
        },
    })
}

fn serialized_observation(
    turn_id: &str,
    call_id: &str,
    tool_name: &str,
    serialized_char_count: usize,
) -> Value {
    json!({
        "type": "large_output_serialized",
        "turn_id": turn_id,
        "call_id": call_id,
        "tool_name": tool_name,
        "serialized_char_count": serialized_char_count,
    })
}

fn filesystem_succeeded(action: &Value, path: &str) -> Value {
    json!({
        "type": "filesystem_succeeded",
        "action_id": action["action_id"],
        "result": {
            "type": "write",
            "model_path": path,
        },
    })
}

fn filesystem_failed(action: &Value, message: &str) -> Value {
    json!({
        "type": "filesystem_failed",
        "action_id": action["action_id"],
        "error": {
            "code": "filesystem_write_failed",
            "message": message,
            "retryable": false,
            "details": null,
        },
    })
}

fn saved_receipt(
    path: &str,
    serialized: &str,
    max_output_characters: usize,
    preview_characters: usize,
    type_definition: &str,
) -> String {
    format!(
        "<system>The tool output is large. To preserve your context window, it was saved to the file system under {path}.\nOutput size: {} characters\nMax size before saving: {max_output_characters} characters\nThe truncated output below contains only the first {preview_characters} characters of the tool response.</system>\n<truncated-output>\n{}…\n</truncated-output>\n<output-type>{type_definition}</output-type>\n<system>Remember: The full content was saved to {path}.\nRead the JSON's inferred type definition from the <output-type> tag first, then inspect the saved JSON with `jq` before searching them with `grep` instead of `read_file`.</system>",
        serialized.encode_utf16().count(),
        serialized
            .chars()
            .take(preview_characters)
            .collect::<String>(),
    )
}

fn hard_limit_receipt(
    tool_name: &str,
    serialized: &str,
    hard_limit_characters: usize,
    type_definition: &str,
) -> String {
    format!(
        "<system>The {tool_name} tool output is unreasonably large ({} characters). To protect the context window, the hard inline safety limit is {hard_limit_characters} characters.\nThe truncated output below contains only the first {hard_limit_characters} characters of the tool response.</system>\n<truncated-output>\n{}…\n</truncated-output>\n<output-type>{type_definition}</output-type>\n<system>Recovery: Retry the tool with narrower parameters, or ask for a summarized/filtered result.</system>",
        serialized.encode_utf16().count(),
        serialized
            .chars()
            .take(hard_limit_characters)
            .collect::<String>(),
    )
}

///
/// *Prepare*: A direct provided tool returns JSON above the configured Core limit.
/// *Do*: Deliver the tool result, checkpoint while its private filesystem write is pending, restore, and complete the write.
/// *Assert*: Core emits one write Action, then commits only the receipt and continues to the model.
///
#[test]
fn direct_large_output_is_written_before_the_effective_result_is_committed() {
    let turn_id = "turn-direct-large-output";
    let call_id = "direct-large";
    let value = json!({"text": "x".repeat(200)});
    let serialized = serde_json::to_string_pretty(&value).unwrap();
    let relative_path = "tool-results/large_lookup-direct-large.json";
    let resolved_path = "/home/user/tool-results/large_lookup-direct-large.json";
    let type_definition = "export interface Output {\ntext: string\n}\n";
    let receipt = saved_receipt(resolved_path, &serialized, 80, 20, type_definition);
    let effective_result = json!({
        "type": "success",
        "content": [{"type": "text", "text": receipt}],
        "structured_content": null,
    });
    let mut runtime = SynchronousRuntime::new(large_output_config(ProvidedToolExposure::Direct));
    let first_completion = runtime.start_turn(turn_id, "look up the large result");
    let calls = vec![tool_call(call_id, "large_lookup", json!({}))];
    let direct_action = runtime
        .complete_with_tool_calls(
            turn_id,
            &first_completion,
            &calls,
            [provided_action(json!({}))],
        )
        .only_action();
    let direct_result = structured_tool_success(&direct_action, value);

    let write_action = runtime
        .apply(
            direct_result,
            running(turn_id)
                .dispatch(filesystem_write(relative_path))
                .observe(serialized_observation(
                    turn_id,
                    call_id,
                    "large_lookup",
                    serialized.encode_utf16().count(),
                )),
        )
        .only_action();
    assert_eq!(write_action["operation"]["content"], serialized);
    runtime.restart_from_checkpoint();

    let next_completion = runtime
        .apply(
            filesystem_succeeded(&write_action, resolved_path),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(tool_result_committed(
                    turn_id,
                    &direct_action,
                    effective_result.clone(),
                ))
                .observe(tool_execution_finished(turn_id, call_id, effective_result)),
        )
        .only_action();
    runtime.assert_last_model_message_update(
        &next_completion,
        "replace",
        &[
            model_system(),
            model_user_text("look up the large result"),
            model_assistant_tool_calls(&calls),
            model_tool_text(call_id, "large_lookup", &receipt),
        ],
    );
}

///
/// *Prepare*: A direct result exceeds the filesystem threshold but fits the hard inline limit.
/// *Do*: Runtime reports that the private write failed.
/// *Assert*: Core commits the complete original result and continues the turn.
///
#[test]
fn failed_write_returns_the_complete_result_within_the_hard_inline_limit() {
    let turn_id = "turn-failed-write-fallback";
    let call_id = "failed-write";
    let value = json!({"text": "x".repeat(200)});
    let serialized = serde_json::to_string_pretty(&value).unwrap();
    let mut runtime = SynchronousRuntime::new(large_output_config(ProvidedToolExposure::Direct));
    let first_completion = runtime.start_turn(turn_id, "look up the large result");
    let calls = vec![tool_call(call_id, "large_lookup", json!({}))];
    let direct_action = runtime
        .complete_with_tool_calls(
            turn_id,
            &first_completion,
            &calls,
            [provided_action(json!({}))],
        )
        .only_action();
    let direct_result = structured_tool_success(&direct_action, value.clone());

    let write_action = runtime
        .apply(
            direct_result.clone(),
            running(turn_id)
                .dispatch(filesystem_write(
                    "tool-results/large_lookup-failed-write.json",
                ))
                .observe(serialized_observation(
                    turn_id,
                    call_id,
                    "large_lookup",
                    serialized.encode_utf16().count(),
                )),
        )
        .only_action();

    let next_completion = runtime
        .apply(
            filesystem_failed(&write_action, "filesystem unavailable"),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe_completed_tool_result(&direct_action, &direct_result),
        )
        .only_action();
    runtime.assert_last_model_message_update(
        &next_completion,
        "append",
        &[
            model_assistant_tool_calls(&calls),
            model_tool_text(
                call_id,
                "large_lookup",
                &serde_json::to_string(&value).unwrap(),
            ),
        ],
    );
}

///
/// *Prepare*: Disabled offloading receives a direct result above the hard inline limit.
/// *Do*: Complete the tool without any filesystem adapter.
/// *Assert*: Core emits no filesystem Action and gives the model a bounded truncation receipt.
///
#[test]
fn disabled_mode_truncates_only_results_above_the_hard_inline_limit() {
    const HARD_INLINE_CHARACTER_LIMIT: usize = 100_000;

    let turn_id = "turn-disabled-hard-limit";
    let call_id = "disabled-hard-limit";
    let value = json!({"text": "x".repeat(HARD_INLINE_CHARACTER_LIMIT * 2)});
    let serialized = serde_json::to_string_pretty(&value).unwrap();
    let type_definition = "export interface Output {\ntext: string\n}\n";
    let receipt = hard_limit_receipt(
        "large_lookup",
        &serialized,
        HARD_INLINE_CHARACTER_LIMIT,
        type_definition,
    );
    let effective_result = json!({
        "type": "success",
        "content": [{"type": "text", "text": receipt}],
        "structured_content": null,
    });
    let mut harness_config = large_output_config(ProvidedToolExposure::Direct);
    harness_config.settings.tools.large_output = LargeOutputPolicy::Disabled;
    let mut runtime = SynchronousRuntime::new(harness_config);
    let first_completion = runtime.start_turn(turn_id, "look up the enormous result");
    let calls = vec![tool_call(call_id, "large_lookup", json!({}))];
    let direct_action = runtime
        .complete_with_tool_calls(
            turn_id,
            &first_completion,
            &calls,
            [provided_action(json!({}))],
        )
        .only_action();
    let direct_result = structured_tool_success(&direct_action, value);

    let next_completion = runtime
        .apply(
            direct_result,
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(serialized_observation(
                    turn_id,
                    call_id,
                    "large_lookup",
                    serialized.encode_utf16().count(),
                ))
                .observe(tool_result_committed(
                    turn_id,
                    &direct_action,
                    effective_result.clone(),
                ))
                .observe(tool_execution_finished(turn_id, call_id, effective_result)),
        )
        .only_action();
    runtime.assert_last_model_message_update(
        &next_completion,
        "append",
        &[
            model_assistant_tool_calls(&calls),
            model_tool_text(call_id, "large_lookup", &receipt),
        ],
    );
}

///
/// *Prepare*: `run_typescript` receives an oversized nested result and reduces it to a small count.
/// *Do*: Complete the nested programmatic tool call.
/// *Assert*: The nested value reaches TypeScript unchanged, no filesystem write is emitted, and only the small outer result reaches the model.
///
#[test]
fn programmatic_subtool_results_bypass_offloading() {
    let turn_id = "turn-programmatic-large-output";
    let mut runtime =
        SynchronousRuntime::new(large_output_config(ProvidedToolExposure::Programmatic));
    let first_completion = runtime.start_turn(turn_id, "count the lookup results");
    let source = "async function main() { const value = await tools.data.large_lookup({}); return { count: value.items.length }; }";
    let nested_action = runtime
        .complete_with_typescript_program(
            turn_id,
            &first_completion,
            "program-large",
            source,
            [provided_action(json!({}))],
        )
        .only_action();
    let nested_value = json!({"items": [{"text": "x".repeat(200)}]});
    let nested_result = structured_tool_success(&nested_action, nested_value);
    let outer_value = json!({"count": 1});
    let outer_serialized = serde_json::to_string_pretty(&outer_value).unwrap();

    let next_completion = runtime
        .apply(
            nested_result.clone(),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe_tool_result(&nested_action, &nested_result)
                .observe(serialized_observation(
                    turn_id,
                    "program-large",
                    "run_typescript",
                    outer_serialized.encode_utf16().count(),
                ))
                .observe_tool_execution_finished(
                    "program-large",
                    run_typescript_success(outer_value),
                ),
        )
        .only_action();
    runtime.assert_last_model_message_update(
        &next_completion,
        "append",
        &[
            model_assistant_typescript("program-large", source),
            model_tool_text("program-large", "run_typescript", r#"{"count":1}"#),
        ],
    );
}

///
/// *Prepare*: `run_typescript` executes two sequential `Promise.allSettled` batches containing
/// six and thirty-six programmatic tool calls, then builds a fixed memory-pressure result.
/// *Do*: Resolve every nested call with a production-sized JSON text block so final replay reaches
/// the V8 memory limit after all operations are recorded.
/// *Assert*: The final nested result is committed, `run_typescript` reports an actionable failure,
/// and the agent receives another model turn.
///
#[test]
fn sequential_programmatic_batches_report_memory_limit_and_continue() {
    const RESULT_PAYLOAD_BYTES: usize = 55_000;

    let turn_id = "turn-sequential-programmatic-batches";
    let call_id = "program-batches";
    let first_queries = (0..6)
        .map(|index| format!("first-{index}"))
        .collect::<Vec<_>>();
    let second_queries = (0..36)
        .map(|index| format!("second-{index}"))
        .collect::<Vec<_>>();
    let source = format!(
        "async function main() {{\n  const firstQueries = {};\n  const secondQueries = {};\n  const first = await Promise.allSettled(firstQueries.map(query => tools.data.large_lookup({{ query }})));\n  const second = await Promise.allSettled(secondQueries.map(query => tools.data.large_lookup({{ query }})));\n  const memoryPressure = JSON.stringify({{ value: \"x\".repeat(8 * 1024 * 1024) }});\n  return {{ first, second, totalQueries: 42, memoryPressure: JSON.parse(memoryPressure) }};\n}}",
        serde_json::to_string(&first_queries).unwrap(),
        serde_json::to_string(&second_queries).unwrap(),
    );
    let mut harness_config = large_output_config(ProvidedToolExposure::Programmatic);
    harness_config.settings.tools.large_output = LargeOutputPolicy::Filesystem {
        max_output_tokens: 10_000,
        model_visible_output_tokens: 5_000,
    };
    let mut runtime = SynchronousRuntime::new(harness_config);
    let first_completion = runtime.start_turn(turn_id, "run two search batches");
    let first_actions = runtime
        .complete_with_typescript_program(
            turn_id,
            &first_completion,
            call_id,
            &source,
            first_queries
                .iter()
                .map(|query| provided_action(json!({"query": query}))),
        )
        .actions();
    assert_eq!(first_actions.len(), first_queries.len());

    let nested_value = |query: &str| {
        let seed = format!("{query}:");
        let mut payload = seed.repeat(RESULT_PAYLOAD_BYTES / seed.len() + 1);
        payload.truncate(RESULT_PAYLOAD_BYTES);
        json!({
            "query": query,
            "payload": payload,
        })
    };
    let nested_result = |action: &Value, query: &str| {
        let text = serde_json::to_string(&nested_value(query)).unwrap();
        text_tool_success(action, &text)
    };
    for (index, action) in first_actions
        .iter()
        .enumerate()
        .take(first_actions.len() - 1)
    {
        let result = nested_result(action, &first_queries[index]);
        let expected = first_actions[index + 1..]
            .iter()
            .fold(running(turn_id), |expected, pending| expected.keep(pending))
            .observe_tool_result(action, &result);
        runtime.apply(result, expected);
    }

    let last_first_action = first_actions.last().unwrap();
    let last_first_result = nested_result(last_first_action, first_queries.last().unwrap());
    let second_actions = runtime
        .apply(
            last_first_result.clone(),
            second_queries
                .iter()
                .map(|query| provided_action(json!({"query": query})))
                .fold(running(turn_id), ExpectedTransition::dispatch)
                .observe_tool_result(last_first_action, &last_first_result),
        )
        .actions();
    assert_eq!(second_actions.len(), second_queries.len());

    for (index, action) in second_actions
        .iter()
        .enumerate()
        .take(second_actions.len() - 1)
    {
        let result = nested_result(action, &second_queries[index]);
        let expected = second_actions[index + 1..]
            .iter()
            .fold(running(turn_id), |expected, pending| expected.keep(pending))
            .observe_tool_result(action, &result);
        runtime.apply(result, expected);
    }

    let last_second_action = second_actions.last().unwrap();
    let last_second_result = nested_result(last_second_action, second_queries.last().unwrap());
    let memory_error = json!({
        "name": "MemoryLimitError",
        "message": "run_typescript exceeded its 16 MiB memory limit with 42 recorded operations. Retry with fewer tool calls in each run_typescript block, request smaller tool results, or split the work across multiple run_typescript calls. Return only the fields needed for the next step instead of every full tool result.",
        "details": {
            "memoryLimitMiB": 16,
            "operationCount": 42,
        },
    });
    let failure_text = format!(
        "run_typescript failed: {}",
        serde_json::to_string(&memory_error).unwrap()
    );
    let failure_result = json!({
        "type": "failure",
        "content": [{"type": "text", "text": failure_text}],
        "error": {
            "code": "MemoryLimitError",
            "message": memory_error["message"],
            "retryable": false,
            "details": memory_error["details"],
        },
    });
    let failure_serialized = serde_json::to_string_pretty(&failure_result).unwrap();
    let next_completion = runtime
        .apply(
            last_second_result.clone(),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe_tool_result(last_second_action, &last_second_result)
                .observe(serialized_observation(
                    turn_id,
                    call_id,
                    "run_typescript",
                    failure_serialized.encode_utf16().count(),
                ))
                .observe_tool_execution_finished(call_id, failure_result),
        )
        .only_action();
    runtime.assert_last_model_message_update(
        &next_completion,
        "append",
        &[
            model_assistant_typescript(call_id, &source),
            json!({
                "role": "tool",
                "tool_call_id": call_id,
                "name": "run_typescript",
                "outcome": "failure",
                "content": [{"type": "text", "text": failure_text}],
            }),
        ],
    );
}

///
/// *Prepare*: A local `run_typescript` call returns an oversized value without nested effects.
/// *Do*: Resolve the private filesystem write.
/// *Assert*: The outer wrapper finishes only after the write and the next model request contains the receipt.
///
#[test]
fn oversized_outer_run_typescript_result_uses_the_private_write_action() {
    let turn_id = "turn-outer-large-output";
    let call_id = "outer-large";
    let value = json!({"text": "x".repeat(200)});
    let serialized = serde_json::to_string_pretty(&value).unwrap();
    let relative_path = "tool-results/run_typescript-outer-large.json";
    let resolved_path = "/home/user/tool-results/run_typescript-outer-large.json";
    let receipt = saved_receipt(
        resolved_path,
        &serialized,
        80,
        20,
        "export interface Output {\ntext: string\n}\n",
    );
    let mut runtime = SynchronousRuntime::new(large_output_config(ProvidedToolExposure::Direct));
    let first_completion = runtime.start_turn(turn_id, "return a large local value");
    let source = format!(
        "async function main() {{ return {{ text: {:?} }}; }}",
        "x".repeat(200)
    );

    let write_action = runtime
        .apply(
            run_typescript_completion(action_id(&first_completion), call_id, &source),
            running(turn_id)
                .dispatch(filesystem_write(relative_path))
                .observe(tool_execution_started(turn_id, call_id))
                .observe(serialized_observation(
                    turn_id,
                    call_id,
                    "run_typescript",
                    serialized.encode_utf16().count(),
                )),
        )
        .only_action();
    assert_eq!(write_action["operation"]["content"], serialized);

    let effective_result = json!({
        "type": "success",
        "content": [{"type": "text", "text": receipt}],
        "structured_content": null,
    });
    let next_completion = runtime
        .apply(
            filesystem_succeeded(&write_action, resolved_path),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(tool_execution_finished(turn_id, call_id, effective_result)),
        )
        .only_action();
    runtime.assert_last_model_message_update(
        &next_completion,
        "append",
        &[
            model_assistant_typescript(call_id, &source),
            model_tool_text(call_id, "run_typescript", &receipt),
        ],
    );
}

///
/// *Prepare*: A local `run_typescript` call logs oversized stdout and returns a small value.
/// *Do*: Resolve the private filesystem write.
/// *Assert*: The saved payload includes stdout, and the model sees only the receipt.
///
#[test]
fn oversized_run_typescript_stdout_is_included_in_the_private_write() {
    let turn_id = "turn-outer-large-stdout";
    let call_id = "outer-stdout";
    let stdout = "x".repeat(200);
    let source =
        format!("async function main() {{ console.log({stdout:?}); return {{ ok: true }}; }}");
    let value = json!({"ok": true});
    let serialized = serde_json::to_string_pretty(&json!({
        "content": [
            {"type": "text", "text": serde_json::to_string(&value).unwrap()},
            {"type": "text", "text": format!("stdout:\n{stdout}")},
        ],
        "structured_content": value,
    }))
    .unwrap();
    let relative_path = "tool-results/run_typescript-outer-stdout.json";
    let resolved_path = "/home/user/tool-results/run_typescript-outer-stdout.json";
    let type_definition = "export interface Output {\ncontent: {\ntype: string\ntext: string\n}[]\nstructured_content: {\nok: boolean\n}\n}\n";
    let receipt = saved_receipt(resolved_path, &serialized, 80, 20, type_definition);
    let mut runtime = SynchronousRuntime::new(large_output_config(ProvidedToolExposure::Direct));
    let first_completion = runtime.start_turn(turn_id, "log a large local value");

    let write_action = runtime
        .apply(
            run_typescript_completion(action_id(&first_completion), call_id, &source),
            running(turn_id)
                .dispatch(filesystem_write(relative_path))
                .observe(tool_execution_started(turn_id, call_id))
                .observe(serialized_observation(
                    turn_id,
                    call_id,
                    "run_typescript",
                    serialized.encode_utf16().count(),
                )),
        )
        .only_action();
    assert_eq!(write_action["operation"]["content"], serialized);

    let effective_result = json!({
        "type": "success",
        "content": [{"type": "text", "text": receipt}],
        "structured_content": null,
    });
    let next_completion = runtime
        .apply(
            filesystem_succeeded(&write_action, resolved_path),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(tool_execution_finished(turn_id, call_id, effective_result)),
        )
        .only_action();
    runtime.assert_last_model_message_update(
        &next_completion,
        "append",
        &[
            model_assistant_typescript(call_id, &source),
            model_tool_text(call_id, "run_typescript", &receipt),
        ],
    );
}
