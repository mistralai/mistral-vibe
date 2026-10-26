use super::*;

const START_MS: u64 = 1_700_000_000_000;

fn ask_config(tool: &str) -> HarnessConfig {
    let mut config_value = serde_json::to_value(config()).unwrap();
    config_value["settings"]["tools"]["permissions"] = json!({
        "rules": [{
            "tools": [tool],
            "decision": "ask",
            "allowlist": [],
            "denylist": [],
            "sensitive": [],
        }],
        "default": "allow",
    });
    serde_json::from_value(config_value).expect("valid policy")
}

/// *Prepare*: A program writes a draft that waits for approval, then sleeps.
/// *Do*: Approve the write two minutes later, twice the program's time budget, and settle both operations.
/// *Assert*: The write starts once, the sleep keeps its full length, and the program succeeds.
#[test]
fn program_approval_longer_than_the_time_budget_does_not_stop_the_program() {
    let turn_id = "slow-approval";
    let call_id = "program";
    let source = r#"async function main() {
      await tools.file_system.write_file({ path: "draft.md", content: "draft" });
      await tools.self.sleep({ seconds: 1 });
      return "draft created";
    }"#;
    let mut runtime = SynchronousRuntime::new(ask_config("file_system.write_file"));
    let completion = runtime.start_turn(turn_id, "create a draft");
    let operation_id = format!(
        "{}:tool:0",
        crate::core::testing::typescript_execution_id(3, call_id, 0)
    );
    let approval = runtime
        .apply_at(
            run_typescript_completion(action_id(&completion), call_id, source),
            START_MS,
            running(turn_id)
                .dispatch(json!({"type": "approval", "call_id": operation_id}))
                .observe(tool_execution_started(turn_id, call_id)),
        )
        .only_action();
    let write = runtime
        .apply_at(
            json!({
                "type": "approval_completed",
                "action_id": action_id(&approval),
                "outcome": "approve",
            }),
            START_MS + 120_000,
            running(turn_id).dispatch(runtime_tool(
                "file_system.write_file",
                json!({"path": "draft.md", "content": "draft"}),
            )),
        )
        .only_action();
    let write_result = structured_tool_success(
        &write,
        json!({"path": "draft.md", "bytes_written": 5, "file_existed": false}),
    );
    let sleep = runtime
        .apply_at(
            write_result.clone(),
            START_MS + 125_000,
            running(turn_id)
                .dispatch(runtime_tool("self.sleep", json!({"seconds": 1})))
                .observe_tool_result(&write, &write_result),
        )
        .only_action();
    let sleep_result = structured_tool_success(&sleep, json!({"seconds": 1}));

    // Do
    let resumed = runtime
        .apply_at(
            sleep_result.clone(),
            START_MS + 126_000,
            running(turn_id)
                .dispatch(llm_call(1))
                .observe_tool_result(&sleep, &sleep_result)
                .observe_tool_execution_finished(
                    call_id,
                    run_typescript_success(json!("draft created")),
                ),
        )
        .only_action();

    // Assert
    runtime.assert_last_model_message_update(
        &resumed,
        "append",
        &[
            model_assistant_typescript(call_id, source),
            model_tool_text(call_id, "run_typescript", "\"draft created\""),
        ],
    );
}

/// *Prepare*: A program's sleep waits for its pre-tool Hook.
/// *Do*: Continue the Hook 65 s later, after the program's 60 s time budget would have run out.
/// *Assert*: The sleep starts with its full length, and the program succeeds.
#[test]
fn pre_hook_wait_does_not_count_toward_the_time_budget() {
    let turn_id = "slow-pre-hook";
    let call_id = "program";
    let source = r#"async function main() {
      await tools.self.sleep({ seconds: 1 });
      return "slept";
    }"#;
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
            START_MS,
            running(turn_id)
                .dispatch(hook_call("pre_tool_call", &["pre-tool"], Value::Null))
                .observe(tool_execution_started(turn_id, call_id)),
        )
        .only_action();
    let sleep = runtime
        .apply_at(
            hook_completed(
                &hook,
                "pre_tool_call",
                json!({"type": "continue", "effective_arguments": {"seconds": 1}}),
            ),
            START_MS + 65_000,
            running(turn_id).dispatch(runtime_tool("self.sleep", json!({"seconds": 1}))),
        )
        .only_action();
    let sleep_result = structured_tool_success(&sleep, json!({"seconds": 1}));

    // Do / Assert
    runtime.apply_at(
        sleep_result.clone(),
        START_MS + 66_000,
        running(turn_id)
            .dispatch(llm_call(1))
            .observe_tool_result(&sleep, &sleep_result)
            .observe_tool_execution_finished(call_id, run_typescript_success(json!("slept"))),
    );
}

/// Config with one pre-tool Hook on every tool call.
fn pre_hook_config() -> HarnessConfig {
    let mut configuration = config();
    configuration.capabilities.hook_bindings = serde_json::from_value(json!([
        {"id": "pre-tool", "point": "pre_tool_call", "order": 0, "selector": {"type": "always"}}
    ]))
    .unwrap();
    configuration
}

/// *Prepare*: A program asks for a 30 s sleep, and its pre-tool Hook lowers it to 5 s.
/// *Do*: Finish the 5 s sleep, with most of the time budget left.
/// *Assert*: The sleep counts as finished, and the program succeeds.
#[test]
fn a_sleep_a_pre_hook_shortened_is_not_a_budget_stop() {
    let turn_id = "hook-lowers-sleep";
    let call_id = "program";
    let source = r#"async function main() {
      await tools.self.sleep({ seconds: 30 });
      return "slept";
    }"#;
    let mut runtime = SynchronousRuntime::new(pre_hook_config());
    let completion = runtime.start_turn(turn_id, "pause briefly");
    let hook = runtime
        .apply_at(
            run_typescript_completion(action_id(&completion), call_id, source),
            START_MS,
            running(turn_id)
                .dispatch(hook_call("pre_tool_call", &["pre-tool"], Value::Null))
                .observe(tool_execution_started(turn_id, call_id)),
        )
        .only_action();
    let sleep = runtime
        .apply_at(
            hook_completed(
                &hook,
                "pre_tool_call",
                json!({"type": "continue", "effective_arguments": {"seconds": 5}}),
            ),
            START_MS,
            running(turn_id).dispatch(runtime_tool("self.sleep", json!({"seconds": 5}))),
        )
        .only_action();
    let sleep_result = structured_tool_success(&sleep, json!({"seconds": 5}));

    // Do / Assert
    runtime.apply_at(
        sleep_result.clone(),
        START_MS + 5_000,
        running(turn_id)
            .dispatch(llm_call(1))
            .observe_tool_result(&sleep, &sleep_result)
            .observe_tool_execution_finished(call_id, run_typescript_success(json!("slept"))),
    );
}

/// *Prepare*: After a 50 s read, a program asks for a 1 s sleep, and its pre-tool Hook raises it to 30 s.
/// *Do*: Core cuts the sleep to the 10 s left; restore a checkpoint, then finish the sleep.
/// *Assert*: The sleep rejects catchably with `ProgramBudgetExceeded`, and the program finishes.
#[test]
fn a_sleep_a_pre_hook_lengthened_is_still_cut_to_the_time_budget_across_a_restore() {
    let turn_id = "hook-raises-sleep";
    let call_id = "program";
    let source = r#"async function main() {
      await tools.file_system.read_file({ path: "slow.txt" });
      try { await tools.self.sleep({ seconds: 1 }); }
      catch (error) { console.log(`sleep: ${error.code}`); }
      return "continued";
    }"#;
    let mut runtime = SynchronousRuntime::new(pre_hook_config());
    let completion = runtime.start_turn(turn_id, "pace the work");
    let read_hook = runtime
        .apply_at(
            run_typescript_completion(action_id(&completion), call_id, source),
            START_MS,
            running(turn_id)
                .dispatch(hook_call("pre_tool_call", &["pre-tool"], Value::Null))
                .observe(tool_execution_started(turn_id, call_id)),
        )
        .only_action();
    let read = runtime
        .apply_at(
            hook_completed(
                &read_hook,
                "pre_tool_call",
                json!({"type": "continue", "effective_arguments": {"path": "slow.txt"}}),
            ),
            START_MS,
            running(turn_id).dispatch(runtime_tool(
                "file_system.read_file",
                json!({"path": "slow.txt"}),
            )),
        )
        .only_action();
    let read_result = file_read_success(&read, "slow");
    let sleep_hook = runtime
        .apply_at(
            read_result.clone(),
            START_MS + 50_000,
            running(turn_id)
                .dispatch(hook_call("pre_tool_call", &["pre-tool"], Value::Null))
                .observe_tool_result(&read, &read_result),
        )
        .only_action();
    let sleep = runtime
        .apply_at(
            hook_completed(
                &sleep_hook,
                "pre_tool_call",
                json!({"type": "continue", "effective_arguments": {"seconds": 30}}),
            ),
            START_MS + 50_000,
            running(turn_id).dispatch(runtime_tool("self.sleep", json!({"seconds": 10.0}))),
        )
        .only_action();

    // Do
    runtime.restart_from_checkpoint();
    let budget_message = "This sleep ended early because run_typescript used its 60.0 s time budget. The program stops before its next tool call.";
    let budget_failure = json!({
        "type": "failure",
        "content": [{"type": "text", "text": budget_message}],
        "error": {
            "code": "ProgramBudgetExceeded",
            "message": budget_message,
            "retryable": false,
            "details": {"budget_ms": 60_000, "used_ms": 60_000},
        },
    });
    let success = json!({
        "type": "success",
        "content": [
            {"type": "text", "text": "\"continued\""},
            {"type": "text", "text": "stdout:\nsleep: ProgramBudgetExceeded"},
        ],
        "structured_content": "continued",
    });

    // Assert
    runtime.apply_at(
        structured_tool_success(&sleep, json!({"seconds": 10.0})),
        START_MS + 60_000,
        running(turn_id)
            .dispatch(llm_call(1))
            .observe(tool_result_committed(turn_id, &sleep, budget_failure))
            .observe_tool_execution_finished(call_id, success),
    );
}

/// *Prepare*: After a 40 s read, a program catches its sleep that the time budget cut short, then writes a file.
/// *Do*: Finish the shortened sleep at the end of the 60 s time budget.
/// *Assert*: The program stops before the write, and its call list shows the sleep as never started.
#[test]
fn a_sleep_the_time_budget_cut_short_is_listed_as_never_started() {
    let turn_id = "budget-sleep-summary";
    let call_id = "program";
    let source = r#"async function main() {
      await tools.file_system.read_file({ path: "slow.txt" });
      try { await tools.self.sleep({ seconds: 30 }); }
      catch (error) { console.log(`sleep: ${error.code}`); }
      await tools.file_system.write_file({ path: "out.txt", content: "x" });
      return "written";
    }"#;
    let mut runtime = SynchronousRuntime::new(config());
    let completion = runtime.start_turn(turn_id, "pace the work");
    let read = runtime
        .apply_at(
            run_typescript_completion(action_id(&completion), call_id, source),
            START_MS,
            running(turn_id)
                .dispatch(runtime_tool(
                    "file_system.read_file",
                    json!({"path": "slow.txt"}),
                ))
                .observe(tool_execution_started(turn_id, call_id)),
        )
        .only_action();
    let read_result = file_read_success(&read, "slow");
    let sleep = runtime
        .apply_at(
            read_result.clone(),
            START_MS + 40_000,
            running(turn_id)
                .dispatch(runtime_tool("self.sleep", json!({"seconds": 20.0})))
                .observe_tool_result(&read, &read_result),
        )
        .only_action();

    // Do
    let budget_message = "This sleep ended early because run_typescript used its 60.0 s time budget. The program stops before its next tool call.";
    let budget_failure = json!({
        "type": "failure",
        "content": [{"type": "text", "text": budget_message}],
        "error": {
            "code": "ProgramBudgetExceeded",
            "message": budget_message,
            "retryable": false,
            "details": {"budget_ms": 60_000, "used_ms": 60_000},
        },
    });
    let stopped = budget_stop(
        60_000,
        &[
            "tools.file_system.read_file 1: done",
            "tools.self.sleep 2: never started",
            "console.log 1",
            "tools.file_system.write_file 3: never started",
        ],
        "sleep: ProgramBudgetExceeded",
    );

    // Assert
    runtime.apply_at(
        structured_tool_success(&sleep, json!({"seconds": 20.0})),
        START_MS + 60_000,
        running(turn_id)
            .dispatch(llm_call(1))
            .observe(tool_result_committed(turn_id, &sleep, budget_failure))
            .observe_tool_execution_finished(call_id, stopped),
    );
}

/// *Prepare*: A program sleeps 30 s and reads a file for 25 s, across a checkpoint restore.
/// *Do*: Ask for a 30 s sleep with 5 s of time budget left.
/// *Assert*: The read counts, the sleep is cut to 5 s and rejects catchably, and the program finishes.
#[test]
fn sleep_beyond_the_time_budget_ends_early_and_the_program_continues() {
    let turn_id = "time-budget";
    let call_id = "program";
    let source = r#"async function main() {
      await tools.self.sleep({ seconds: 30 });
      await tools.file_system.read_file({ path: "slow.txt" });
      try { await tools.self.sleep({ seconds: 30 }); }
      catch (error) { console.log(`sleep: ${error.code}`); }
      return "continued";
    }"#;
    let mut runtime = SynchronousRuntime::new(config());
    let completion = runtime.start_turn(turn_id, "pace the work");
    let first_sleep = runtime
        .apply_at(
            run_typescript_completion(action_id(&completion), call_id, source),
            START_MS,
            running(turn_id)
                .dispatch(runtime_tool("self.sleep", json!({"seconds": 30})))
                .observe(tool_execution_started(turn_id, call_id)),
        )
        .only_action();
    let first_sleep_result = structured_tool_success(&first_sleep, json!({"seconds": 30}));
    let read = runtime
        .apply_at(
            first_sleep_result.clone(),
            START_MS + 30_000,
            running(turn_id)
                .dispatch(runtime_tool(
                    "file_system.read_file",
                    json!({"path": "slow.txt"}),
                ))
                .observe_tool_result(&first_sleep, &first_sleep_result),
        )
        .only_action();
    runtime.restart_from_checkpoint();
    let read_result = file_read_success(&read, "slow");
    let shortened_sleep = runtime
        .apply_at(
            read_result.clone(),
            START_MS + 55_000,
            running(turn_id)
                .dispatch(runtime_tool("self.sleep", json!({"seconds": 5.0})))
                .observe_tool_result(&read, &read_result),
        )
        .only_action();

    // Do
    let budget_message = "This sleep ended early because run_typescript used its 60.0 s time budget. The program stops before its next tool call.";
    let budget_failure = json!({
        "type": "failure",
        "content": [{"type": "text", "text": budget_message}],
        "error": {
            "code": "ProgramBudgetExceeded",
            "message": budget_message,
            "retryable": false,
            "details": {"budget_ms": 60_000, "used_ms": 60_000},
        },
    });
    let success = json!({
        "type": "success",
        "content": [
            {"type": "text", "text": "\"continued\""},
            {"type": "text", "text": "stdout:\nsleep: ProgramBudgetExceeded"},
        ],
        "structured_content": "continued",
    });
    let resumed = runtime
        .apply_at(
            structured_tool_success(&shortened_sleep, json!({"seconds": 5.0})),
            START_MS + 60_000,
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(tool_result_committed(
                    turn_id,
                    &shortened_sleep,
                    budget_failure,
                ))
                .observe_tool_execution_finished(call_id, success.clone()),
        )
        .only_action();

    // Assert
    runtime.assert_last_model_message_update(
        &resumed,
        "replace",
        &[
            model_system(),
            model_user_text("pace the work"),
            model_assistant_typescript(call_id, source),
            json!({
                "role": "tool",
                "tool_call_id": call_id,
                "name": "run_typescript",
                "outcome": "success",
                "content": success["content"],
            }),
        ],
    );
}

/// The `run_typescript` failure of a program that reached a tool call after
/// its time budget, with its call list and stdout.
fn budget_stop(used_ms: u64, call_lines: &[&str], stdout: &str) -> Value {
    let error = json!({
        "name": "ProgramBudgetExceeded",
        "message": "run_typescript used its 60.0 s time budget and stopped before its next tool call.",
        "details": {"budget_ms": 60_000, "used_ms": used_ms},
    });
    json!({
        "type": "failure",
        "content": [
            {"type": "text", "text": format!("run_typescript failed: {error}")},
            {"type": "text", "text": program_call_summary(call_lines)},
            {"type": "text", "text": format!("stdout:\n{stdout}")},
        ],
        "error": {
            "code": "ProgramBudgetExceeded",
            "message": error["message"],
            "retryable": false,
            "details": error["details"],
        },
    })
}

/// *Prepare*: A program reads a slow file, logs, then writes a file.
/// *Do*: Return the read after 70 s, longer than the program's 60 s time budget.
/// *Assert*: The read is not cut short, it counts, and the program stops before the write starts.
#[test]
fn slow_tool_execution_counts_and_the_program_stops_before_its_next_call() {
    let turn_id = "slow-tool";
    let call_id = "program";
    let source = r#"async function main() {
      await tools.file_system.read_file({ path: "slow.txt" });
      console.log("read slow.txt");
      await tools.file_system.write_file({ path: "out.txt", content: "x" });
      return "written";
    }"#;
    let mut runtime = SynchronousRuntime::new(config());
    let completion = runtime.start_turn(turn_id, "copy the file");
    let read = runtime
        .apply_at(
            run_typescript_completion(action_id(&completion), call_id, source),
            START_MS,
            running(turn_id)
                .dispatch(runtime_tool(
                    "file_system.read_file",
                    json!({"path": "slow.txt"}),
                ))
                .observe(tool_execution_started(turn_id, call_id)),
        )
        .only_action();
    let read_result = file_read_success(&read, "slow");

    // Do
    let stopped = budget_stop(
        70_000,
        &[
            "tools.file_system.read_file 1: done",
            "console.log 1",
            "tools.file_system.write_file 2: never started",
        ],
        "read slow.txt",
    );
    let resumed = runtime
        .apply_at(
            read_result.clone(),
            START_MS + 70_000,
            running(turn_id)
                .dispatch(llm_call(1))
                .observe_tool_result(&read, &read_result)
                .observe_tool_execution_finished(call_id, stopped.clone()),
        )
        .only_action();

    // Assert
    runtime.assert_last_model_message_update(
        &resumed,
        "append",
        &[
            model_assistant_typescript(call_id, source),
            json!({"role": "tool", "tool_call_id": call_id, "name": "run_typescript", "outcome": "failure", "content": stopped["content"]}),
        ],
    );
}

/// *Prepare*: A program reads three files one after the other; a steer arrives during the first read.
/// *Do*: Return the first read at 40 s and the second at 65 s, after the 60 s time budget.
/// *Assert*: The program stops before the third read, and the next model call carries the steer.
#[test]
fn steering_reaches_the_model_at_the_first_tool_call_after_the_time_budget() {
    let turn_id = "steer-program";
    let call_id = "program";
    let source = r#"async function main() {
      for (const path of ["a.txt", "b.txt", "c.txt"]) {
        await tools.file_system.read_file({ path });
      }
      return "read";
    }"#;
    let mut runtime = SynchronousRuntime::new(config());
    let completion = runtime.start_turn(turn_id, "read the files");
    let first = runtime
        .apply_at(
            run_typescript_completion(action_id(&completion), call_id, source),
            START_MS,
            running(turn_id)
                .dispatch(runtime_tool(
                    "file_system.read_file",
                    json!({"path": "a.txt"}),
                ))
                .observe(tool_execution_started(turn_id, call_id)),
        )
        .only_action();
    runtime.receive_steering(turn_id, "only read a.txt", &[&first]);
    let first_result = file_read_success(&first, "a");
    let second = runtime
        .apply_at(
            first_result.clone(),
            START_MS + 40_000,
            running(turn_id)
                .dispatch(runtime_tool(
                    "file_system.read_file",
                    json!({"path": "b.txt"}),
                ))
                .observe_tool_result(&first, &first_result),
        )
        .only_action();
    let second_result = file_read_success(&second, "b");

    // Do
    let error = json!({
        "name": "ProgramBudgetExceeded",
        "message": "run_typescript used its 60.0 s time budget and stopped before its next tool call.",
        "details": {"budget_ms": 60_000, "used_ms": 65_000},
    });
    let content = json!([
        {"type": "text", "text": format!("run_typescript failed: {error}")},
        {"type": "text", "text": program_call_summary(&[
            "tools.file_system.read_file 1-2: done",
            "tools.file_system.read_file 3: never started",
        ])},
    ]);
    let resumed = runtime
        .apply_at(
            second_result.clone(),
            START_MS + 65_000,
            running(turn_id)
                .dispatch(llm_call(1))
                .observe_tool_result(&second, &second_result)
                .observe_tool_execution_finished(
                    call_id,
                    json!({
                        "type": "failure",
                        "content": content,
                        "error": {
                            "code": "ProgramBudgetExceeded",
                            "message": error["message"],
                            "retryable": false,
                            "details": error["details"],
                        },
                    }),
                )
                .observe(turn_steered(turn_id, "only read a.txt")),
        )
        .only_action();

    // Assert
    runtime.assert_last_model_message_update(
        &resumed,
        "append",
        &[
            model_assistant_typescript(call_id, source),
            json!({"role": "tool", "tool_call_id": call_id, "name": "run_typescript", "outcome": "failure", "content": content}),
            model_user_text("only read a.txt"),
        ],
    );
}

/// *Prepare*: A program has a 2 s time budget and reads a background process's output.
/// *Do*: Ask twice for output, waiting up to 5 s each time; the first read returns after 0.5 s.
/// *Assert*: The first read waits at most 2 s and the second at most the 1.5 s left.
#[test]
fn process_output_waits_only_for_the_remaining_time_budget() {
    let turn_id = "process-wait";
    let call_id = "program";
    let source = r#"async function main() {
      await tools.process.output({ processId: "p", waitMs: 5000 });
      await tools.process.output({ processId: "p", waitMs: 5000 });
      return "read";
    }"#;
    let mut config_value = serde_json::to_value(config()).unwrap();
    config_value["settings"]["tools"]["background_processes"] = json!({"mode": "enabled"});
    config_value["settings"]["tools"]["programmatic"]["max_elapsed_ms"] = json!(2_000);
    let mut runtime =
        SynchronousRuntime::new(serde_json::from_value(config_value).expect("valid config"));
    let completion = runtime.start_turn(turn_id, "watch the process");
    let output = json!({
        "processId": "p",
        "status": "running",
        "exitCode": null,
        "output": "",
        "outputStartCursor": 0,
        "nextCursor": 0,
        "bytesAvailable": 0,
        "hasMore": false,
        "truncatedBefore": false,
    });
    let first = runtime
        .apply_at(
            run_typescript_completion(action_id(&completion), call_id, source),
            START_MS,
            running(turn_id)
                .dispatch(runtime_tool(
                    "process.output",
                    json!({"processId": "p", "waitMs": 2_000}),
                ))
                .observe(tool_execution_started(turn_id, call_id)),
        )
        .only_action();
    let first_result = structured_tool_success(&first, output.clone());

    // Do
    let second = runtime
        .apply_at(
            first_result.clone(),
            START_MS + 500,
            running(turn_id)
                .dispatch(runtime_tool(
                    "process.output",
                    json!({"processId": "p", "waitMs": 1_500}),
                ))
                .observe_tool_result(&first, &first_result),
        )
        .only_action();
    let second_result = structured_tool_success(&second, output);

    // Assert
    runtime.apply_at(
        second_result.clone(),
        START_MS + 2_000,
        running(turn_id)
            .dispatch(llm_call(1))
            .observe_tool_result(&second, &second_result)
            .observe_tool_execution_finished(call_id, run_typescript_success(json!("read"))),
    );
}

/// *Prepare*: A program reads forty 64 KiB files, then a denied write never starts, then the program throws.
/// *Do*: Return every read result.
/// *Assert*: The failure holds only the error, a call list that merges the reads, and the stdout.
#[test]
fn stopped_program_summary_stays_small_after_many_large_tool_calls() {
    let turn_id = "many-reads";
    let call_id = "program";
    let source = r#"async function main() {
      for (let page = 0; page < 40; page++) {
        await tools.file_system.read_file({ path: `page-${page}.txt` });
      }
      console.log("read 40 pages");
      try { await tools.file_system.write_file({ path: "out.txt", content: "x" }); } catch (_) {}
      throw { name: "StoppedEarly", message: "stopped after reading" };
    }"#;
    let mut config_value = serde_json::to_value(config()).unwrap();
    config_value["settings"]["tools"]["programmatic"]["max_effects"] = json!(64);
    config_value["settings"]["tools"]["programmatic"]["max_operations"] = json!(64);
    config_value["settings"]["tools"]["permissions"] = json!({
        "rules": [{
            "tools": ["file_system.write_file"],
            "decision": "deny",
            "allowlist": [],
            "denylist": [],
            "sensitive": [],
        }],
        "default": "allow",
    });
    let mut runtime =
        SynchronousRuntime::new(serde_json::from_value(config_value).expect("valid policy"));
    let completion = runtime.start_turn(turn_id, "read every page");
    let mut read = runtime
        .apply_at(
            run_typescript_completion(action_id(&completion), call_id, source),
            START_MS,
            running(turn_id)
                .dispatch(runtime_tool(
                    "file_system.read_file",
                    json!({"path": "page-0.txt"}),
                ))
                .observe(tool_execution_started(turn_id, call_id)),
        )
        .only_action();
    let page = "x".repeat(64 * 1024);
    for index in 1..40 {
        let result = file_read_success(&read, &page);
        read = runtime
            .apply_at(
                result.clone(),
                START_MS + index * 1_000,
                running(turn_id)
                    .dispatch(runtime_tool(
                        "file_system.read_file",
                        json!({"path": format!("page-{index}.txt")}),
                    ))
                    .observe_tool_result(&read, &result),
            )
            .actions()
            .pop()
            .expect("next read");
    }

    // Do
    let last_result = file_read_success(&read, &page);
    let error = json!({"name": "StoppedEarly", "message": "stopped after reading"});
    let summary = program_call_summary(&[
        "tools.file_system.read_file 1-40: done",
        "console.log 1",
        "tools.file_system.write_file 41: never started",
    ]);
    let content = json!([
        {"type": "text", "text": format!("run_typescript failed: {error}")},
        {"type": "text", "text": summary},
        {"type": "text", "text": "stdout:\nread 40 pages"},
    ]);
    let denied_write_id = format!(
        "{}:tool:40",
        crate::core::testing::typescript_execution_id(3, call_id, 40)
    );
    let denied_write = json!({
        "action_id": crate::core::testing::effect_id_for_operation(
            crate::core::testing::ToolOrigin::Programmatic,
            &denied_write_id,
        ),
        "call_id": denied_write_id,
        "call": {
            "type": "runtime_builtin",
            "name": "file_system.write_file",
            "arguments": {"path": "out.txt", "content": "x"},
        },
    });
    let denial = "Tool call denied by permission policy.";
    let resumed = runtime
        .apply_at(
            last_result.clone(),
            START_MS + 40_000,
            running(turn_id)
                .dispatch(llm_call(1))
                .observe_tool_result(&read, &last_result)
                .observe(tool_result_committed(
                    turn_id,
                    &denied_write,
                    json!({
                        "type": "failure",
                        "content": [{"type": "text", "text": denial}],
                        "error": {
                            "code": "permission_denied",
                            "message": denial,
                            "retryable": false,
                            "details": null,
                        },
                    }),
                ))
                .observe_tool_execution_finished(
                    call_id,
                    json!({
                        "type": "failure",
                        "content": content,
                        "error": {
                            "code": "StoppedEarly",
                            "message": "stopped after reading",
                            "retryable": false,
                            "details": null,
                        },
                    }),
                ),
        )
        .only_action();

    // Assert
    assert!(content.to_string().len() < 1024);
    runtime.assert_last_model_message_update(
        &resumed,
        "append",
        &[
            model_assistant_typescript(call_id, source),
            json!({"role": "tool", "tool_call_id": call_id, "name": "run_typescript", "outcome": "failure", "content": content}),
        ],
    );
}

/// *Prepare*: A program has read one file and logged, then started a second read and asked for approval to write.
/// *Do*: Interrupt the turn, then start the next one.
/// *Assert*: The interrupted result lists the done, still running, and never started calls and the log line, then the stdout.
#[test]
fn interrupted_program_result_says_which_tool_calls_ran() {
    let turn_id = "interrupted-program";
    let call_id = "program";
    let source = r#"async function main() {
      await tools.file_system.read_file({ path: "notes.txt" });
      console.log("read notes.txt");
      await Promise.all([
        tools.file_system.read_file({ path: "slow.txt" }),
        tools.file_system.write_file({ path: "out.txt", content: "x" }),
      ]);
      return "done";
    }"#;
    let mut runtime = SynchronousRuntime::new(ask_config("file_system.write_file"));
    let completion = runtime.start_turn(turn_id, "read and write");
    let first_read = runtime
        .apply_at(
            run_typescript_completion(action_id(&completion), call_id, source),
            START_MS,
            running(turn_id)
                .dispatch(runtime_tool(
                    "file_system.read_file",
                    json!({"path": "notes.txt"}),
                ))
                .observe(tool_execution_started(turn_id, call_id)),
        )
        .only_action();
    let first_read_result = file_read_success(&first_read, "notes");
    let write_operation_id = format!(
        "{}:tool:2",
        crate::core::testing::typescript_execution_id(3, call_id, 1)
    );
    let pending = runtime
        .apply_at(
            first_read_result.clone(),
            START_MS + 1_000,
            running(turn_id)
                .dispatch(runtime_tool(
                    "file_system.read_file",
                    json!({"path": "slow.txt"}),
                ))
                .dispatch(json!({"type": "approval", "call_id": write_operation_id}))
                .observe_tool_result(&first_read, &first_read_result),
        )
        .actions();
    let reason = "user pressed stop";

    // Do
    let mut expected = interrupted(turn_id, reason).observe(turn_interrupted(turn_id, reason));
    for action in &pending {
        expected = expected.observe(action_abandoned(turn_id, action, "interrupt"));
    }
    runtime.apply_at(
        json!({
            "type": "interrupt",
            "expected_turn_id": turn_id,
            "reason": reason,
        }),
        START_MS + 5_000,
        expected,
    );
    let next_turn_id = "after-interrupt";
    let next_completion = runtime
        .apply(
            user_message(next_turn_id, "what happened?", "queue"),
            running(next_turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(next_turn_id, "what happened?")),
        )
        .only_action();

    // Assert
    let summary = program_call_summary(&[
        "tools.file_system.read_file 1: done",
        "console.log 1",
        "tools.file_system.read_file 2: still running, check effects",
        "tools.file_system.write_file 3: never started",
    ]);
    let interrupted_error = json!({
        "result": {
            "type": "error",
            "error": {"name": "Interrupted", "message": reason},
        }
    });
    runtime.assert_last_model_message_update(
        &next_completion,
        "append",
        &[
            model_assistant_typescript(call_id, source),
            json!({
                "role": "tool",
                "tool_call_id": call_id,
                "name": "run_typescript",
                "outcome": "failure",
                "content": [
                    {"type": "text", "text": interrupted_error.to_string()},
                    {"type": "text", "text": summary},
                    {"type": "text", "text": "stdout:\nread notes.txt"},
                ],
            }),
            model_user_text("what happened?"),
        ],
    );
}

/// *Prepare*: A program's file read fails, and its post-tool hook is still running.
/// *Do*: Interrupt the turn, then start the next one.
/// *Assert*: The interrupted result lists the read as failed, not done.
#[test]
fn interrupted_program_result_lists_a_failed_call_waiting_for_its_post_hook_as_failed() {
    let turn_id = "interrupted-post-hook";
    let call_id = "program";
    let source = r#"async function main() {
      await tools.file_system.read_file({ path: "missing.txt" });
      return "done";
    }"#;
    let mut configuration = config();
    configuration.capabilities.hook_bindings = serde_json::from_value(json!([
        {"id": "post-tool", "point": "post_tool_call", "order": 0, "selector": {"type": "always"}}
    ]))
    .unwrap();
    let mut runtime = SynchronousRuntime::new(configuration);
    let completion = runtime.start_turn(turn_id, "read a missing file");
    let read = runtime
        .apply_at(
            run_typescript_completion(action_id(&completion), call_id, source),
            START_MS,
            running(turn_id)
                .dispatch(runtime_tool(
                    "file_system.read_file",
                    json!({"path": "missing.txt"}),
                ))
                .observe(tool_execution_started(turn_id, call_id)),
        )
        .only_action();
    let failure = json!({
        "type": "failure",
        "content": [{"type": "text", "text": "missing.txt was not found"}],
        "error": {
            "code": "not_found",
            "message": "missing.txt was not found",
            "retryable": false,
            "details": null,
        },
    });
    let post_hook = runtime
        .apply_at(
            json!({
                "type": "tool_failed",
                "action_id": read["action_id"],
                "call_id": read["call_id"],
                "result": failure,
            }),
            START_MS + 1_000,
            running(turn_id).dispatch(hook_call(
                "post_tool_call",
                &["post-tool"],
                json!({
                    "tool_call": {"call_id": read["call_id"], "call": read["call"]},
                    "tool_result": failure,
                }),
            )),
        )
        .only_action();
    let reason = "user pressed stop";

    // Do
    runtime.apply_at(
        json!({
            "type": "interrupt",
            "expected_turn_id": turn_id,
            "reason": reason,
        }),
        START_MS + 2_000,
        interrupted(turn_id, reason)
            .observe(turn_interrupted(turn_id, reason))
            .observe(action_abandoned(turn_id, &post_hook, "interrupt")),
    );
    let next_turn_id = "after-interrupt";
    let next_completion = runtime
        .apply(
            user_message(next_turn_id, "what happened?", "queue"),
            running(next_turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(next_turn_id, "what happened?")),
        )
        .only_action();

    // Assert
    let interrupted_error = json!({
        "result": {
            "type": "error",
            "error": {"name": "Interrupted", "message": reason},
        }
    });
    runtime.assert_last_model_message_update(
        &next_completion,
        "append",
        &[
            model_assistant_typescript(call_id, source),
            json!({
                "role": "tool",
                "tool_call_id": call_id,
                "name": "run_typescript",
                "outcome": "failure",
                "content": [
                    {"type": "text", "text": interrupted_error.to_string()},
                    {"type": "text", "text": program_call_summary(&[
                        "tools.file_system.read_file 1: failed, check effects",
                    ])},
                ],
            }),
            model_user_text("what happened?"),
        ],
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
