//! Requests the provider rejects as invalid: which part of the rejected turn leaves the
//! model context after a compaction.

use super::*;

/// *Prepare*: A manual compaction rebuilt the history, and the next turn's first request is
/// in flight.
/// *Do*: The provider rejects that request, then the user sends another message.
/// *Assert*: The rejected turn leaves the model context, and the next request replaces the
/// model input with the compacted history and the new message only.
#[test]
fn a_rejected_request_after_a_manual_compaction_leaves_the_model_context() {
    // Prepare
    let rejected_turn_id = "turn-rejected-after-compaction";
    let next_turn_id = "turn-after-compaction-rejection";
    let history = vec![
        Message::user_text("an earlier request"),
        assistant_text("an earlier answer"),
    ];
    let mut runtime = SynchronousRuntime::new_with_history(automatic_compaction_config(), history);
    let compaction = runtime
        .apply(
            compact(""),
            compacting("manual").dispatch(compaction_call("manual", 0, 1)),
        )
        .only_action();
    let mut compacted = context_compacted("", &compaction, "the earlier work");
    compacted["turn_id"] = Value::Null;
    compacted["trigger"] = json!("manual");
    runtime.apply(
        summary_completion(&compaction, "the earlier work"),
        idle().observe(compacted),
    );
    let rejected = runtime
        .apply(
            user_message(rejected_turn_id, "describe this image", "queue"),
            running(rejected_turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(rejected_turn_id, "describe this image")),
        )
        .only_action();
    let rejection = completion_failed(&rejected, "completion_rejected", "unreadable image");
    let error = rejection["error"].clone();

    // Do
    runtime.apply(
        rejection,
        failed(rejected_turn_id, error.clone())
            .observe(failed_candidate(rejected_turn_id, &rejected, error.clone()))
            .observe(turn_failed(rejected_turn_id, error)),
    );
    let next = runtime
        .apply(
            user_message(next_turn_id, "say hello", "queue"),
            running(next_turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(next_turn_id, "say hello")),
        )
        .only_action();

    // Assert
    let update = &next["model_input"]["messages"];
    assert_eq!(update["type"], "replace");
    let texts: Vec<&str> = action_messages(&next)
        .iter()
        .filter_map(message_text)
        .collect();
    assert!(
        texts.iter().any(|text| text.contains("the earlier work")),
        "the compacted history stays: {texts:?}"
    );
    assert!(
        !texts
            .iter()
            .any(|text| text.contains("describe this image")),
        "the rejected turn left the model context: {texts:?}"
    );
    assert_eq!(texts.last(), Some(&"say hello"));
}

/// *Prepare*: A turn's first request needed compaction, and the request after the compaction
/// is in flight.
/// *Do*: The provider rejects that request, then the user sends another message.
/// *Assert*: Only the turn's own message leaves the model context: the next request keeps the
/// summary and the earlier carried request, and ends with the new message.
#[test]
fn a_rejected_request_after_a_compaction_in_its_turn_removes_only_its_input() {
    // Prepare
    let rejected_turn_id = "turn-rejected-after-own-compaction";
    let next_turn_id = "turn-after-own-compaction-rejection";
    let mut runtime = runtime_needing_compaction("new request");
    let compaction = start_continued_turn(&mut runtime, rejected_turn_id);
    let rejected = runtime
        .apply(
            summary_completion(&compaction, "the earlier work"),
            running(rejected_turn_id)
                .dispatch(llm_call(0))
                .observe(context_compacted(
                    rejected_turn_id,
                    &compaction,
                    "the earlier work",
                )),
        )
        .only_action();
    let rejection = completion_failed(&rejected, "completion_rejected", "unreadable image");
    let error = rejection["error"].clone();

    // Do
    runtime.apply(
        rejection,
        failed(rejected_turn_id, error.clone())
            .observe(failed_candidate(rejected_turn_id, &rejected, error.clone()))
            .observe(turn_failed(rejected_turn_id, error)),
    );
    let next = runtime
        .apply(
            user_message(next_turn_id, "say hello", "queue"),
            running(next_turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(next_turn_id, "say hello")),
        )
        .only_action();

    // Assert
    assert_eq!(next["model_input"]["messages"]["type"], "replace");
    let texts: Vec<&str> = action_messages(&next)
        .iter()
        .filter_map(message_text)
        .collect();
    assert!(
        texts.iter().any(|text| text.contains("the earlier work")),
        "the summary stays: {texts:?}"
    );
    assert!(
        texts.contains(&"new request"),
        "the earlier carried request stays: {texts:?}"
    );
    assert!(
        !texts.contains(&"continue"),
        "the rejected turn's message left the model context: {texts:?}"
    );
    assert_eq!(texts.last(), Some(&"say hello"));
}

/// *Prepare*: A turn's first request needed compaction, steering arrived during the
/// compaction, and the request after it, which carries the steering, is in flight.
/// *Do*: The provider rejects that request, then the user sends another message.
/// *Assert*: The turn's own request leaves the model context and the steering stays, so the
/// next request holds the steering and the new message but not the rejected request.
#[test]
fn a_rejected_request_keeps_steering_delivered_before_it() {
    // Prepare
    let rejected_turn_id = "turn-rejected-with-early-steering";
    let next_turn_id = "turn-after-early-steering";
    let request = large_text("original direction");
    let mut runtime = SynchronousRuntime::new(automatic_compaction_config());
    let compaction = runtime
        .apply(
            user_message(rejected_turn_id, &request, "queue"),
            running(rejected_turn_id)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started(rejected_turn_id, &request)),
        )
        .only_action();
    runtime.receive_steering(rejected_turn_id, "new direction", &[&compaction]);
    let rejected = runtime
        .apply(
            summary_completion(&compaction, "steered summary"),
            running(rejected_turn_id)
                .dispatch(llm_call(0))
                .observe(context_compacted(
                    rejected_turn_id,
                    &compaction,
                    "steered summary",
                ))
                .observe(turn_steered(rejected_turn_id, "new direction")),
        )
        .only_action();
    let rejection = completion_failed(&rejected, "completion_rejected", "unreadable image");
    let error = rejection["error"].clone();

    // Do
    runtime.apply(
        rejection,
        failed(rejected_turn_id, error.clone())
            .observe(failed_candidate(rejected_turn_id, &rejected, error.clone()))
            .observe(turn_failed(rejected_turn_id, error)),
    );
    runtime.restart_from_checkpoint();
    let next = runtime
        .apply(
            user_message(next_turn_id, "say hello", "queue"),
            running(next_turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(next_turn_id, "say hello")),
        )
        .only_action();

    // Assert
    let texts: Vec<&str> = action_messages(&next)
        .iter()
        .filter_map(message_text)
        .collect();
    assert!(
        texts.contains(&"new direction"),
        "the steering stays: {texts:?}"
    );
    assert!(
        !texts
            .iter()
            .any(|text| text.starts_with("original direction")),
        "the rejected request left the model context: {texts:?}"
    );
    assert_eq!(texts.last(), Some(&"say hello"));
}

/// *Prepare*: An earlier exchange, then a turn whose own message is larger than the threshold,
/// so its preflight compaction shows that message only in the reduced view.
/// *Do*: The provider rejects the request after the compaction, then the user sends another
/// message.
/// *Assert*: No single message is the turn's input, so Core removes nothing: the earlier
/// carried request stays in the next request.
#[test]
fn a_rejected_request_whose_input_was_reduced_keeps_the_model_context() {
    // Prepare
    let rejected_turn_id = "turn-rejected-reduced-input";
    let next_turn_id = "turn-after-reduced-input";
    let mut runtime = SynchronousRuntime::new_with_history(
        automatic_compaction_config_with_threshold(20_000),
        vec![
            Message::user_text("an earlier request"),
            assistant_text("an earlier answer"),
        ],
    );
    let request = format!("unreadable input {}", "0123456789abcdef".repeat(6_000));
    let compaction = runtime
        .apply(
            user_message(rejected_turn_id, &request, "queue"),
            running(rejected_turn_id)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started(rejected_turn_id, &request)),
        )
        .only_action();
    let rejected = runtime
        .apply(
            summary_completion(&compaction, "the earlier work"),
            running(rejected_turn_id)
                .dispatch(llm_call(0))
                .observe(context_compacted(
                    rejected_turn_id,
                    &compaction,
                    "the earlier work",
                ))
                .observe(inputs_reduced(rejected_turn_id, 2, "clipped")),
        )
        .only_action();
    let rejection = completion_failed(&rejected, "completion_rejected", "unreadable image");
    let error = rejection["error"].clone();

    // Do
    runtime.apply(
        rejection,
        failed(rejected_turn_id, error.clone())
            .observe(failed_candidate(rejected_turn_id, &rejected, error.clone()))
            .observe(turn_failed(rejected_turn_id, error)),
    );
    runtime.restart_from_checkpoint();
    let next = runtime
        .apply(
            user_message(next_turn_id, "say hello", "queue"),
            running(next_turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(next_turn_id, "say hello")),
        )
        .only_action();

    // Assert
    let before: Vec<&str> = action_messages(&rejected)
        .iter()
        .filter_map(message_text)
        .collect();
    let after: Vec<&str> = action_messages(&next)
        .iter()
        .filter_map(message_text)
        .collect();
    assert!(
        before
            .iter()
            .any(|text| text.starts_with(REDUCED_VIEW_HEADER)),
        "the rejected request carries the reduced view: {before:?}"
    );
    assert_eq!(
        &after[..after.len() - 1],
        &before[..],
        "nothing was removed"
    );
    assert_eq!(after.last(), Some(&"say hello"));
}

/// *Prepare*: A turn's first request needed compaction, a context message arrived during the
/// compaction, and the request after it, which carries the context, is in flight.
/// *Do*: The provider rejects that request, then the user sends another message.
/// *Assert*: The context message does not count as steering: the turn's own request leaves
/// the model context, and the context message stays.
#[test]
fn a_rejected_request_after_a_context_message_still_removes_its_input() {
    // Prepare
    let rejected_turn_id = "turn-rejected-with-context";
    let next_turn_id = "turn-after-context-rejection";
    let request = large_text("original direction");
    let mut runtime = SynchronousRuntime::new(automatic_compaction_config());
    let compaction = runtime
        .apply(
            user_message(rejected_turn_id, &request, "queue"),
            running(rejected_turn_id)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started(rejected_turn_id, &request)),
        )
        .only_action();
    runtime.apply(
        json!({
            "type": "context_message",
            "content": [{"type": "text", "text": "database for this task: LOCAL-42"}],
        }),
        running(rejected_turn_id).keep(&compaction),
    );
    let rejected = runtime
        .apply(
            summary_completion(&compaction, "the earlier work"),
            running(rejected_turn_id)
                .dispatch(llm_call(0))
                .observe(context_compacted(
                    rejected_turn_id,
                    &compaction,
                    "the earlier work",
                )),
        )
        .only_action();
    let rejection = completion_failed(&rejected, "completion_rejected", "unreadable image");
    let error = rejection["error"].clone();

    // Do
    runtime.apply(
        rejection,
        failed(rejected_turn_id, error.clone())
            .observe(failed_candidate(rejected_turn_id, &rejected, error.clone()))
            .observe(turn_failed(rejected_turn_id, error)),
    );
    let next = runtime
        .apply(
            user_message(next_turn_id, "say hello", "queue"),
            running(next_turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(next_turn_id, "say hello")),
        )
        .only_action();

    // Assert
    assert_eq!(next["model_input"]["messages"]["type"], "replace");
    let texts: Vec<&str> = action_messages(&next)
        .iter()
        .filter_map(message_text)
        .collect();
    assert!(
        texts.contains(&"database for this task: LOCAL-42"),
        "the context message stays: {texts:?}"
    );
    assert!(
        !texts
            .iter()
            .any(|text| text.starts_with("original direction")),
        "the rejected request left the model context: {texts:?}"
    );
    assert_eq!(texts.last(), Some(&"say hello"));
}

/// *Prepare*: An earlier exchange whose reply is larger than the threshold, then a turn with
/// a short message, so its preflight compaction reduces only that earlier reply.
/// *Do*: The provider rejects the request after the compaction, then the user sends another
/// message.
/// *Assert*: The turn's message survived the compaction whole, so it leaves the model context
/// and the next request ends with the new message.
#[test]
fn a_rejected_request_after_a_compaction_that_reduced_only_the_earlier_reply_removes_its_input() {
    // Prepare
    let rejected_turn_id = "turn-rejected-after-reduced-reply";
    let next_turn_id = "turn-after-reduced-reply";
    let mut runtime = SynchronousRuntime::new_with_history(
        automatic_compaction_config_with_threshold(20_000),
        vec![
            Message::user_text("an earlier request"),
            assistant_text(format!(
                "an earlier answer {}",
                "0123456789abcdef".repeat(6_000)
            )),
        ],
    );
    let compaction = runtime
        .apply(
            user_message(rejected_turn_id, "bad input", "queue"),
            running(rejected_turn_id)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started(rejected_turn_id, "bad input")),
        )
        .only_action();
    let rejected = runtime
        .apply(
            summary_completion(&compaction, "the earlier work"),
            running(rejected_turn_id)
                .dispatch(llm_call(0))
                .observe(context_compacted(
                    rejected_turn_id,
                    &compaction,
                    "the earlier work",
                ))
                .observe(inputs_reduced(rejected_turn_id, 1, "clipped")),
        )
        .only_action();
    let rejection = completion_failed(&rejected, "completion_rejected", "unreadable image");
    let error = rejection["error"].clone();

    // Do
    runtime.apply(
        rejection,
        failed(rejected_turn_id, error.clone())
            .observe(failed_candidate(rejected_turn_id, &rejected, error.clone()))
            .observe(turn_failed(rejected_turn_id, error)),
    );
    let next = runtime
        .apply(
            user_message(next_turn_id, "say hello", "queue"),
            running(next_turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(next_turn_id, "say hello")),
        )
        .only_action();

    // Assert
    assert_eq!(next["model_input"]["messages"]["type"], "replace");
    let texts: Vec<&str> = action_messages(&next)
        .iter()
        .filter_map(message_text)
        .collect();
    assert!(
        !texts.contains(&"bad input"),
        "the rejected request left the model context: {texts:?}"
    );
    assert!(texts.contains(&"an earlier request"), "{texts:?}");
    assert_eq!(texts.last(), Some(&"say hello"));
}

/// *Prepare*: A turn's model called a tool whose result alone exceeds the threshold, so the
/// compaction after it shows the call and its result only in the reduced view, and the
/// request after the compaction is in flight.
/// *Do*: The provider rejects that request, then the user sends another message.
/// *Assert*: The model already answered in the turn, even though its reply now sits in the
/// reduced view, so Core removes nothing: the turn's request stays in the next request.
#[test]
fn a_rejected_request_after_a_compaction_reduced_the_turns_reply_keeps_the_model_context() {
    // Prepare
    let rejected_turn_id = "turn-rejected-after-reduced-tool-result";
    let next_turn_id = "turn-after-reduced-tool-result";
    let mut runtime = SynchronousRuntime::new_with_history(
        automatic_compaction_config_with_threshold(20_000),
        vec![Message::user_text("hello"), assistant_text("hi")],
    );
    let tool_action = read_large_file(&mut runtime, rejected_turn_id);
    let tool_output = format!("large file contents {}", "0123456789abcdef".repeat(5_000));
    let tool_result = file_read_success(&tool_action, &tool_output);
    let compaction = runtime
        .apply(
            tool_result.clone(),
            running(rejected_turn_id)
                .dispatch(compaction_call("automatic", 1, 1))
                .observe_completed_tool_result(&tool_action, &tool_result),
        )
        .only_action();
    let rejected = runtime
        .apply(
            summary_completion(&compaction, "summary"),
            running(rejected_turn_id)
                .dispatch(llm_call(1))
                .observe(context_compacted(rejected_turn_id, &compaction, "summary"))
                .observe(inputs_reduced(rejected_turn_id, 2, "clipped")),
        )
        .only_action();
    let rejection = completion_failed(&rejected, "completion_rejected", "unreadable image");
    let error = rejection["error"].clone();

    // Do
    runtime.apply(
        rejection,
        failed(rejected_turn_id, error.clone())
            .observe(failed_candidate(rejected_turn_id, &rejected, error.clone()))
            .observe(turn_failed(rejected_turn_id, error)),
    );
    let next = runtime
        .apply(
            user_message(next_turn_id, "say hello", "queue"),
            running(next_turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(next_turn_id, "say hello")),
        )
        .only_action();

    // Assert
    runtime.assert_last_model_message_update(&next, "append", &[model_user_text("say hello")]);
}

/// *Prepare*: A turn's model called a tool, steering arrived while it ran, and the tool's
/// result alone exceeds the threshold, so the compaction after it shows the turn's reply
/// only in the reduced view while the steering stays whole.
/// *Do*: The provider rejects the request after the compaction, then the user sends another
/// message.
/// *Assert*: The model already answered in the turn, so Core removes nothing even though the
/// steering is the newest user message: the next request appends to the model input.
#[test]
fn a_rejected_request_after_steering_and_a_reduced_turn_reply_keeps_the_model_context() {
    // Prepare
    let rejected_turn_id = "turn-rejected-after-steering-and-reduced-reply";
    let next_turn_id = "turn-after-steering-and-reduced-reply";
    let mut runtime = SynchronousRuntime::new_with_history(
        automatic_compaction_config_with_threshold(20_000),
        vec![Message::user_text("hello"), assistant_text("hi")],
    );
    let tool_action = read_large_file(&mut runtime, rejected_turn_id);
    runtime.receive_steering(rejected_turn_id, "also check the logs", &[&tool_action]);
    let tool_output = format!("large file contents {}", "0123456789abcdef".repeat(5_000));
    let tool_result = file_read_success(&tool_action, &tool_output);
    let compaction = runtime
        .apply(
            tool_result.clone(),
            running(rejected_turn_id)
                .dispatch(compaction_call("automatic", 1, 1))
                .observe_completed_tool_result(&tool_action, &tool_result)
                .observe(turn_steered(rejected_turn_id, "also check the logs")),
        )
        .only_action();
    let rejected = runtime
        .apply(
            summary_completion(&compaction, "summary"),
            running(rejected_turn_id)
                .dispatch(llm_call(1))
                .observe(context_compacted(rejected_turn_id, &compaction, "summary"))
                .observe(inputs_reduced(rejected_turn_id, 2, "clipped")),
        )
        .only_action();
    let rejection = completion_failed(&rejected, "completion_rejected", "unreadable image");
    let error = rejection["error"].clone();

    // Do
    runtime.apply(
        rejection,
        failed(rejected_turn_id, error.clone())
            .observe(failed_candidate(rejected_turn_id, &rejected, error.clone()))
            .observe(turn_failed(rejected_turn_id, error)),
    );
    let next = runtime
        .apply(
            user_message(next_turn_id, "say hello", "queue"),
            running(next_turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(next_turn_id, "say hello")),
        )
        .only_action();

    // Assert
    runtime.assert_last_model_message_update(&next, "append", &[model_user_text("say hello")]);
}
