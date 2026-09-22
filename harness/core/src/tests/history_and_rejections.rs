use super::*;

fn imported_history(value: Value) -> Vec<crate::core::Message> {
    serde_json::from_value(value).expect("initial history is valid")
}

fn input_with_protocol_version(
    protocol_version: u8,
    input_id: u64,
    command: Value,
) -> HarnessInput {
    serde_json::from_value(json!({
        "protocol_version": protocol_version,
        "input_id": input_id,
        "determinism": {
            "time_unix_ms": 1_700_000_000_000_u64 + input_id,
            "random_seed": input_id,
        },
        "command": command,
    }))
    .expect("public-interface test input is valid")
}

fn apply_value(session: &mut HarnessSession, input: HarnessInput) -> Value {
    serde_json::to_value(session.apply(input)).expect("apply result serializes")
}

fn model_messages(result: &Value) -> &[Value] {
    result["transition"]["next"]["directives"][0]["action"]["model_input"]["messages"]["messages"]
        .as_array()
        .expect("completion action contains model messages")
}

fn tool_call_completion(action_id: &str) -> Value {
    json!({
        "type": "completion_succeeded",
        "action_id": action_id,
        "result": {
            "parts": [{
                "type": "tool_call",
                "id": "call-read",
                "name": "read_file",
                "arguments_json": "{\"path\":\"notes.txt\"}",
            }],
            "finish_reason": "tool_call",
            "usage": null,
        },
    })
}

fn tool_succeeded(action_id: &str, call_id: &str) -> Value {
    json!({
        "type": "tool_succeeded",
        "action_id": action_id,
        "call_id": call_id,
        "result": {
            "type": "success",
            "content": [{"type": "text", "text": "file contents"}],
        },
    })
}

///
/// *Prepare*: Serialized imported history contains user, assistant, and system messages, with system messages in non-leading positions.
/// *Do*: Create a session with that history, start a turn, restore its checkpoint, and request the pending completion's full model input.
/// *Assert*: Both full model inputs preserve imported order and contain exactly one generated system prompt.
///
#[test]
fn create_with_history_is_reflected_in_first_model_input() {
    // Prepare
    let generated_prompt_marker = "generated-prompt-marker-history-contract";
    let mut session_config = config();
    session_config.system_instructions = generated_prompt_marker.to_string();
    let history = imported_history(json!([
        {
            "role": "user",
            "content": [{"type": "text", "text": "historical user before system"}],
        },
        {
            "role": "system",
            "content": [{"type": "text", "text": "historical system in the middle"}],
        },
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "historical assistant after system"}],
        },
        {
            "role": "system",
            "content": [{"type": "text", "text": "historical system at the end"}],
        },
    ]));

    // Do
    let mut session = HarnessSession::create_with_history(session_config.clone(), history)
        .expect("session creates with history");
    let started = accepted_value(session.apply(input(
        1,
        user_message("turn-history", "current user message", "queue"),
    )));
    let action_id = dispatched_action_id(&started).to_string();
    let checkpoint = session.checkpoint().expect("checkpoint captures");
    let encoded = serde_json::to_string(&checkpoint).expect("checkpoint serializes");
    let decoded = decode_checkpoint(&encoded).expect("checkpoint decodes");
    let mut restored =
        HarnessSession::restore(session_config, decoded, 0).expect("checkpoint restores");
    let refreshed = accepted_value(restored.apply(input(
        1,
        json!({
            "type": "completion_model_input_resync_requested",
            "action_id": action_id,
        }),
    )));

    // Assert
    assert_eq!(
        started["transition"]["next"]["directives"][0]["action"]["model_input"]["messages"]["type"],
        "replace"
    );
    let first_messages = model_messages(&started);
    assert_eq!(
        first_messages
            .iter()
            .map(|message| message["role"].as_str().expect("message has a role"))
            .collect::<Vec<_>>(),
        vec!["system", "user", "system", "assistant", "system", "user",]
    );
    assert_eq!(
        first_messages[1],
        json!({
            "role": "user",
            "content": [{"type": "text", "text": "historical user before system"}],
        })
    );
    assert_eq!(
        first_messages[2],
        json!({
            "role": "system",
            "content": [{"type": "text", "text": "historical system in the middle"}],
        })
    );
    assert_eq!(
        first_messages[3],
        json!({
            "role": "assistant",
            "content": [{"type": "text", "text": "historical assistant after system"}],
        })
    );
    assert_eq!(
        first_messages[4],
        json!({
            "role": "system",
            "content": [{"type": "text", "text": "historical system at the end"}],
        })
    );
    assert_eq!(
        first_messages[5],
        json!({
            "role": "user",
            "content": [{"type": "text", "text": "current user message"}],
        })
    );
    let first_messages_json =
        serde_json::to_string(first_messages).expect("model messages serialize");
    assert_eq!(
        first_messages_json.matches(generated_prompt_marker).count(),
        1
    );

    assert_eq!(
        refreshed["transition"]["next"]["directives"][0]["type"],
        "refresh"
    );
    assert_eq!(model_messages(&refreshed), first_messages);
    let restored_messages_json =
        serde_json::to_string(model_messages(&refreshed)).expect("model messages serialize");
    assert_eq!(
        restored_messages_json
            .matches(generated_prompt_marker)
            .count(),
        1
    );
}

///
/// *Prepare*: A session has accepted inputs while one completion or tool action remains pending.
/// *Do*: Send unsupported-version, conflicting, stale, skipped, wrong-action, and wrong-tool-call inputs, replaying the latest accepted input after each rejection.
/// *Assert*: Every rejection has its exact serialized value, consumes no new input ID, and leaves the latest receipt and model-input delivery mode unchanged.
///
#[test]
fn rejections_are_exact_non_consuming_and_leave_delivery_state_unchanged() {
    // Prepare
    let mut session = HarnessSession::create(config()).expect("session creates");
    let start_input = input(1, user_message("turn-rejections", "read a file", "queue"));
    let started = accepted_value(session.apply(start_input.clone()));
    let completion_action_id = dispatched_action_id(&started).to_string();

    // Do
    let unsupported = apply_value(
        &mut session,
        input_with_protocol_version(
            2,
            2,
            notification("notification-unsupported", "unsupported"),
        ),
    );
    let replay_after_unsupported = apply_value(&mut session, start_input);

    let notification_two_input = input(
        2,
        notification("notification-two", "accepted after unsupported version"),
    );
    let notification_two = accepted_value(session.apply(notification_two_input.clone()));
    let conflicting = apply_value(
        &mut session,
        input(
            2,
            notification("notification-conflict", "conflicting latest input"),
        ),
    );
    let replay_after_conflict = apply_value(&mut session, notification_two_input.clone());

    let stale = apply_value(
        &mut session,
        input(1, notification("notification-stale", "stale input")),
    );
    let replay_after_stale = apply_value(&mut session, notification_two_input.clone());

    let skipped_input = input(
        4,
        notification("notification-four", "accepted after filling the gap"),
    );
    let skipped = apply_value(&mut session, skipped_input.clone());
    let replay_after_skipped = apply_value(&mut session, notification_two_input);
    let notification_three = accepted_value(session.apply(input(
        3,
        notification("notification-three", "fills the gap"),
    )));
    let notification_four = accepted_value(session.apply(skipped_input));

    let wrong_action = apply_value(
        &mut session,
        input(5, tool_call_completion("wrong-completion-action")),
    );
    let replay_after_wrong_action = apply_value(
        &mut session,
        input(
            4,
            notification("notification-four", "accepted after filling the gap"),
        ),
    );

    let completion_input = input(5, tool_call_completion(&completion_action_id));
    let tool_pending = accepted_value(session.apply(completion_input.clone()));
    let tool_action = &tool_pending["transition"]["next"]["directives"][0]["action"];
    let tool_action_id = tool_action["action_id"]
        .as_str()
        .expect("tool action has an ID")
        .to_string();
    let tool_call_id = tool_action["call_id"]
        .as_str()
        .expect("tool action has a call ID")
        .to_string();
    let wrong_tool_call = apply_value(
        &mut session,
        input(6, tool_succeeded(&tool_action_id, "wrong-tool-call")),
    );
    let replay_after_wrong_tool_call = apply_value(&mut session, completion_input);
    let tool_completed =
        accepted_value(session.apply(input(6, tool_succeeded(&tool_action_id, &tool_call_id))));

    // Assert
    assert_eq!(
        unsupported,
        json!({
            "type": "rejected",
            "rejection": {
                "code": "invalid_command",
                "error": {
                    "code": "invalid_command",
                    "message": "unsupported harness input version 2",
                    "retryable": false,
                    "details": null,
                },
            },
        })
    );
    assert_eq!(replay_after_unsupported, started);
    assert_eq!(notification_two["transition"]["input_id"], 2);

    assert_eq!(
        conflicting,
        json!({
            "type": "rejected",
            "rejection": {
                "code": "input_conflict",
                "input_id": 2,
            },
        })
    );
    assert_eq!(replay_after_conflict, notification_two);

    assert_eq!(
        stale,
        json!({
            "type": "rejected",
            "rejection": {
                "code": "stale_input",
                "received_input_id": 1,
                "last_accepted_input_id": 2,
            },
        })
    );
    assert_eq!(replay_after_stale, notification_two);

    assert_eq!(
        skipped,
        json!({
            "type": "rejected",
            "rejection": {
                "code": "out_of_order_input",
                "received_input_id": 4,
                "expected_input_id": 3,
            },
        })
    );
    assert_eq!(replay_after_skipped, notification_two);
    assert_eq!(notification_three["transition"]["input_id"], 3);
    assert_eq!(notification_four["transition"]["input_id"], 4);

    assert_eq!(
        wrong_action,
        json!({
            "type": "rejected",
            "rejection": {
                "code": "invalid_correlation",
                "received_action_id": "wrong-completion-action",
                "pending_action_ids": [completion_action_id],
            },
        })
    );
    assert_eq!(replay_after_wrong_action, notification_four);
    assert_eq!(tool_pending["transition"]["input_id"], 5);

    assert_eq!(
        wrong_tool_call,
        json!({
            "type": "rejected",
            "rejection": {
                "code": "invalid_state",
                "command_type": "tool_succeeded",
                "state": "running",
            },
        })
    );
    assert_eq!(replay_after_wrong_tool_call, tool_pending);
    assert_eq!(tool_completed["transition"]["input_id"], 6);
    let next_model_input =
        &tool_completed["transition"]["next"]["directives"][0]["action"]["model_input"];
    assert_eq!(next_model_input["messages"]["type"], "append");
    assert_eq!(next_model_input["tool_catalog"]["type"], "keep");
}

///
/// *Prepare*: A session has one active turn and a pending completion action.
/// *Do*: Steer and interrupt using a different turn ID, reusing the same input ID after each rejection.
/// *Assert*: Both commands return their exact mismatch errors and leave checkpoint and delivery state unchanged.
///
#[test]
fn stale_turn_controls_are_exact_and_non_consuming() {
    // Prepare
    let mut session = HarnessSession::create(config()).expect("session creates");
    accepted_value(session.apply(input(1, user_message("turn-current", "work", "queue"))));
    let before_checkpoint = checkpoint_value(&session);
    let before_inspection = inspection_value(&session);

    // Do
    let stale_steer = apply_value(
        &mut session,
        input(2, user_message("turn-stale", "change direction", "steer")),
    );
    let stale_interrupt = apply_value(
        &mut session,
        input(
            2,
            json!({
                "type": "interrupt",
                "expected_turn_id": "turn-stale",
                "reason": "stop",
            }),
        ),
    );

    // Assert
    assert_eq!(
        stale_steer["rejection"]["error"]["message"],
        "steer turn mismatch: expected \"turn-current\", got \"turn-stale\""
    );
    assert_eq!(
        stale_interrupt["rejection"]["error"]["message"],
        "interrupt turn mismatch: expected \"turn-current\", got \"turn-stale\""
    );
    assert_eq!(checkpoint_value(&session), before_checkpoint);
    assert_eq!(inspection_value(&session), before_inspection);
}

///
/// *Prepare*: A session has completed a turn and later receives an idle context message.
/// *Do*: Interrupt the finished turn before and after the context message, then interrupt an unknown turn.
/// *Assert*: Finished-turn interrupts are accepted no-ops, while the unknown turn is rejected without consuming input or changing the checkpoint.
///
#[test]
fn late_interrupts_are_idempotent_only_for_the_last_finished_turn() {
    // Prepare
    let mut session = HarnessSession::create(config()).expect("session creates");
    let started =
        accepted_value(session.apply(input(1, user_message("turn-finished", "work", "queue"))));
    let completion_action_id = dispatched_action_id(&started).to_string();
    accepted_value(session.apply(input(
        2,
        completion(
            &completion_action_id,
            json!([{"type": "text", "text": "done"}]),
        ),
    )));
    let terminal_checkpoint = checkpoint_value(&session);

    // Do
    let late_terminal = accepted_value(session.apply(input(
        3,
        json!({
            "type": "interrupt",
            "expected_turn_id": "turn-finished",
            "reason": "late stop",
        }),
    )));
    accepted_value(session.apply(input(
        4,
        json!({
            "type": "context_message",
            "content": [{"type": "text", "text": "runtime context"}],
        }),
    )));
    let idle_checkpoint = checkpoint_value(&session);
    let late_idle = accepted_value(session.apply(input(
        5,
        json!({
            "type": "interrupt",
            "expected_turn_id": "turn-finished",
            "reason": null,
        }),
    )));
    let unknown = apply_value(
        &mut session,
        input(
            6,
            json!({
                "type": "interrupt",
                "expected_turn_id": "turn-unknown",
                "reason": null,
            }),
        ),
    );

    // Assert
    assert_eq!(late_terminal["transition"]["next"]["type"], "none");
    assert_eq!(late_terminal["transition"]["observations"], json!([]));
    assert_eq!(late_terminal["transition"]["turn"]["status"], "completed");
    assert_eq!(checkpoint_value(&session), idle_checkpoint);
    assert_ne!(idle_checkpoint, terminal_checkpoint);
    assert_eq!(late_idle["transition"]["next"]["type"], "none");
    assert_eq!(late_idle["transition"]["observations"], json!([]));
    assert_eq!(late_idle["transition"]["turn"]["status"], "idle");
    assert_eq!(
        unknown,
        json!({
            "type": "rejected",
            "rejection": {
                "code": "invalid_state",
                "command_type": "interrupt",
                "state": "idle",
            },
        })
    );
    assert_eq!(inspection_value(&session)["last_input_id"], 5);
}
