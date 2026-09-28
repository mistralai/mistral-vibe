use super::*;

///
/// *Prepare*: A turn has one model completion in flight.
/// *Do*: Steer before that completion returns, then finish the retained completion.
/// *Assert*: Core keeps the original completion, commits its tool-free assistant message,
/// inserts steering after it, and continues the same turn with one new completion.
///
#[test]
fn steering_while_completion_is_running_waits_for_its_assistant_message() {
    // Prepare
    let turn_id = "turn-steer-completion";
    let mut runtime = SynchronousRuntime::new(config());
    let original_completion = runtime.start_turn(turn_id, "first direction");

    // Do
    runtime.receive_steering(turn_id, "changed direction", &[&original_completion]);
    runtime.restart_from_checkpoint();
    let follow_up_completion = runtime
        .apply(
            text_completion(&original_completion, "answer before steering"),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(assistant_text_committed(
                    turn_id,
                    &original_completion,
                    "answer before steering",
                ))
                .observe(turn_steered(turn_id, "changed direction")),
        )
        .only_action();
    runtime.assert_last_model_message_update(
        &follow_up_completion,
        "replace",
        &[
            model_system(),
            model_user_text("first direction"),
            model_assistant_text("answer before steering"),
            model_user_text("changed direction"),
        ],
    );
    runtime.finish_turn_with_text(turn_id, &follow_up_completion, "changed answer");

    // Assert
    assert_ne!(
        action_id(&original_completion),
        action_id(&follow_up_completion)
    );
}

///
/// *Prepare*: A post-LLM hook is running for a text completion candidate.
/// *Do*: Steer before the hook returns, then accept the retained candidate.
/// *Assert*: Core keeps the hook, commits the accepted assistant message, inserts steering
/// after it, and runs the normal post-LLM hook pipeline for the follow-up completion.
///
#[test]
fn steering_while_hook_is_running_waits_for_the_accepted_assistant_message() {
    // Prepare
    let turn_id = "turn-steer-hook";
    let mut runtime = SynchronousRuntime::new(config_with_post_llm_hook());
    let original_completion = runtime.start_turn(turn_id, "first direction");
    let retained_hook = runtime
        .apply(
            text_completion(&original_completion, "draft before steering"),
            running(turn_id).dispatch(hook_call(
                "post_llm_call",
                &["test-hook-0-PostLlmCall"],
                json!({"candidate": text_candidate("draft before steering")}),
            )),
        )
        .only_action();

    // Do
    runtime.receive_steering(turn_id, "changed direction", &[&retained_hook]);
    runtime.restart_from_checkpoint();
    let follow_up_completion = runtime
        .apply(
            hook_completed(
                &retained_hook,
                "post_llm_call",
                json!({"type": "accept", "acceptance": {"type": "candidate"}}),
            ),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(assistant_text_committed(
                    turn_id,
                    &original_completion,
                    "draft before steering",
                ))
                .observe(turn_steered(turn_id, "changed direction")),
        )
        .only_action();
    runtime.assert_last_model_message_update(
        &follow_up_completion,
        "replace",
        &[
            model_system(),
            model_user_text("first direction"),
            model_assistant_text("draft before steering"),
            model_user_text("changed direction"),
        ],
    );
    let follow_up_hook = runtime
        .apply(
            text_completion(&follow_up_completion, "changed answer"),
            running(turn_id).dispatch(hook_call(
                "post_llm_call",
                &["test-hook-0-PostLlmCall"],
                json!({"candidate": text_candidate("changed answer")}),
            )),
        )
        .only_action();
    runtime.apply(
        hook_completed(
            &follow_up_hook,
            "post_llm_call",
            json!({"type": "accept", "acceptance": {"type": "candidate"}}),
        ),
        completed(
            turn_id,
            vec![json!({"type": "text", "text": "changed answer"})],
        )
        .observe(assistant_text_committed(
            turn_id,
            &follow_up_completion,
            "changed answer",
        ))
        .observe(turn_completed(turn_id, "changed answer")),
    );

    // Assert
    assert_eq!(retained_hook["hook"], "post_llm_call");
    assert_eq!(follow_up_hook["hook"], "post_llm_call");
}

///
/// *Prepare*: Two direct filesystem Actions from one model tool-call batch are executing in parallel.
/// *Do*: Steer while both remain in flight, then return their results one at a time.
/// *Assert*: Core keeps both tools, waits for the complete batch, inserts steering after both
/// model-visible tool messages, and continues the same turn with one completion.
///
#[test]
fn steering_while_tools_are_running_waits_for_the_complete_tool_batch() {
    // Prepare
    let turn_id = "turn-steer-tool";
    let mut runtime = SynchronousRuntime::new(config());
    let original_completion = runtime.start_turn(turn_id, "read both files");
    let calls = vec![
        tool_call("steer-tool-a", "read_file", json!({"path": "a.txt"})),
        tool_call("steer-tool-b", "read_file", json!({"path": "b.txt"})),
    ];
    let tools = runtime
        .complete_with_tool_calls(
            turn_id,
            &original_completion,
            &calls,
            [
                runtime_tool("file_system.read_file", json!({"path": "a.txt"})),
                runtime_tool("file_system.read_file", json!({"path": "b.txt"})),
            ],
        )
        .actions();
    let [first_tool, second_tool]: [Value; 2] = tools.try_into().expect("two tools are running");

    // Do
    runtime.receive_steering(
        turn_id,
        "change direction after both results",
        &[&first_tool, &second_tool],
    );
    runtime.restart_from_checkpoint();
    let first_result = text_tool_success(&first_tool, "a contents");
    runtime.apply(
        first_result.clone(),
        running(turn_id)
            .keep(&second_tool)
            .observe_completed_tool_result(&first_tool, &first_result),
    );
    let second_result = text_tool_success(&second_tool, "b contents");
    let follow_up_completion = runtime
        .apply(
            second_result.clone(),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe_completed_tool_result(&second_tool, &second_result)
                .observe(turn_steered(turn_id, "change direction after both results")),
        )
        .only_action();
    runtime.assert_last_model_message_update(
        &follow_up_completion,
        "replace",
        &[
            model_system(),
            model_user_text("read both files"),
            model_assistant_tool_calls(&calls),
            model_tool_text("steer-tool-a", "read_file", "a contents"),
            model_tool_text("steer-tool-b", "read_file", "b contents"),
            model_user_text("change direction after both results"),
        ],
    );
    runtime.finish_turn_with_text(
        turn_id,
        &follow_up_completion,
        "redirected after both tools",
    );

    // Assert
    assert_eq!(first_tool["call_id"], "steer-tool-a");
    assert_eq!(second_tool["call_id"], "steer-tool-b");
}

///
/// *Prepare*: One completion is pending for an active turn.
/// *Do*: Receive two steering messages with one notification between them, then restore and finish the retained completion.
/// *Assert*: Core delivers steering in arrival order before the notification and emits one delivery Observation for each input.
///
#[test]
fn multiple_steering_messages_keep_order_before_notifications() {
    // Prepare
    let turn_id = "turn-multiple-steering";
    let mut runtime = SynchronousRuntime::new(config());
    let original_completion = runtime.start_turn(turn_id, "prepare the first answer");
    let notification = notification(
        "notification-between-steering",
        "background evidence is ready",
    );

    // Do
    runtime.receive_steering(turn_id, "use the first correction", &[&original_completion]);
    runtime.receive_notification(turn_id, &notification, &[&original_completion]);
    runtime.receive_steering(
        turn_id,
        "then use the second correction",
        &[&original_completion],
    );
    runtime.restart_from_checkpoint();
    let follow_up_completion = runtime
        .apply(
            text_completion(&original_completion, "answer before both corrections"),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(assistant_text_committed(
                    turn_id,
                    &original_completion,
                    "answer before both corrections",
                ))
                .observe(turn_steered(turn_id, "use the first correction"))
                .observe(turn_steered(turn_id, "then use the second correction"))
                .observe(notification_delivered(
                    turn_id,
                    observed_notification(&notification),
                )),
        )
        .only_action();
    runtime.assert_last_model_message_update(
        &follow_up_completion,
        "replace",
        &[
            model_system(),
            model_user_text("prepare the first answer"),
            model_assistant_text("answer before both corrections"),
            model_user_text("use the first correction"),
            model_user_text("then use the second correction"),
            model_notifications(&[&notification]),
        ],
    );
    runtime.finish_turn_with_text(
        turn_id,
        &follow_up_completion,
        "answer with both corrections and the evidence",
    );

    // Assert
    assert_ne!(
        action_id(&original_completion),
        action_id(&follow_up_completion)
    );
}

///
/// *Prepare*: A turn has one model completion in flight and one accepted steering message.
/// *Do*: Interrupt the turn before any model call carries the steering, then start the next turn.
/// *Assert*: Core keeps the steering message in model context, so the next turn's completion reads it
/// before the new user message.
///
#[test]
fn interrupting_a_turn_keeps_its_undelivered_steering_in_model_context() {
    // Prepare
    let turn_id = "turn-steer-then-interrupt";
    let reason = "user pressed stop";
    let mut runtime = SynchronousRuntime::new(config());
    let original_completion = runtime.start_turn(turn_id, "first direction");
    runtime.receive_steering(turn_id, "changed direction", &[&original_completion]);

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
                &original_completion,
                "interrupt",
            ))
            .observe(turn_interrupted(turn_id, reason))
            .observe(action_abandoned(turn_id, &original_completion, "interrupt")),
    );
    runtime.restart_from_checkpoint();
    let next_turn_id = "turn-after-interrupt";
    let next_completion = runtime
        .apply(
            user_message(next_turn_id, "act on what i just wrote", "queue"),
            running(next_turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(next_turn_id, "act on what i just wrote")),
        )
        .only_action();

    // Assert
    runtime.assert_last_model_message_update(
        &next_completion,
        "replace",
        &[
            model_system(),
            model_user_text("first direction"),
            model_user_text("changed direction"),
            model_user_text("act on what i just wrote"),
        ],
    );
}

///
/// *Prepare*: A direct filesystem Action from a model tool call is running with two accepted
/// steering messages waiting behind it.
/// *Do*: Interrupt the turn while the tool is still in flight, then start the next turn.
/// *Assert*: Core commits the interrupted tool message, keeps both steering messages after it in
/// arrival order, and the next turn's completion reads them before the new user message.
///
#[test]
fn interrupting_a_running_tool_keeps_its_steering_after_the_interrupted_tool_message() {
    // Prepare
    let turn_id = "turn-steer-tool-then-interrupt";
    let reason = "client interrupt";
    let mut runtime = SynchronousRuntime::new(config());
    let original_completion = runtime.start_turn(turn_id, "read the file");
    let calls = vec![tool_call(
        "steer-interrupt-tool",
        "read_file",
        json!({"path": "a.txt"}),
    )];
    let tools = runtime
        .complete_with_tool_calls(
            turn_id,
            &original_completion,
            &calls,
            [runtime_tool(
                "file_system.read_file",
                json!({"path": "a.txt"}),
            )],
        )
        .actions();
    let [tool]: [Value; 1] = tools.try_into().expect("one tool is running");
    runtime.receive_steering(turn_id, "stop running tests", &[&tool]);
    runtime.receive_steering(turn_id, "just add the logs", &[&tool]);

    // Do
    runtime.apply(
        json!({
            "type": "interrupt",
            "expected_turn_id": turn_id,
            "reason": reason,
        }),
        interrupted(turn_id, reason)
            .observe(turn_interrupted(turn_id, reason))
            .observe(action_abandoned(turn_id, &tool, "interrupt")),
    );
    runtime.restart_from_checkpoint();
    let next_turn_id = "turn-after-tool-interrupt";
    let next_completion = runtime
        .apply(
            user_message(next_turn_id, "act on what i just wrote", "queue"),
            running(next_turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(next_turn_id, "act on what i just wrote")),
        )
        .only_action();

    // Assert
    runtime.assert_last_model_message_update(
        &next_completion,
        "replace",
        &[
            model_system(),
            model_user_text("read the file"),
            model_assistant_tool_calls(&calls),
            json!({"role": "tool", "tool_call_id": "steer-interrupt-tool"}),
            model_user_text("stop running tests"),
            model_user_text("just add the logs"),
            model_user_text("act on what i just wrote"),
        ],
    );
}
