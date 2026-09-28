use super::*;

///
/// *Prepare*: One turn has a model completion in flight.
/// *Do*: Queue a second turn, then complete the first turn.
/// *Assert*: Core keeps the first Action while queuing and starts the second turn only after the first turn completes.
///
#[test]
fn queued_turn_starts_after_current_turn_completes() {
    // Prepare
    let first_turn_id = "turn-queue-first";
    let second_turn_id = "turn-queue-second";
    let mut runtime = SynchronousRuntime::new(config());
    let first_completion = runtime.start_turn(first_turn_id, "first request");

    // Do
    runtime.apply(
        user_message(second_turn_id, "second request", "queue"),
        running(first_turn_id).keep(&first_completion),
    );
    let second_completion = runtime
        .apply(
            text_completion(&first_completion, "first answer"),
            running(second_turn_id)
                .dispatch(llm_call(0))
                .observe(assistant_text_committed(
                    first_turn_id,
                    &first_completion,
                    "first answer",
                ))
                .observe(turn_completed(first_turn_id, "first answer"))
                .observe(turn_started(second_turn_id, "second request")),
        )
        .only_action();

    // Assert
    runtime.assert_last_model_message_update(
        &second_completion,
        "append",
        &[
            model_assistant_text("first answer"),
            model_user_text("second request"),
        ],
    );
    runtime.finish_turn_with_text(second_turn_id, &second_completion, "second answer");
}

///
/// *Prepare*: A turn has a model completion in flight.
/// *Do*: Interrupt the turn.
/// *Assert*: Core discards the pending candidate, abandons the Runtime Action, and reports the interrupted turn.
///
#[test]
fn interrupting_a_pending_completion_ends_the_turn() {
    // Prepare
    let turn_id = "turn-interrupt-completion";
    let reason = "user pressed stop";
    let mut runtime = SynchronousRuntime::new(config());
    let completion = runtime.start_turn(turn_id, "long running work");

    // Do
    runtime.apply(
        json!({
            "type": "interrupt",
            "expected_turn_id": turn_id,
            "reason": reason,
        }),
        interrupted(turn_id, reason)
            .observe(agent_completion_candidate_discarded(
                turn_id,
                &completion,
                "interrupt",
            ))
            .observe(turn_interrupted(turn_id, reason))
            .observe(action_abandoned(turn_id, &completion, "interrupt")),
    );

    // Assert
    assert_eq!(
        runtime.inspection_value(),
        json!({
            "protocol_version": 1,
            "status": "completed",
            "active_turn_id": null,
            "last_turn_id": turn_id,
            "pending_actions": [],
            "message_count": 2,
            "context_revision": 1,
            "tool_catalog_revision": 0,
            "last_input_id": 2,
        })
    );
}

///
/// *Prepare*: A turn has a model completion in flight.
/// *Do*: Send a context message, then complete the retained model Action with the same input ID.
/// *Assert*: Core rejects the context message without changing state or consuming the input ID.
///
#[test]
fn context_message_is_rejected_while_work_is_pending() {
    // Prepare
    let turn_id = "turn-context-rejection";
    let mut runtime = SynchronousRuntime::new(config());
    let completion = runtime.start_turn(turn_id, "work");

    // Do
    runtime.reject(
        json!({
            "type": "context_message",
            "content": [{"type": "text", "text": "Runtime injected context."}],
        }),
        json!({
            "code": "invalid_state",
            "command_type": "context_message",
            "state": "running",
        }),
    );

    // Assert
    runtime.finish_turn_with_text(turn_id, &completion, "done");
}

fn completion_failed(action: &Value, error: &Value) -> Value {
    json!({
        "type": "completion_failed",
        "action_id": action_id(action),
        "error": error,
    })
}

fn failed_candidate(turn_id: &str, action: &Value, error: &Value) -> Value {
    json!({
        "type": "agent_completion_candidate_discarded",
        "turn_id": turn_id,
        "action_id": action_id(action),
        "cause": {"type": "failure", "error": error},
    })
}

fn turn_failed(turn_id: &str, error: &Value) -> Value {
    json!({
        "type": "turn_failed",
        "turn_id": turn_id,
        "error": error,
    })
}

///
/// *Prepare*: A turn has a model completion in flight.
/// *Do*: Return a retryable provider failure.
/// *Assert*: Core preserves the error, discards the candidate, and reports the failed turn.
///
#[test]
fn completion_failure_ends_the_turn() {
    // Prepare
    let turn_id = "turn-completion-failure";
    let mut runtime = SynchronousRuntime::new(config());
    let completion = runtime.start_turn(turn_id, "work");
    let error = json!({
        "code": "upstream_unavailable",
        "message": "the model provider is unavailable",
        "retryable": true,
        "details": null,
    });

    // Do
    runtime.apply(
        completion_failed(&completion, &error),
        failed(turn_id, error.clone())
            .observe(failed_candidate(turn_id, &completion, &error))
            .observe(turn_failed(turn_id, &error)),
    );

    // Assert
    assert_eq!(
        runtime.inspection_value(),
        json!({
            "protocol_version": 1,
            "status": "failed",
            "active_turn_id": null,
            "last_turn_id": turn_id,
            "pending_actions": [],
            "message_count": 2,
            "context_revision": 1,
            "tool_catalog_revision": 0,
            "last_input_id": 2,
        })
    );
}

///
/// *Prepare*: A turn allows a single model iteration and the model spends it on a tool call.
/// *Do*: Return the tool result so the turn reaches its iteration budget with the tool executed.
/// *Assert*: Core runs the pending tool, then ends the turn with an `iteration_limit` stop
/// reason (not a silent, indistinguishable empty result), and a follow-up turn resumes normally.
///
#[test]
fn final_iteration_tool_call_runs_then_stops_with_a_limit_reason() {
    // Prepare
    let turn_id = "turn-iteration-limit";
    let mut harness_config = config();
    harness_config.settings.turn.max_iterations = Some(1);
    let mut runtime = SynchronousRuntime::new(harness_config);
    let completion = runtime.start_turn(turn_id, "read the file");
    let arguments = json!({"path": "notes.txt"});

    // Do: the only allowed completion issues a tool call, and the tool runs.
    let tool = runtime
        .complete_with_tool_calls(
            turn_id,
            &completion,
            &[tool_call("call-final", "read_file", arguments.clone())],
            [runtime_tool("file_system.read_file", arguments.clone())],
        )
        .only_action();
    let tool_result = file_read_success(&tool, "the file body");

    // Assert: the tool result is committed and the turn completes carrying an
    // `iteration_limit` stop reason so Runtimes can surface why it stopped.
    runtime.apply(
        tool_result.clone(),
        completed(turn_id, vec![])
            .observe_tool_result(&tool, &tool_result)
            .observe(tool_execution_finished(
                turn_id,
                "call-final",
                tool_result["result"].clone(),
            ))
            .observe(json!({
                "type": "turn_completed",
                "turn_id": turn_id,
                "output": [],
                "stop_reason": "iteration_limit",
            })),
    );

    // The session is idle, not stranded: a follow-up turn starts its own
    // completion without the user having to nudge it twice.
    runtime.apply(
        user_message("turn-after-limit", "continue", "queue"),
        running("turn-after-limit")
            .dispatch(llm_call(0))
            .observe(turn_started("turn-after-limit", "continue")),
    );
}

///
/// *Prepare*: A turn has no model-iteration limit.
/// *Do*: Complete one model iteration with a tool call and return its result.
/// *Assert*: Core requests another model iteration instead of stopping the turn.
///
#[test]
fn unlimited_turn_continues_after_a_tool_call() {
    // Prepare
    let turn_id = "turn-without-iteration-limit";
    let mut harness_config = config();
    harness_config.settings.turn.max_iterations = None;
    let mut runtime = SynchronousRuntime::new(harness_config);
    let completion = runtime.start_turn(turn_id, "read the file and summarize it");
    assert_eq!(completion["max_iterations"], Value::Null);
    let arguments = json!({"path": "notes.txt"});
    let tool = runtime
        .complete_with_tool_calls(
            turn_id,
            &completion,
            &[tool_call("call-unlimited", "read_file", arguments.clone())],
            [runtime_tool("file_system.read_file", arguments)],
        )
        .only_action();
    let tool_result = file_read_success(&tool, "the file body");

    // Do
    let next_completion = runtime
        .apply(
            tool_result.clone(),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe_tool_result(&tool, &tool_result)
                .observe(tool_execution_finished(
                    turn_id,
                    "call-unlimited",
                    tool_result["result"].clone(),
                )),
        )
        .only_action();

    // Assert
    assert_eq!(next_completion["max_iterations"], Value::Null);
    runtime.finish_turn_with_text(turn_id, &next_completion, "summary");
}
