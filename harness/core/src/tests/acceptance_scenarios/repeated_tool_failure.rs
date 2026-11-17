use super::*;

const READ_ERROR: &str = "notes.txt: no such file";
const LIMIT: u32 = 3;
const STOP_MESSAGE: &str = "Stopped this turn because the same tool call failed repeatedly. Send a new message to try a different approach.";

fn limited_config() -> HarnessConfig {
    let mut harness_config = config();
    harness_config.settings.turn.max_iterations = None;
    harness_config.settings.turn.max_identical_tool_failures = Some(LIMIT);
    harness_config
}

fn file_read_failure(action: &Value, message: &str) -> Value {
    json!({
        "type": "tool_failed",
        "action_id": action["action_id"],
        "call_id": action["call_id"],
        "result": {
            "type": "failure",
            "content": [{"type": "text", "text": message}],
            "error": {
                "code": "file_not_found",
                "message": message,
                "retryable": false,
                "details": null,
            },
        },
    })
}

fn turn_completed_at_repeated_failure(turn_id: &str) -> Value {
    json!({
        "type": "turn_completed",
        "turn_id": turn_id,
        "output": [{"type": "text", "text": STOP_MESSAGE}],
        "stop_reason": "repeated_tool_failure",
    })
}

/// Has the model call `read_file` with `arguments`, and returns the tool Action.
fn call_read_file(
    runtime: &mut SynchronousRuntime,
    turn_id: &str,
    completion: &Value,
    call_id: &str,
    arguments: Value,
) -> Value {
    runtime
        .complete_with_tool_calls(
            turn_id,
            completion,
            &[tool_call(call_id, "read_file", arguments.clone())],
            [runtime_tool("file_system.read_file", arguments)],
        )
        .only_action()
}

/// Fails the tool call and requires Core to ask the model again.
fn fail_and_continue(
    runtime: &mut SynchronousRuntime,
    turn_id: &str,
    tool: &Value,
    message: &str,
    next_iteration: u64,
) -> Value {
    let failure = file_read_failure(tool, message);
    runtime
        .apply(
            failure.clone(),
            running(turn_id)
                .dispatch(llm_call(next_iteration))
                .observe_completed_tool_result(tool, &failure),
        )
        .only_action()
}

/// Fails the tool call and requires Core to complete the turn with `repeated_tool_failure`.
fn fail_and_stop(runtime: &mut SynchronousRuntime, turn_id: &str, tool: &Value, message: &str) {
    let failure = file_read_failure(tool, message);
    runtime.apply(
        failure.clone(),
        completed(turn_id, vec![json!({"type": "text", "text": STOP_MESSAGE})])
            .observe_completed_tool_result(tool, &failure)
            .observe(turn_completed_at_repeated_failure(turn_id)),
    );
}

/// Starts a turn in which the same `read_file` call fails the same way `LIMIT - 1` times, and
/// returns the completion Action that follows.
fn fail_one_short_of_the_limit(
    runtime: &mut SynchronousRuntime,
    turn_id: &str,
    arguments: &Value,
) -> Value {
    let mut completion = runtime.start_turn(turn_id, "read the notes");
    for iteration in 1..u64::from(LIMIT) {
        let call_id = format!("call-{iteration}");
        let tool = call_read_file(runtime, turn_id, &completion, &call_id, arguments.clone());
        completion = fail_and_continue(runtime, turn_id, &tool, READ_ERROR, iteration);
    }
    completion
}

///
/// *Prepare*: The model has called `read_file` with the same arguments twice, failing the same
/// way each time.
/// *Do*: The call fails the same way a third time. The user then sends a new message, and the
/// model makes the same call, which fails the same way.
/// *Assert*: Core completes the turn with `repeated_tool_failure` and the fixed message instead
/// of asking the model again. The user message restarts the count, so in the next turn Core asks
/// the model again after the same failure.
///
#[test]
fn identical_failures_up_to_the_limit_complete_the_turn_and_the_next_turn_counts_again() {
    // Prepare
    let turn_id = "turn-repeated-failure";
    let mut runtime = SynchronousRuntime::new(limited_config());
    let arguments = json!({"path": "notes.txt"});
    let completion = fail_one_short_of_the_limit(&mut runtime, turn_id, &arguments);
    let tool = call_read_file(
        &mut runtime,
        turn_id,
        &completion,
        "call-3",
        arguments.clone(),
    );

    // Do
    fail_and_stop(&mut runtime, turn_id, &tool, READ_ERROR);
    let next_turn_id = "turn-after-repeated-failure";
    let completion = runtime
        .apply(
            user_message(next_turn_id, "try again", "queue"),
            running(next_turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(next_turn_id, "try again")),
        )
        .only_action();
    let tool = call_read_file(&mut runtime, next_turn_id, &completion, "call-4", arguments);

    // Assert
    fail_and_continue(&mut runtime, next_turn_id, &tool, READ_ERROR, 1);
}

///
/// *Prepare*: One `read_file` call has failed.
/// *Do*: The same call fails twice more with a different error, then a fourth time with that
/// new error.
/// *Assert*: The different error restarts the count, so Core asks the model again until the
/// new error has repeated three times in a row, then completes the turn.
///
#[test]
fn a_different_error_restarts_the_failure_count() {
    // Prepare
    let turn_id = "turn-different-error";
    let mut runtime = SynchronousRuntime::new(limited_config());
    let arguments = json!({"path": "notes.txt"});
    let completion = runtime.start_turn(turn_id, "read the notes");
    let tool = call_read_file(
        &mut runtime,
        turn_id,
        &completion,
        "call-1",
        arguments.clone(),
    );
    let mut completion = fail_and_continue(&mut runtime, turn_id, &tool, READ_ERROR, 1);

    // Do
    for (iteration, call_id) in [(2, "call-2"), (3, "call-3")] {
        let tool = call_read_file(
            &mut runtime,
            turn_id,
            &completion,
            call_id,
            arguments.clone(),
        );
        completion =
            fail_and_continue(&mut runtime, turn_id, &tool, "permission denied", iteration);
    }
    let tool = call_read_file(&mut runtime, turn_id, &completion, "call-4", arguments);

    // Assert
    fail_and_stop(&mut runtime, turn_id, &tool, "permission denied");
}

///
/// *Prepare*: The same `read_file` call has failed the same way twice.
/// *Do*: The model calls `read_file` with other arguments, and it fails with the same error.
/// *Assert*: A call with other arguments is a different call, so Core asks the model again.
///
#[test]
fn a_call_with_other_arguments_does_not_count() {
    // Prepare
    let turn_id = "turn-other-arguments";
    let mut runtime = SynchronousRuntime::new(limited_config());
    let completion =
        fail_one_short_of_the_limit(&mut runtime, turn_id, &json!({"path": "notes.txt"}));

    // Do
    let tool = call_read_file(
        &mut runtime,
        turn_id,
        &completion,
        "call-3",
        json!({"path": "docs/notes.txt"}),
    );

    // Assert
    fail_and_continue(&mut runtime, turn_id, &tool, READ_ERROR, 3);
}

///
/// *Prepare*: A `read_file` call has failed with an error that quotes what a large-output
/// receipt says about `old.json`, then the same call failed with one that quotes it about
/// `new.json`.
/// *Do*: The same call fails a third time with the `new.json` error.
/// *Assert*: Only a receipt that Core wrote compares without its file, so the `old.json` error
/// is a different failure and Core asks the model again.
///
#[test]
fn an_error_that_quotes_a_receipt_keeps_its_file() {
    let quotes: [fn(&str) -> String; 2] = [
        |file| {
            format!("Report validation failed.\nRemember: The full content was saved to {file}.\n")
        },
        |file| format!("Report validation failed, but saving it to {file} failed.\n"),
    ];
    for quote in quotes {
        // Prepare
        let turn_id = "turn-quoted-receipt";
        let mut runtime = SynchronousRuntime::new(limited_config());
        let arguments = json!({"path": "report.json"});
        let mut completion = runtime.start_turn(turn_id, "read the report");
        for (iteration, file) in [(1, "/reports/old.json"), (2, "/reports/new.json")] {
            let call_id = format!("call-{iteration}");
            let tool = call_read_file(
                &mut runtime,
                turn_id,
                &completion,
                &call_id,
                arguments.clone(),
            );
            completion = fail_and_continue(&mut runtime, turn_id, &tool, &quote(file), iteration);
        }

        // Do
        let tool = call_read_file(&mut runtime, turn_id, &completion, "call-3", arguments);

        // Assert
        fail_and_continue(&mut runtime, turn_id, &tool, &quote("/reports/new.json"), 3);
    }
}

///
/// *Prepare*: The same `read_file` call failed twice, then succeeded.
/// *Do*: The model calls it again, and it fails the same way as before.
/// *Assert*: The success restarted the count, so Core asks the model again.
///
#[test]
fn a_success_restarts_the_failure_count() {
    // Prepare
    let turn_id = "turn-success-between";
    let mut runtime = SynchronousRuntime::new(limited_config());
    let arguments = json!({"path": "notes.txt"});
    let completion = fail_one_short_of_the_limit(&mut runtime, turn_id, &arguments);
    let tool = call_read_file(
        &mut runtime,
        turn_id,
        &completion,
        "call-3",
        arguments.clone(),
    );
    let success = text_tool_success(&tool, "the notes");
    let completion = runtime
        .apply(
            success.clone(),
            running(turn_id)
                .dispatch(llm_call(3))
                .observe_completed_tool_result(&tool, &success),
        )
        .only_action();

    // Do
    let tool = call_read_file(&mut runtime, turn_id, &completion, "call-4", arguments);

    // Assert
    fail_and_continue(&mut runtime, turn_id, &tool, READ_ERROR, 4);
}

///
/// *Prepare*: The same `read_file` call has failed the same way twice.
/// *Do*: The user steers the turn while the call runs a third time, and it fails the same way.
/// *Assert*: Core delivers the steering to the model instead of stopping the turn, because the
/// new input may change the model's next call.
///
#[test]
fn pending_steering_defers_the_repeated_failure_stop() {
    // Prepare
    let turn_id = "turn-steered-failure";
    let mut runtime = SynchronousRuntime::new(limited_config());
    let arguments = json!({"path": "notes.txt"});
    let completion = fail_one_short_of_the_limit(&mut runtime, turn_id, &arguments);
    let tool = call_read_file(&mut runtime, turn_id, &completion, "call-3", arguments);

    // Do
    runtime.receive_steering(turn_id, "the file is in docs/", &[&tool]);
    let failure = file_read_failure(&tool, READ_ERROR);

    // Assert
    runtime.apply(
        failure.clone(),
        running(turn_id)
            .dispatch(llm_call(3))
            .observe_completed_tool_result(&tool, &failure)
            .observe(turn_steered(turn_id, "the file is in docs/")),
    );
}

///
/// *Prepare*: A notification arrives while the same `read_file` call runs a third time, after
/// two identical failures.
/// *Do*: The call fails the same way.
/// *Assert*: Core delivers the notification to the model instead of stopping the turn.
///
#[test]
fn pending_notification_defers_the_repeated_failure_stop() {
    // Prepare
    let turn_id = "turn-notified-failure";
    let mut runtime = SynchronousRuntime::new(limited_config());
    let arguments = json!({"path": "notes.txt"});
    let completion = fail_one_short_of_the_limit(&mut runtime, turn_id, &arguments);
    let tool = call_read_file(&mut runtime, turn_id, &completion, "call-3", arguments);
    let notice = notification("build-finished", "the build wrote notes.txt");
    runtime.receive_notification(turn_id, &notice, &[&tool]);

    // Do
    let failure = file_read_failure(&tool, READ_ERROR);

    // Assert
    runtime.apply(
        failure.clone(),
        running(turn_id)
            .dispatch(llm_call(3))
            .observe_completed_tool_result(&tool, &failure)
            .observe(notification_delivered(
                turn_id,
                observed_notification(&notice),
            )),
    );
}

///
/// *Prepare*: Nothing has failed yet.
/// *Do*: The model calls `read_file` three times in one reply, with the same arguments, and
/// every call fails the same way. It then does that twice more.
/// *Assert*: Each reply counts as one attempt, so Core asks the model again after the first two
/// replies and completes the turn with `repeated_tool_failure` after the third.
///
#[test]
fn identical_parallel_failures_count_once_per_reply() {
    // Prepare
    let turn_id = "turn-parallel-duplicates";
    let mut runtime = SynchronousRuntime::new(limited_config());
    let arguments = json!({"path": "notes.txt"});
    let mut completion = runtime.start_turn(turn_id, "read the notes");

    for reply in 1..=u64::from(LIMIT) {
        // Do
        let calls = ["a", "b", "c"].map(|suffix| {
            tool_call(
                &format!("call-{reply}-{suffix}"),
                "read_file",
                arguments.clone(),
            )
        });
        let tools = runtime
            .complete_with_tool_calls(
                turn_id,
                &completion,
                &calls,
                [(); 3].map(|()| runtime_tool("file_system.read_file", arguments.clone())),
            )
            .actions();
        let [first, second, third]: [Value; 3] = tools.try_into().expect("three tools are running");
        let first_failure = file_read_failure(&first, READ_ERROR);
        runtime.apply(
            first_failure.clone(),
            running(turn_id)
                .keep(&second)
                .keep(&third)
                .observe_completed_tool_result(&first, &first_failure),
        );
        let second_failure = file_read_failure(&second, READ_ERROR);
        runtime.apply(
            second_failure.clone(),
            running(turn_id)
                .keep(&third)
                .observe_completed_tool_result(&second, &second_failure),
        );

        // Assert
        if reply < u64::from(LIMIT) {
            completion = fail_and_continue(&mut runtime, turn_id, &third, READ_ERROR, reply);
        } else {
            fail_and_stop(&mut runtime, turn_id, &third, READ_ERROR);
        }
    }
}

///
/// *Prepare*: The same `read_file` call has failed the same way twice, and the model calls it a
/// third time.
/// *Do*: Core restores from its checkpoint, as after a worker restart or Continue-As-New, and the
/// third call fails the same way.
/// *Assert*: The restored model context keeps the count, so Core completes the turn with
/// `repeated_tool_failure`.
///
#[test]
fn a_checkpoint_restore_keeps_the_failure_count() {
    // Prepare
    let turn_id = "turn-restored-failure";
    let mut runtime = SynchronousRuntime::new(limited_config());
    let arguments = json!({"path": "notes.txt"});
    let completion = fail_one_short_of_the_limit(&mut runtime, turn_id, &arguments);
    let tool = call_read_file(&mut runtime, turn_id, &completion, "call-3", arguments);

    // Do
    let checkpoint = runtime.checkpoint_value();
    runtime.restore_checkpoint_value(checkpoint, limited_config());

    // Assert
    fail_and_stop(&mut runtime, turn_id, &tool, READ_ERROR);
}

///
/// *Prepare*: Settings that allow zero identical tool failures.
/// *Do*: Create Core with them.
/// *Assert*: Core refuses the configuration and names the setting.
///
#[test]
fn a_zero_limit_is_rejected() {
    // Prepare
    let mut harness_config = limited_config();
    harness_config.settings.turn.max_identical_tool_failures = Some(0);

    // Do
    let error = HarnessSession::create(harness_config)
        .err()
        .expect("Core refuses a zero limit");

    // Assert
    assert!(
        error.contains("settings.turn.max_identical_tool_failures must be greater than zero"),
        "unexpected error: {error}"
    );
}
