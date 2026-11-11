use super::*;

#[test]
fn rejects_new_turn_when_another_turn_is_active() {
    let first_turn_id = "turn-queue-first";
    let second_turn_id = "turn-queue-second";
    let mut runtime = SynchronousRuntime::new(config());
    let first_completion = runtime.start_turn(first_turn_id, "first request");

    runtime.reject(
        user_message(second_turn_id, "second request", "queue"),
        json!({
            "code": "invalid_state",
            "command_type": "user_message",
            "state": "running",
        }),
    );
    runtime.finish_turn_with_text(first_turn_id, &first_completion, "first answer");
    let second_completion = runtime
        .apply(
            user_message(second_turn_id, "second request", "queue"),
            running(second_turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(second_turn_id, "second request")),
        )
        .only_action();

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
            "has_pending_model_input": false,
            "last_input_id": 2,
        })
    );
}

///
/// *Prepare*: A turn has a model completion in flight.
/// *Do*: Send a context message and a steer, restart from the checkpoint, then finish the
/// retained completion.
/// *Assert*: Core keeps the completion and holds the context with the steering. The next model
/// call gets the context as a context user message, then the steer; only the steer is observed.
///
#[test]
fn context_message_during_a_turn_waits_with_the_steering() {
    // Prepare
    let turn_id = "turn-context-steer";
    let mut runtime = SynchronousRuntime::new(config());
    let completion = runtime.start_turn(turn_id, "work");

    // Do
    runtime.apply(
        json!({
            "type": "context_message",
            "content": [{"type": "text", "text": "github is down"}],
        }),
        running(turn_id).keep(&completion),
    );
    runtime.receive_steering(turn_id, "open github", &[&completion]);
    runtime.restart_from_checkpoint();
    let follow_up = runtime
        .apply(
            text_completion(&completion, "working"),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(assistant_text_committed(turn_id, &completion, "working"))
                .observe(turn_steered(turn_id, "open github")),
        )
        .only_action();

    // Assert
    runtime.assert_last_model_message_update(
        &follow_up,
        "replace",
        &[
            model_system(),
            model_user_text("work"),
            model_assistant_text("working"),
            model_context_text("github is down"),
            model_user_text("open github"),
        ],
    );
    runtime.finish_turn_with_text(turn_id, &follow_up, "done");
}

///
/// *Prepare*: A turn has a model completion in flight.
/// *Do*: Send a steer, then a context message with a second steer, then finish the retained
/// completion.
/// *Assert*: The next model call gets the context right before the steer it came with, after the
/// earlier steer.
///
#[test]
fn context_message_with_a_later_steer_stays_after_earlier_steering() {
    // Prepare
    let turn_id = "turn-context-order";
    let mut runtime = SynchronousRuntime::new(config());
    let completion = runtime.start_turn(turn_id, "work");

    // Do
    runtime.receive_steering(turn_id, "first steering", &[&completion]);
    runtime.apply(
        json!({
            "type": "context_message",
            "content": [{"type": "text", "text": "last context"}],
        }),
        running(turn_id).keep(&completion),
    );
    runtime.receive_steering(turn_id, "continue", &[&completion]);
    let follow_up = runtime
        .apply(
            text_completion(&completion, "working"),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(assistant_text_committed(turn_id, &completion, "working"))
                .observe(turn_steered(turn_id, "first steering"))
                .observe(turn_steered(turn_id, "continue")),
        )
        .only_action();

    // Assert
    runtime.assert_last_model_message_update(
        &follow_up,
        "append",
        &[
            model_assistant_text("working"),
            model_user_text("first steering"),
            model_context_text("last context"),
            model_user_text("continue"),
        ],
    );
    runtime.finish_turn_with_text(turn_id, &follow_up, "done");
}

///
/// *Prepare*: A turn has a model completion in flight.
/// *Do*: Steer the turn, send a context message, then complete the model call.
/// *Assert*: The next model call gets the context after the steer that came before it.
///
#[test]
fn context_message_after_a_steer_stays_after_it() {
    // Prepare
    let turn_id = "turn-context-after-steer";
    let mut runtime = SynchronousRuntime::new(config());
    let completion = runtime.start_turn(turn_id, "work");

    // Do
    runtime.receive_steering(turn_id, "first steering", &[&completion]);
    runtime.apply(
        json!({
            "type": "context_message",
            "content": [{"type": "text", "text": "later context"}],
        }),
        running(turn_id).keep(&completion),
    );
    let follow_up = runtime
        .apply(
            text_completion(&completion, "working"),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(assistant_text_committed(turn_id, &completion, "working"))
                .observe(turn_steered(turn_id, "first steering")),
        )
        .only_action();

    // Assert
    runtime.assert_last_model_message_update(
        &follow_up,
        "append",
        &[
            model_assistant_text("working"),
            model_user_text("first steering"),
            model_context_text("later context"),
        ],
    );
    runtime.finish_turn_with_text(turn_id, &follow_up, "done");
}

///
/// *Prepare*: A turn has a model completion in flight.
/// *Do*: Send a context message, let the completion finish the turn, then start the next turn.
/// *Assert*: Core keeps the completion, the context does not extend the finished turn, and the
/// next model call reads it before the next user message.
///
#[test]
fn context_message_during_a_final_completion_waits_for_the_next_turn() {
    // Prepare
    let turn_id = "turn-context-in-flight";
    let mut runtime = SynchronousRuntime::new(config());
    let completion = runtime.start_turn(turn_id, "work");

    // Do
    runtime.apply(
        json!({
            "type": "context_message",
            "content": [{"type": "text", "text": "Runtime injected context."}],
        }),
        running(turn_id).keep(&completion),
    );
    runtime.finish_turn_with_text(turn_id, &completion, "done");
    let next_turn_id = "turn-after-context";
    let next = runtime
        .apply(
            user_message(next_turn_id, "next", "queue"),
            running(next_turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(next_turn_id, "next")),
        )
        .only_action();

    // Assert
    runtime.assert_last_model_message_update(
        &next,
        "append",
        &[
            model_assistant_text("done"),
            model_context_text("Runtime injected context."),
            model_user_text("next"),
        ],
    );
}

///
/// *Prepare*: A turn that retries cut-off completions has a model completion in flight.
/// *Do*: Send a context message and a steer, as a Runtime does for a steer with attached context,
/// then cut off the completion at the output limit and answer the retry.
/// *Assert*: The retried model call gets only the retry feedback. The call after it gets the
/// context right before its steer.
///
#[test]
fn context_message_stays_with_its_steer_when_a_completion_is_retried() {
    // Prepare
    let turn_id = "turn-context-steer-retry";
    let mut settings = config();
    settings.settings.turn = serde_json::from_value(json!({
        "max_iterations": 8,
        "output_limit_recovery": {"mode": "retry", "max_retries": 2},
    }))
    .expect("turn settings deserialize");
    let mut runtime = SynchronousRuntime::new(settings);
    let completion = runtime.start_turn(turn_id, "write the report");

    // Do
    runtime.apply(
        json!({
            "type": "context_message",
            "content": [{"type": "text", "text": "the selected repository is acme/api"}],
        }),
        running(turn_id).keep(&completion),
    );
    runtime.receive_steering(turn_id, "switch to the selected repository", &[&completion]);
    let retry = runtime
        .apply(
            json!({
                "type": "completion_succeeded",
                "action_id": action_id(&completion),
                "result": {
                    "parts": [{"type": "text", "text": "first fragment"}],
                    "finish_reason": "length",
                    "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120, "cached_input_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0},
                },
            }),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(agent_completion_candidate_discarded(turn_id, &completion, "retry")),
        )
        .only_action();
    runtime.assert_last_model_message_update(
        &retry,
        "append",
        &[
            model_user_text("Your previous response was discarded because it reached the output token limit after 20 tokens. Give a shorter answer; put long content in a file with a tool call if needed."),
        ],
    );
    let follow_up = runtime
        .apply(
            text_completion(&retry, "short answer"),
            running(turn_id)
                .dispatch(llm_call(2))
                .observe(assistant_text_committed(turn_id, &retry, "short answer"))
                .observe(turn_steered(turn_id, "switch to the selected repository")),
        )
        .only_action();

    // Assert
    runtime.assert_last_model_message_update(
        &follow_up,
        "append",
        &[
            model_assistant_text("short answer"),
            model_context_text("the selected repository is acme/api"),
            model_user_text("switch to the selected repository"),
        ],
    );
    runtime.finish_turn_with_text(turn_id, &follow_up, "switched");
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
            "has_pending_model_input": false,
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
            .observe(turn_completed_at_iteration_limit(turn_id, vec![])),
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
/// *Prepare*: A turn allows a single model iteration.
/// *Do*: The model writes text next to its tool call, and the tool result arrives.
/// *Assert*: The turn stops at the limit and its output is that text alone, as a final
/// answer would carry it: no tool call and no tool result.
///
#[test]
fn iteration_limit_output_is_the_latest_assistant_text() {
    // Prepare
    let turn_id = "turn-iteration-limit-with-text";
    let mut harness_config = config();
    harness_config.settings.turn.max_iterations = Some(1);
    let mut runtime = SynchronousRuntime::new(harness_config);
    let completion = runtime.start_turn(turn_id, "read the file");
    let arguments = json!({"path": "notes.txt"});
    let call = tool_call("call-final", "read_file", arguments.clone());
    let text = json!({"type": "text", "text": "Reading the notes next."});
    let mut candidate = tool_calls_candidate(std::slice::from_ref(&call));
    candidate["message"]["content"]
        .as_array_mut()
        .expect("candidate content is a list")
        .insert(0, text.clone());

    // Do
    let mut parts = vec![text.clone()];
    parts.push(call);
    let tool = runtime
        .apply(
            json!({
                "type": "completion_succeeded",
                "action_id": action_id(&completion),
                "result": {"parts": parts, "finish_reason": "tool_call", "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120, "cached_input_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0}},
            }),
            running(turn_id)
                .dispatch(runtime_tool("file_system.read_file", arguments))
                .observe(json!({
                    "type": "assistant_message_committed",
                    "turn_id": turn_id,
                    "action_id": action_id(&completion),
                    "candidate": candidate,
                }))
                .observe(tool_execution_started(turn_id, "call-final")),
        )
        .only_action();
    let tool_result = file_read_success(&tool, "the file body");

    // Assert
    runtime.apply(
        tool_result.clone(),
        completed(turn_id, vec![text.clone()])
            .observe_tool_result(&tool, &tool_result)
            .observe(tool_execution_finished(
                turn_id,
                "call-final",
                tool_result["result"].clone(),
            ))
            .observe(turn_completed_at_iteration_limit(turn_id, vec![text])),
    );
}

///
/// *Prepare*: The model wrote text next to a tool call, and the tool is running.
/// *Do*: Interrupt the turn.
/// *Assert*: The interrupted turn reports that text alone as its output: no tool call and no
/// tool result.
///
#[test]
fn interrupted_turn_output_is_the_latest_assistant_text() {
    // Prepare
    let turn_id = "turn-interrupted-with-text";
    let reason = "user pressed stop";
    let mut runtime = SynchronousRuntime::new(config());
    let completion = runtime.start_turn(turn_id, "read the file");
    let arguments = json!({"path": "notes.txt"});
    let call = tool_call("call-running", "read_file", arguments.clone());
    let text = json!({"type": "text", "text": "Reading the notes next."});
    let mut candidate = tool_calls_candidate(std::slice::from_ref(&call));
    candidate["message"]["content"]
        .as_array_mut()
        .expect("candidate content is a list")
        .insert(0, text.clone());
    let tool = runtime
        .apply(
            json!({
                "type": "completion_succeeded",
                "action_id": action_id(&completion),
                "result": {"parts": [text.clone(), call], "finish_reason": "tool_call", "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120, "cached_input_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0}},
            }),
            running(turn_id)
                .dispatch(runtime_tool("file_system.read_file", arguments))
                .observe(json!({
                    "type": "assistant_message_committed",
                    "turn_id": turn_id,
                    "action_id": action_id(&completion),
                    "candidate": candidate,
                }))
                .observe(tool_execution_started(turn_id, "call-running")),
        )
        .only_action();

    // Do / Assert
    runtime.apply(
        json!({
            "type": "interrupt",
            "expected_turn_id": turn_id,
            "reason": reason,
        }),
        interrupted(turn_id, reason)
            .observe(json!({
                "type": "turn_interrupted",
                "turn_id": turn_id,
                "reason": reason,
                "output": [text],
            }))
            .observe(action_abandoned(turn_id, &tool, "interrupt")),
    );
}

#[test]
fn completes_final_answer_at_iteration_limit_without_a_stop_reason() {
    let turn_id = "turn-final-answer-at-iteration-limit";
    let mut harness_config = config();
    harness_config.settings.turn.max_iterations = Some(1);
    let mut runtime = SynchronousRuntime::new(harness_config);
    let completion = runtime.start_turn(turn_id, "answer once");

    runtime.finish_turn_with_text(turn_id, &completion, "the accepted answer");
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
