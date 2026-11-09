use super::*;

fn city() -> Value {
    json!({
        "name": "City",
        "schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    })
}

fn user_message_with_output_type(
    turn_id: &str,
    text: &str,
    mode: &str,
    output_type: Value,
) -> Value {
    let mut command = user_message(turn_id, text, mode);
    command["output_type"] = output_type;
    command
}

fn typed_llm_call(iteration: u64) -> Value {
    let mut action = llm_call(iteration);
    action["output_type"] = city();
    action
}

/// *Prepare*: Turn 1 starts with output type `City`; its first completion calls a tool.
/// *Do*: Restore from a checkpoint, return the tool result, finish turn 1, then send
/// "Thanks, bye!" without an output type.
/// *Assert*: Both agent completions of turn 1 ask for `City`, across the restore; the
/// completion of turn 2 asks for none.
#[test]
fn an_output_type_applies_to_the_turn_it_starts_only() {
    // Prepare
    let mut runtime = SynchronousRuntime::new(config());
    let first = runtime
        .apply(
            user_message_with_output_type("turn-city", "Which city?", "queue", city()),
            running("turn-city")
                .dispatch(typed_llm_call(0))
                .observe(turn_started("turn-city", "Which city?")),
        )
        .only_action();
    let arguments = json!({"path": "city.txt"});
    let tool = runtime
        .complete_with_tool_calls(
            "turn-city",
            &first,
            &[tool_call("call-city", "read_file", arguments.clone())],
            [runtime_tool("file_system.read_file", arguments)],
        )
        .only_action();

    // Do
    runtime.restart_from_checkpoint();
    let tool_result = file_read_success(&tool, "Paris");
    let second = runtime
        .apply(
            tool_result.clone(),
            running("turn-city")
                .dispatch(typed_llm_call(1))
                .observe_tool_result(&tool, &tool_result)
                .observe(tool_execution_finished(
                    "turn-city",
                    "call-city",
                    tool_result["result"].clone(),
                )),
        )
        .only_action();
    runtime.finish_turn_with_text("turn-city", &second, r#"{"name": "Paris"}"#);
    let goodbye = runtime
        .apply(
            user_message("turn-bye", "Thanks, bye!", "queue"),
            running("turn-bye")
                .dispatch(llm_call(0))
                .observe(turn_started("turn-bye", "Thanks, bye!")),
        )
        .only_action();

    // Assert
    assert_eq!(first["output_type"], city());
    assert_eq!(second["output_type"], city());
    assert!(
        goodbye.get("output_type").is_none(),
        "turn 2 asked for turn 1's output type: {goodbye}"
    );
}

/// *Prepare*: A pre-agent hook runs for a turn started with output type `City`.
/// *Do*: Restore from a checkpoint, then let the hook continue.
/// *Assert*: The turn's first agent completion asks for `City`.
#[test]
fn an_output_type_waits_with_its_turn_for_the_pre_agent_hook() {
    // Prepare
    let mut config = config();
    crate::core::testing::add_always_hook(&mut config, HookPoint::PreAgentTurn);
    let mut runtime = SynchronousRuntime::new(config);
    let hook = runtime
        .apply(
            user_message_with_output_type("turn-hook", "Which city?", "queue", city()),
            running("turn-hook").dispatch(hook_call(
                "pre_agent_turn",
                &["test-hook-0-PreAgentTurn"],
                json!({"user_content": [{"type": "text", "text": "Which city?"}]}),
            )),
        )
        .only_action();

    // Do
    runtime.restart_from_checkpoint();
    let completion = runtime
        .apply(
            hook_completed(
                &hook,
                "pre_agent_turn",
                json!({
                    "type": "continue",
                    "user_content": [{"type": "text", "text": "Which city?"}],
                }),
            ),
            running("turn-hook")
                .dispatch(typed_llm_call(0))
                .observe(turn_started("turn-hook", "Which city?")),
        )
        .only_action();

    // Assert
    assert_eq!(completion["output_type"], city());
}

/// *Prepare*: A turn started with output type `City` waits on its completion.
/// *Do*: Steer it with another output type, with `City`, and with none.
/// *Assert*: Core rejects only the steer with another output type, with code
/// `output_type_mismatch`, and keeps the turn's completion.
#[test]
fn a_steer_cannot_change_the_turns_output_type() {
    // Prepare
    let mut runtime = SynchronousRuntime::new(config());
    let completion = runtime
        .apply(
            user_message_with_output_type("turn-city", "Which city?", "queue", city()),
            running("turn-city")
                .dispatch(typed_llm_call(0))
                .observe(turn_started("turn-city", "Which city?")),
        )
        .only_action();
    let country = json!({"name": "Country", "schema": {"type": "object"}});

    // Do
    runtime.reject(
        user_message_with_output_type("turn-city", "a country instead", "steer", country),
        json!({
            "code": "invalid_command",
            "error": {
                "code": "output_type_mismatch",
                "message": "the active turn has another output_type; send the message without one or wait for the turn to end",
                "retryable": false,
                "details": null,
            },
        }),
    );
    runtime.apply(
        user_message_with_output_type("turn-city", "in France", "steer", city()),
        running("turn-city")
            .keep(&completion)
            .observe(turn_steering_received("turn-city", "in France")),
    );
    runtime.receive_steering("turn-city", "the capital", &[&completion]);

    // Assert
    assert_eq!(completion["output_type"], city());
}

/// *Prepare*: An idle session.
/// *Do*: Queue a message whose output type has an empty name, then one whose schema is
/// not an object.
/// *Assert*: Core rejects both as invalid commands and starts no turn.
#[test]
fn a_malformed_output_type_is_rejected() {
    let mut runtime = SynchronousRuntime::new(config());

    for (output_type, message) in [
        (
            json!({"name": " ", "schema": {"type": "object"}}),
            "output_type name must not be empty",
        ),
        (
            json!({"name": "City", "schema": true}),
            "output_type schema must be a JSON object",
        ),
    ] {
        runtime.reject(
            user_message_with_output_type("turn-bad", "Which city?", "queue", output_type),
            json!({
                "code": "invalid_command",
                "error": {
                    "code": "invalid_command",
                    "message": message,
                    "retryable": false,
                    "details": null,
                },
            }),
        );
    }
}
