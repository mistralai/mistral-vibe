use super::*;

/// Host time of the step that dispatches the tool Action in each scenario.
const DISPATCHED_AT_MS: u64 = 1_700_000_100_000;
const BINDING: &str = "observe-result";
const PROGRAM_SOURCE: &str = r#"async function main() {
  await tools.file_system.read_file({ path: "notes.txt" });
  return "done";
}"#;

/// An earlier V1 checkpoint: a direct read of `first.txt` awaits its post-tool
/// hook, and a direct read of `second.txt` is pending.
const EARLIER_BATCH_CHECKPOINT: &str = r##"{
  "checkpoint_version": 1,
  "context": {
    "messages": [
      {
        "message": {
          "role": "user",
          "content": [{"type": "text", "text": "read both notes"}]
        },
        "source": "history",
        "turn_index": 0
      },
      {
        "message": {
          "role": "assistant",
          "content": [
            {
              "type": "tool_call",
              "id": "first",
              "name": "read_file",
              "arguments": {
                "type": "json",
                "raw": "{\"path\":\"first.txt\"}",
                "value": {"path": "first.txt"}
              }
            },
            {
              "type": "tool_call",
              "id": "second",
              "name": "read_file",
              "arguments": {
                "type": "json",
                "raw": "{\"path\":\"second.txt\"}",
                "value": {"path": "second.txt"}
              }
            }
          ]
        },
        "source": "history",
        "turn_index": 0
      }
    ]
  },
  "compaction_count": 0,
  "last_reported_context_tokens": 120,
  "turn": {
    "type": "active",
    "turn_id": "earlier-batch",
    "iterations": 1,
    "phase": {
      "type": "awaiting_tool_batch",
      "batch": {
        "executions": [
          {
            "type": "direct_awaiting_post_hook",
            "hook_binding_ids": ["observe-result"],
            "call": {
              "type": "runtime_builtin",
              "name": "file_system.read_file",
              "arguments": {"path": "first.txt"}
            },
            "result": {
              "type": "success",
              "content": [{"type": "text", "text": "first notes"}]
            }
          },
          {
            "type": "direct_pending",
            "call": {
              "type": "runtime_builtin",
              "name": "file_system.read_file",
              "arguments": {"path": "second.txt"}
            }
          }
        ]
      }
    },
    "queued": {"type": "empty"},
    "pending_steer": [],
    "model_input_ready": false
  },
  "notifications": []
}"##;

/// An earlier V1 checkpoint: a `run_typescript` program waits for its read of
/// `notes.txt`.
const EARLIER_PROGRAM_CHECKPOINT: &str = r##"{
  "checkpoint_version": 1,
  "context": {
    "messages": [
      {
        "message": {
          "role": "user",
          "content": [{"type": "text", "text": "read the notes"}]
        },
        "source": "history",
        "turn_index": 0
      },
      {
        "message": {
          "role": "assistant",
          "content": [
            {
              "type": "tool_call",
              "id": "program",
              "name": "run_typescript",
              "arguments": {
                "type": "json",
                "raw": "{\"code\":\"async function main() {\\n  await tools.file_system.read_file({ path: \\\"notes.txt\\\" });\\n  return \\\"done\\\";\\n}\"}",
                "value": {
                  "code": "async function main() {\n  await tools.file_system.read_file({ path: \"notes.txt\" });\n  return \"done\";\n}"
                }
              }
            }
          ]
        },
        "source": "history",
        "turn_index": 0
      }
    ]
  },
  "compaction_count": 0,
  "last_reported_context_tokens": 120,
  "turn": {
    "type": "active",
    "turn_id": "earlier-program",
    "iterations": 1,
    "phase": {
      "type": "awaiting_tool_batch",
      "batch": {
        "executions": [
          {
            "type": "program_pending",
            "execution": {
              "operations": [
                {
                  "type": "external",
                  "id": "typescript:3:program:0:tool:0",
                  "function": {"name": "read_file", "arguments": {"path": "notes.txt"}},
                  "execution": {
                    "type": "pending",
                    "call": {
                      "type": "runtime_builtin",
                      "name": "file_system.read_file",
                      "arguments": {"path": "notes.txt"}
                    }
                  }
                }
              ],
              "round": 0,
              "started_at_unix_ms": 1700000100000
            }
          }
        ]
      }
    },
    "queued": {"type": "empty"},
    "pending_steer": [],
    "model_input_ready": false
  },
  "notifications": []
}"##;

/// *Prepare*: A post-tool hook is bound, and Core dispatches a direct file read.
/// *Do*: The Runtime reports the read as succeeded 250 ms after the dispatching step.
/// *Assert*: The post-tool hook input carries `duration_ms` 250.
#[test]
fn post_tool_hook_receives_the_duration_of_a_succeeded_direct_call() {
    // Prepare
    let turn_id = "timed-success";
    let mut runtime = SynchronousRuntime::new(post_tool_config());
    let read = dispatch_direct_read(&mut runtime, turn_id);
    let result = text_tool_success(&read, "notes");

    // Do
    let post_hook = runtime
        .apply_at(
            result.clone(),
            DISPATCHED_AT_MS + 250,
            running(turn_id).dispatch(post_tool_hook(&read, &result, 250)),
        )
        .only_action();

    // Assert
    finish_direct_read(&mut runtime, turn_id, &read, &post_hook, &result);
    assert_eq!(post_hook["input"]["duration_ms"], 250);
}

/// *Prepare*: A post-tool hook is bound, and Core dispatches a direct file read.
/// *Do*: The Runtime reports the read as failed 400 ms after the dispatching step.
/// *Assert*: The post-tool hook input carries `duration_ms` 400.
#[test]
fn post_tool_hook_receives_the_duration_of_a_failed_direct_call() {
    // Prepare
    let turn_id = "timed-failure";
    let mut runtime = SynchronousRuntime::new(post_tool_config());
    let read = dispatch_direct_read(&mut runtime, turn_id);
    let result = tool_failure(&read);

    // Do
    let post_hook = runtime
        .apply_at(
            result.clone(),
            DISPATCHED_AT_MS + 400,
            running(turn_id).dispatch(post_tool_hook(&read, &result, 400)),
        )
        .only_action();

    // Assert
    finish_direct_read(&mut runtime, turn_id, &read, &post_hook, &result);
    assert_eq!(post_hook["input"]["duration_ms"], 400);
}

/// *Prepare*: A post-tool hook is bound, and Core dispatches a direct file read.
/// *Do*: The Runtime reports the read with a Host time 500 ms before the dispatching step.
/// *Assert*: The post-tool hook input carries `duration_ms` 0, not a negative value.
#[test]
fn post_tool_hook_duration_is_zero_when_host_time_goes_backwards() {
    // Prepare
    let turn_id = "timed-backwards";
    let mut runtime = SynchronousRuntime::new(post_tool_config());
    let read = dispatch_direct_read(&mut runtime, turn_id);
    let result = text_tool_success(&read, "notes");

    // Do
    let post_hook = runtime
        .apply_at(
            result.clone(),
            DISPATCHED_AT_MS - 500,
            running(turn_id).dispatch(post_tool_hook(&read, &result, 0)),
        )
        .only_action();

    // Assert
    finish_direct_read(&mut runtime, turn_id, &read, &post_hook, &result);
    assert_eq!(post_hook["input"]["duration_ms"], 0);
}

/// *Prepare*: A post-tool hook is bound, and a `run_typescript` program makes Core dispatch a file read.
/// *Do*: The Runtime reports the read as succeeded 1,234 ms after the dispatching step.
/// *Assert*: The post-tool hook input for the program's call carries `duration_ms` 1,234.
#[test]
fn post_tool_hook_receives_the_duration_of_a_program_call() {
    // Prepare
    let turn_id = "timed-program";
    let mut runtime = SynchronousRuntime::new(post_tool_config());
    let read = dispatch_program_read(&mut runtime, turn_id);
    let result = text_tool_success(&read, "notes");

    // Do
    let post_hook = runtime
        .apply_at(
            result.clone(),
            DISPATCHED_AT_MS + 1_234,
            running(turn_id).dispatch(post_tool_hook(&read, &result, 1_234)),
        )
        .only_action();

    // Assert
    finish_program_read(&mut runtime, turn_id, &read, &post_hook, &result);
    assert_eq!(post_hook["input"]["duration_ms"], 1_234);
}

/// *Prepare*: A post-tool hook is bound, and Core dispatches a direct file read.
/// *Do*: The Runtime restarts from a checkpoint while the read is pending, then reports it as succeeded 300 ms after the dispatching step.
/// *Assert*: The post-tool hook input carries `duration_ms` 300, as without the restart.
#[test]
fn direct_call_duration_survives_a_checkpoint_taken_while_the_call_is_pending() {
    // Prepare
    let turn_id = "timed-restart-pending";
    let mut runtime = SynchronousRuntime::new(post_tool_config());
    let read = dispatch_direct_read(&mut runtime, turn_id);
    let result = text_tool_success(&read, "notes");

    // Do
    runtime.restart_from_checkpoint();
    let post_hook = runtime
        .apply_at(
            result.clone(),
            DISPATCHED_AT_MS + 300,
            running(turn_id).dispatch(post_tool_hook(&read, &result, 300)),
        )
        .only_action();

    // Assert
    finish_direct_read(&mut runtime, turn_id, &read, &post_hook, &result);
    assert_eq!(post_hook["input"]["duration_ms"], 300);
}

/// *Prepare*: A post-tool hook is bound, and a `run_typescript` program makes Core dispatch a file read.
/// *Do*: The Runtime restarts from a checkpoint while the read is pending, then reports it as succeeded 300 ms after the dispatching step.
/// *Assert*: The post-tool hook input carries `duration_ms` 300, as without the restart.
#[test]
fn program_call_duration_survives_a_checkpoint_taken_while_the_call_is_pending() {
    // Prepare
    let turn_id = "timed-program-restart";
    let mut runtime = SynchronousRuntime::new(post_tool_config());
    let read = dispatch_program_read(&mut runtime, turn_id);
    let result = text_tool_success(&read, "notes");

    // Do
    runtime.restart_from_checkpoint();
    let post_hook = runtime
        .apply_at(
            result.clone(),
            DISPATCHED_AT_MS + 300,
            running(turn_id).dispatch(post_tool_hook(&read, &result, 300)),
        )
        .only_action();

    // Assert
    finish_program_read(&mut runtime, turn_id, &read, &post_hook, &result);
    assert_eq!(post_hook["input"]["duration_ms"], 300);
}

/// *Prepare*: A post-tool hook is bound, and the hook for a direct read that took 300 ms is pending.
/// *Do*: The Runtime restarts from a checkpoint while the hook is pending.
/// *Assert*: The restored Core captures the same checkpoint, and the hook completes the turn.
#[test]
fn direct_call_duration_survives_a_checkpoint_taken_while_the_post_hook_is_pending() {
    // Prepare
    let turn_id = "timed-restart-hook";
    let mut runtime = SynchronousRuntime::new(post_tool_config());
    let read = dispatch_direct_read(&mut runtime, turn_id);
    let result = text_tool_success(&read, "notes");
    let post_hook = runtime
        .apply_at(
            result.clone(),
            DISPATCHED_AT_MS + 300,
            running(turn_id).dispatch(post_tool_hook(&read, &result, 300)),
        )
        .only_action();
    let checkpoint = runtime.checkpoint_value();

    // Do
    runtime.restart_from_checkpoint();

    // Assert
    assert_eq!(runtime.checkpoint_value(), checkpoint);
    finish_direct_read(&mut runtime, turn_id, &read, &post_hook, &result);
    assert_eq!(post_hook["input"]["duration_ms"], 300);
}

/// *Prepare*: Restore an earlier V1 checkpoint without recorded dispatch times: one direct read awaits its post-tool hook and a second read is pending.
/// *Do*: Complete the first hook, then report the second read as succeeded.
/// *Assert*: Both restore without error, and the second read's post-tool hook input carries a non-negative `duration_ms`.
#[test]
fn earlier_checkpoint_without_dispatch_times_restores_a_pending_post_hook_and_a_pending_call() {
    // Prepare
    let turn_id = "earlier-batch";
    let first = json!({
        "type": "runtime_builtin_tool_call",
        "action_id": "938fe016-ad1f-5301-8ab0-bb7523b13e58",
        "turn_id": turn_id,
        "call_id": "first",
        "call": {
            "type": "runtime_builtin",
            "name": "file_system.read_file",
            "arguments": {"path": "first.txt"},
        },
    });
    let second = json!({
        "type": "runtime_builtin_tool_call",
        "action_id": "f92dca60-79d9-58e0-b3d8-7681175c43ed",
        "turn_id": turn_id,
        "call_id": "second",
        "call": {
            "type": "runtime_builtin",
            "name": "file_system.read_file",
            "arguments": {"path": "second.txt"},
        },
    });
    let first_result = text_tool_success(&first, "first notes");
    let first_hook = json!({
        "type": "hook_call",
        "action_id": "d7c07d72-507f-52b1-9b6c-818af1d50e7e",
        "turn_id": turn_id,
        "hook_binding_ids": [BINDING],
        "hook": "post_tool_call",
        "input": {
            "tool_call": {
                "action_id": first["action_id"],
                "call_id": "first",
                "call": first["call"],
            },
            "tool_result": first_result["result"],
        },
    });
    let mut runtime = SynchronousRuntime::from_checkpoint(
        post_tool_config(),
        EARLIER_BATCH_CHECKPOINT,
        vec![second.clone(), first_hook.clone()],
    );
    let second_result = text_tool_success(&second, "second notes");

    // Do
    runtime.apply_at(
        hook_completed(
            &first_hook,
            "post_tool_call",
            json!({"tool_result": first_result["result"]}),
        ),
        DISPATCHED_AT_MS + 150,
        running(turn_id)
            .keep(&second)
            .observe_completed_tool_result(&first, &first_result),
    );
    let second_hook = runtime
        .apply_at(
            second_result.clone(),
            DISPATCHED_AT_MS + 200,
            running(turn_id).dispatch(hook_call(
                "post_tool_call",
                &[BINDING],
                json!({
                    "tool_call": {"call_id": "second", "call": second["call"]},
                    "tool_result": second_result["result"],
                }),
            )),
        )
        .only_action();

    // Assert
    finish_direct_read(&mut runtime, turn_id, &second, &second_hook, &second_result);
    assert!(
        second_hook["input"]["duration_ms"].is_u64(),
        "post-tool hook input has no non-negative duration: {second_hook}"
    );
}

/// *Prepare*: Restore an earlier V1 checkpoint without recorded dispatch times, in which a program's file read is pending.
/// *Do*: Report the read as succeeded.
/// *Assert*: The post-tool hook input carries a non-negative `duration_ms`, and the program finishes.
#[test]
fn earlier_checkpoint_without_dispatch_times_restores_a_pending_program_call() {
    // Prepare
    let turn_id = "earlier-program";
    let read = json!({
        "type": "runtime_builtin_tool_call",
        "action_id": "14dc286c-889c-5b77-8a90-44f8758e3cc2",
        "turn_id": turn_id,
        "call_id": "typescript:3:program:0:tool:0",
        "call": {
            "type": "runtime_builtin",
            "name": "file_system.read_file",
            "arguments": {"path": "notes.txt"},
        },
        "parent_call_id": "program",
    });
    let mut runtime = SynchronousRuntime::from_checkpoint(
        post_tool_config(),
        EARLIER_PROGRAM_CHECKPOINT,
        vec![read.clone()],
    );
    let result = text_tool_success(&read, "notes");

    // Do
    let post_hook = runtime
        .apply_at(
            result.clone(),
            DISPATCHED_AT_MS + 200,
            running(turn_id).dispatch(hook_call(
                "post_tool_call",
                &[BINDING],
                json!({
                    "tool_call": {"call_id": read["call_id"], "call": read["call"]},
                    "tool_result": result["result"],
                }),
            )),
        )
        .only_action();

    // Assert
    finish_program_read(&mut runtime, turn_id, &read, &post_hook, &result);
    assert!(
        post_hook["input"]["duration_ms"].is_u64(),
        "post-tool hook input has no non-negative duration: {post_hook}"
    );
}

fn post_tool_config() -> HarnessConfig {
    let mut config = config();
    config.capabilities.hook_bindings = serde_json::from_value(json!([{
        "id": BINDING,
        "point": "post_tool_call",
        "order": 0,
        "selector": {"type": "always"},
    }]))
    .expect("post-tool hook configures");
    config
}

/// Starts a turn whose model calls `read_file`; Core dispatches it at [`DISPATCHED_AT_MS`].
fn dispatch_direct_read(runtime: &mut SynchronousRuntime, turn_id: &str) -> Value {
    let completion = runtime.start_turn(turn_id, "read the notes");
    let calls = vec![tool_call("read", "read_file", json!({"path": "notes.txt"}))];
    runtime
        .apply_at(
            tool_call_completion(action_id(&completion), calls.clone()),
            DISPATCHED_AT_MS,
            running(turn_id)
                .dispatch(runtime_tool(
                    "file_system.read_file",
                    json!({"path": "notes.txt"}),
                ))
                .observe(assistant_tool_calls_committed(turn_id, &completion, &calls))
                .observe(tool_execution_started(turn_id, "read")),
        )
        .only_action()
}

/// Starts a turn whose program reads a file; Core dispatches the read at [`DISPATCHED_AT_MS`].
fn dispatch_program_read(runtime: &mut SynchronousRuntime, turn_id: &str) -> Value {
    let completion = runtime.start_turn(turn_id, "read the notes");
    runtime
        .apply_at(
            run_typescript_completion(action_id(&completion), "program", PROGRAM_SOURCE),
            DISPATCHED_AT_MS,
            running(turn_id)
                .dispatch(runtime_tool(
                    "file_system.read_file",
                    json!({"path": "notes.txt"}),
                ))
                .observe(core_tool_execution_started(turn_id, "program")),
        )
        .only_action()
}

fn tool_failure(action: &Value) -> Value {
    json!({
        "type": "tool_failed",
        "action_id": action["action_id"],
        "call_id": action["call_id"],
        "result": {
            "type": "failure",
            "content": [{"type": "text", "text": "notes.txt was not found"}],
            "error": {
                "code": "not_found",
                "message": "notes.txt was not found",
                "retryable": false,
                "details": null,
            },
        },
    })
}

fn post_tool_hook(action: &Value, command: &Value, duration_ms: u64) -> Value {
    hook_call(
        "post_tool_call",
        &[BINDING],
        json!({
            "tool_call": {"call_id": action["call_id"], "call": action["call"]},
            "tool_result": command["result"],
            "duration_ms": duration_ms,
        }),
    )
}

/// Returns the hook output unchanged, then answers with text.
fn finish_direct_read(
    runtime: &mut SynchronousRuntime,
    turn_id: &str,
    read: &Value,
    post_hook: &Value,
    command: &Value,
) {
    let completion = runtime
        .apply(
            hook_completed(
                post_hook,
                "post_tool_call",
                json!({"tool_result": command["result"]}),
            ),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe_completed_tool_result(read, command),
        )
        .only_action();
    runtime.finish_turn_with_text(turn_id, &completion, "read");
}

/// Returns the hook output unchanged, lets the program finish, then answers with text.
fn finish_program_read(
    runtime: &mut SynchronousRuntime,
    turn_id: &str,
    read: &Value,
    post_hook: &Value,
    command: &Value,
) {
    let completion = runtime
        .apply(
            hook_completed(
                post_hook,
                "post_tool_call",
                json!({"tool_result": command["result"]}),
            ),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe_tool_result(read, command)
                .observe_tool_execution_finished("program", run_typescript_success(json!("done"))),
        )
        .only_action();
    runtime.finish_turn_with_text(turn_id, &completion, "read");
}
