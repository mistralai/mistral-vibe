use super::*;

/// *Prepare*: Two parallel reads are already in flight when a program reaches its elapsed budget.
/// *Do*: Settle the reads across a checkpoint restore, with rich content on the first result.
/// *Assert*: The sibling remains pending until it settles, rich content survives, and no next effect starts.
#[test]
fn exhausted_program_settles_parallel_effects_and_retains_rich_content() {
    let source = r#"async function main() {
      await Promise.all([
        tools.file_system.read_file({path: "first.txt"}),
        tools.file_system.read_file({path: "second.txt"}),
      ]);
      await tools.self.sleep({seconds: 1});
    }"#;
    let mut runtime = SynchronousRuntime::new(config());
    let completion = runtime.start_turn("parallel", "read both");
    let reads = runtime
        .apply_at(
            run_typescript_completion(action_id(&completion), "program", source),
            1_700_000_000_000,
            running("parallel")
                .dispatch(runtime_tool(
                    "file_system.read_file",
                    json!({"path": "first.txt"}),
                ))
                .dispatch(runtime_tool(
                    "file_system.read_file",
                    json!({"path": "second.txt"}),
                ))
                .observe(tool_execution_started("parallel", "program")),
        )
        .actions()
        .to_vec();
    let mut first_result = file_read_success(&reads[0], "first");
    let image = json!({"type": "image", "data": "aW1hZ2U=", "mimeType": "image/png"});
    first_result["result"]["content"] = json!([image]);
    runtime.apply_at(
        first_result.clone(),
        1_700_000_060_000,
        running("parallel")
            .keep(&reads[1])
            .observe_tool_result(&reads[0], &first_result),
    );
    runtime.restart_from_checkpoint();
    let second_result = file_read_success(&reads[1], "second");
    let error = json!({
        "name": "ProgramBudgetExceeded",
        "message": "run_typescript exceeded its elapsed-time budget.",
        "details": {"budget_ms": 60_000, "elapsed_ms": 61_000},
    });
    runtime.apply_at(
        second_result.clone(),
        1_700_000_061_000,
        running("parallel")
            .dispatch(llm_call(1))
            .observe_tool_result(&reads[1], &second_result)
            .observe_tool_execution_finished("program", json!({
                "type": "failure",
                "content": [
                    {"type": "text", "text": format!("run_typescript failed: {error}")},
                    {"type": "text", "text": "Additional content from `tools.file_system.read_file` (call 1):"},
                    image,
                ],
                "error": {
                    "code": "ProgramBudgetExceeded",
                    "message": "run_typescript exceeded its elapsed-time budget.",
                    "retryable": false,
                    "details": {"budget_ms": 60_000, "elapsed_ms": 61_000},
                },
            })),
    );
}

/// *Prepare*: An earlier V1 checkpoint has a pending program without its additive start timestamp.
/// *Do*: Resume it once, checkpoint again, then deliver the next result at the elapsed limit.
/// *Assert*: The legacy program establishes time once and later restoration cannot renew its budget.
#[test]
fn earlier_v1_program_establishes_start_time_once() {
    let source = r#"async function main() {
      await tools.self.sleep({ seconds: 30 });
      await tools.self.sleep({ seconds: 30 });
      return "must not succeed";
    }"#;
    let mut runtime = SynchronousRuntime::new(config());
    let completion = runtime.start_turn("legacy", "resume old work");
    let first = runtime
        .apply_at(
            run_typescript_completion(action_id(&completion), "program", source),
            1_700_000_000_000,
            running("legacy")
                .dispatch(runtime_tool("self.sleep", json!({"seconds": 30})))
                .observe(tool_execution_started("legacy", "program")),
        )
        .only_action();
    let mut checkpoint = runtime.checkpoint_value();
    checkpoint["turn"]["phase"]["batch"]["executions"][0]["execution"]
        .as_object_mut()
        .expect("program execution object")
        .remove("started_at_unix_ms")
        .expect("new programs record start time");
    runtime.restore_checkpoint_value(checkpoint, config());
    let first_result = structured_tool_success(&first, json!({"seconds": 30}));
    let second = runtime
        .apply_at(
            first_result.clone(),
            1_700_000_100_000,
            running("legacy")
                .dispatch(runtime_tool("self.sleep", json!({"seconds": 30})))
                .observe_tool_result(&first, &first_result),
        )
        .only_action();
    runtime.restart_from_checkpoint();
    let second_result = structured_tool_success(&second, json!({"seconds": 30}));
    let error = json!({
        "name": "ProgramBudgetExceeded",
        "message": "run_typescript exceeded its elapsed-time budget.",
        "details": {"budget_ms": 60_000, "elapsed_ms": 60_000},
    });
    runtime.apply_at(
        second_result.clone(),
        1_700_000_160_000,
        running("legacy")
            .dispatch(llm_call(1))
            .observe_tool_result(&second, &second_result)
            .observe_tool_execution_finished("program", json!({
                "type": "failure",
                "content": [{"type": "text", "text": format!("run_typescript failed: {error}")}],
                "error": {
                    "code": "ProgramBudgetExceeded",
                    "message": "run_typescript exceeded its elapsed-time budget.",
                    "retryable": false,
                    "details": {"budget_ms": 60_000, "elapsed_ms": 60_000},
                },
            })),
    );
}

/// *Prepare*: Programmatic sleep is available with its resolved thirty-second maximum.
/// *Do*: Catch the validation result of a thirty-one-second request.
/// *Assert*: The script continues without dispatching the invalid timer.
#[test]
fn program_sleep_rejects_requests_above_the_resolved_maximum() {
    let turn_id = "sleep-cap";
    let call_id = "program";
    let source = r#"async function main() {
      try { await tools.self.sleep({ seconds: 31 }); }
      catch (_) { return "sleep rejected"; }
      return "must not wait";
    }"#;
    let mut runtime = SynchronousRuntime::new(config());
    let completion = runtime.start_turn(turn_id, "check the sleep cap");
    let resumed = runtime
        .apply(
            run_typescript_completion(action_id(&completion), call_id, source),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(tool_execution_started(turn_id, call_id))
                .observe_tool_execution_finished(
                    call_id,
                    run_typescript_success(json!("sleep rejected")),
                ),
        )
        .only_action();
    runtime.assert_last_model_message_update(
        &resumed,
        "append",
        &[
            model_assistant_typescript(call_id, source),
            model_tool_text(call_id, "run_typescript", "\"sleep rejected\""),
        ],
    );
}

/// *Prepare*: A foreground program allows two thirty-second operations and captures logs.
/// *Do*: Restore after its first result, then return the second result at the elapsed limit.
/// *Assert*: The next operation never dispatches, completed results remain committed, and failure retains logs.
#[test]
fn program_elapsed_budget_survives_replay_and_checkpoint_restore() {
    let turn_id = "timed-program";
    let call_id = "program";
    let source = r#"async function main() {
      console.log("begin");
      await tools.self.sleep({ seconds: 30 });
      console.warn("first finished");
      await tools.self.sleep({ seconds: 30 });
      console.log("second finished");
      while (true) {
        try { await tools.self.sleep({ seconds: 1 }); } catch (_) {}
      }
    }"#;
    let mut config = config();
    config.settings.tools.programmatic.max_effects = 2;
    config.settings.tools.programmatic.max_operations = 2;
    let mut runtime = SynchronousRuntime::new(config);
    let completion = runtime.start_turn(turn_id, "pace queries");
    let first = runtime
        .apply_at(
            run_typescript_completion(action_id(&completion), call_id, source),
            1_700_000_000_000,
            running(turn_id)
                .dispatch(runtime_tool("self.sleep", json!({"seconds": 30})))
                .observe(tool_execution_started(turn_id, call_id)),
        )
        .only_action();
    let first_result = structured_tool_success(&first, json!({"seconds": 30}));
    let second = runtime
        .apply_at(
            first_result.clone(),
            1_700_000_030_000,
            running(turn_id)
                .dispatch(runtime_tool("self.sleep", json!({"seconds": 30})))
                .observe_tool_result(&first, &first_result),
        )
        .only_action();
    runtime.restart_from_checkpoint();

    let second_result = structured_tool_success(&second, json!({"seconds": 30}));
    let error = json!({
        "name": "ProgramBudgetExceeded",
        "message": "run_typescript exceeded its elapsed-time budget.",
        "details": {"budget_ms": 60_000, "elapsed_ms": 60_000},
    });
    let content = json!([
        {"type": "text", "text": format!("run_typescript failed: {error}")},
        {"type": "text", "text": "stdout:\nbegin\nsecond finished"},
        {"type": "text", "text": "stderr:\nfirst finished"},
    ]);
    let failure = json!({
        "type": "failure",
        "content": content,
        "error": {
            "code": "ProgramBudgetExceeded",
            "message": "run_typescript exceeded its elapsed-time budget.",
            "retryable": false,
            "details": {"budget_ms": 60_000, "elapsed_ms": 60_000},
        },
    });
    let resumed = runtime
        .apply_at(
            second_result.clone(),
            1_700_000_060_000,
            running(turn_id)
                .dispatch(llm_call(1))
                .observe_tool_result(&second, &second_result)
                .observe_tool_execution_finished(call_id, failure),
        )
        .only_action();
    runtime.assert_last_model_message_update(
        &resumed,
        "replace",
        &[
            model_system(),
            model_user_text("pace queries"),
            model_assistant_typescript(call_id, source),
            json!({
                "role": "tool",
                "tool_call_id": call_id,
                "name": "run_typescript",
                "outcome": "failure",
                "content": content,
            }),
        ],
    );
}

/// *Prepare*: A program's first operation is waiting for its pre-tool Hook.
/// *Do*: Approve after the program budget, while the script catches rejection and attempts another operation.
/// *Assert*: Neither operation starts and the model receives budget failure rather than a caught successful return.
#[test]
fn program_budget_blocks_effect_after_pre_hook_and_cannot_be_caught() {
    assert_pre_hook_budget_failure(
        r#"async function main() {
          console.log("before hook");
          try { await tools.self.sleep({ seconds: 1 }); }
          catch (_) { console.log("caught"); }
          try { await tools.self.sleep({ seconds: 2 }); } catch (_) {}
          return "must not succeed";
        }"#,
    );
}

/// *Prepare*: A pre-tool Hook outlives the program budget, and JavaScript catches tool errors.
/// *Do*: Approve the Hook before the catch branch would await an unresolved Promise.
/// *Assert*: Core stops at the known budget failure, retains earlier logs, and starts no effect.
#[test]
fn pre_hook_budget_failure_cannot_be_hidden_by_an_unresolved_promise() {
    assert_pre_hook_budget_failure(
        r#"async function main() {
          console.log("before hook");
          try { await tools.self.sleep({ seconds: 1 }); }
          catch (_) { console.log("caught"); await new Promise(() => {}); }
        }"#,
    );
}

fn assert_pre_hook_budget_failure(source: &str) {
    let turn_id = "timed-pre-hook";
    let call_id = "program";
    let mut configuration = config();
    configuration.capabilities.hook_bindings = serde_json::from_value(json!([
        {"id": "pre-tool", "point": "pre_tool_call", "order": 0, "selector": {"type": "always"}}
    ]))
    .unwrap();
    let mut runtime = SynchronousRuntime::new(configuration);
    let completion = runtime.start_turn(turn_id, "approve slowly");
    let hook = runtime
        .apply_at(
            run_typescript_completion(action_id(&completion), call_id, source),
            1_700_000_000_000,
            running(turn_id)
                .dispatch(hook_call("pre_tool_call", &["pre-tool"], Value::Null))
                .observe(tool_execution_started(turn_id, call_id)),
        )
        .only_action();
    let error = json!({
        "name": "ProgramBudgetExceeded",
        "message": "run_typescript exceeded its elapsed-time budget.",
        "details": {"budget_ms": 60_000, "elapsed_ms": 65_000},
    });
    let protocol_error = json!({
        "code": "ProgramBudgetExceeded",
        "message": "run_typescript exceeded its elapsed-time budget.",
        "retryable": false,
        "details": {"budget_ms": 60_000, "elapsed_ms": 65_000},
    });
    let nested_result = json!({
        "type": "failure",
        "content": [{"type": "text", "text": "Program operation was not started because its elapsed-time budget was exhausted."}],
        "error": protocol_error,
    });
    let content = json!([
        {"type": "text", "text": format!("run_typescript failed: {error}")},
        {"type": "text", "text": "stdout:\nbefore hook"},
    ]);
    let failure = json!({"type": "failure", "content": content, "error": protocol_error});
    let tool_call = &hook["input"]["tool_call"];
    let resumed = runtime
        .apply_at(
            hook_completed(
                &hook,
                "pre_tool_call",
                json!({"type": "continue", "effective_arguments": {"seconds": 1}}),
            ),
            1_700_000_065_000,
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(json!({
                    "type": "tool_result_committed",
                    "turn_id": turn_id,
                    "action_id": tool_call["action_id"],
                    "call_id": tool_call["call_id"],
                    "result": nested_result,
                }))
                .observe_tool_execution_finished(call_id, failure),
        )
        .only_action();
    runtime.assert_last_model_message_update(&resumed, "append", &[
        model_assistant_typescript(call_id, source),
        json!({"role": "tool", "tool_call_id": call_id, "name": "run_typescript", "outcome": "failure", "content": content}),
    ]);
}

/// *Prepare*: A program's read waits for approval, and JavaScript catches tool errors.
/// *Do*: Approve the read after the program budget ran out.
/// *Assert*: The read never starts and the model receives the budget failure.
#[test]
fn late_program_approval_starts_no_effect_after_the_budget() {
    let turn_id = "timed-approval";
    let call_id = "program";
    let source = r#"async function main() {
      console.log("before approval");
      try { await tools.file_system.read_file({ path: "secrets.txt" }); }
      catch (_) { console.log("caught"); }
      return "must not succeed";
    }"#;
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
    let mut runtime =
        SynchronousRuntime::new(serde_json::from_value(config_value).expect("valid policy"));
    let completion = runtime.start_turn(turn_id, "read slowly");
    let operation_id = format!(
        "{}:tool:0",
        crate::core::testing::typescript_execution_id(3, call_id, 0)
    );
    let approval = runtime
        .apply_at(
            run_typescript_completion(action_id(&completion), call_id, source),
            1_700_000_000_000,
            running(turn_id)
                .dispatch(json!({"type": "approval", "call_id": operation_id}))
                .observe(tool_execution_started(turn_id, call_id)),
        )
        .only_action();
    let error = json!({
        "name": "ProgramBudgetExceeded",
        "message": "run_typescript exceeded its elapsed-time budget.",
        "details": {"budget_ms": 60_000, "elapsed_ms": 65_000},
    });
    let protocol_error = json!({
        "code": "ProgramBudgetExceeded",
        "message": "run_typescript exceeded its elapsed-time budget.",
        "retryable": false,
        "details": {"budget_ms": 60_000, "elapsed_ms": 65_000},
    });
    let nested_result = json!({
        "type": "failure",
        "content": [{"type": "text", "text": "Program operation was not started because its elapsed-time budget was exhausted."}],
        "error": protocol_error,
    });
    let content = json!([
        {"type": "text", "text": format!("run_typescript failed: {error}")},
        {"type": "text", "text": "stdout:\nbefore approval"},
    ]);
    let failure = json!({"type": "failure", "content": content, "error": protocol_error});
    let operation = json!({
        "action_id": crate::core::testing::effect_id_for_operation(
            crate::core::testing::ToolOrigin::Programmatic,
            &operation_id,
        ),
        "call_id": operation_id,
    });
    let resumed = runtime
        .apply_at(
            json!({
                "type": "approval_completed",
                "action_id": action_id(&approval),
                "outcome": "approve",
            }),
            1_700_000_065_000,
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(tool_result_committed(turn_id, &operation, nested_result))
                .observe_tool_execution_finished(call_id, failure),
        )
        .only_action();
    runtime.assert_last_model_message_update(&resumed, "append", &[
        model_assistant_typescript(call_id, source),
        json!({"role": "tool", "tool_call_id": call_id, "name": "run_typescript", "outcome": "failure", "content": content}),
    ]);
}
