//! What the rebuilt history keeps after the summary.

use super::*;

/// *Prepare*: Automatic compaction is enabled with a 20,000-token threshold.
/// An earlier turn left a large request in history, and the current turn
/// calls a tool.
/// *Do*: Complete the tool call with a result that crosses the threshold,
/// then return a summary.
/// *Assert*: Core compacts before the next agent provider call. The summary
/// request ends at the reply that called the tool, with a placeholder for its
/// result. The rebuilt history puts the reply and the real result back after
/// the summary, verbatim.
#[test]
fn a_new_tool_result_stays_out_of_the_summary_request_and_goes_back_after_it() {
    let mut runtime = SynchronousRuntime::new_with_history(
        automatic_compaction_config_with_threshold(20_000),
        vec![
            Message::user_text(format!(
                "earlier request {}",
                "0123456789abcdef".repeat(2_500)
            )),
            assistant_text("earlier answer"),
        ],
    );
    let turn_id = "turn-tool-result-preflight-compaction";
    let tool_action = read_large_file(&mut runtime, turn_id);
    let tool_output = format!("large file contents {}", "0123456789abcdef".repeat(2_500));
    let tool_result = file_read_success(&tool_action, &tool_output);

    let compaction = runtime
        .apply(
            tool_result.clone(),
            running(turn_id)
                .dispatch(compaction_call("automatic", 1, 1))
                .observe_completed_tool_result(&tool_action, &tool_result),
        )
        .only_action();

    assert_eq!(compaction["purpose"], "compaction");
    let messages = action_messages(&compaction);
    let placeholder = &messages[messages.len() - 2];
    assert_eq!(placeholder["role"], "tool");
    assert_eq!(placeholder["tool_call_id"], "large-read");
    assert_eq!(message_text(placeholder), Some(TOOL_RESULT_PLACEHOLDER));
    let compaction_text = Value::from(messages.to_vec()).to_string();
    assert!(compaction_text.contains("earlier request"));
    assert!(!compaction_text.contains("large file contents"));

    let resumed = runtime
        .apply(
            summary_completion(&compaction, "large file summary"),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(context_compacted(
                    turn_id,
                    &compaction,
                    "large file summary",
                )),
        )
        .only_action();
    let resumed_messages = action_messages(&resumed);
    let summary_index = resumed_messages
        .iter()
        .position(|message| {
            message_text(message).is_some_and(|text| text.contains("large file summary"))
        })
        .expect("the rebuilt history has the summary");
    let after_summary = &resumed_messages[summary_index + 1..];
    assert_eq!(after_summary.len(), 2);
    assert_eq!(after_summary[0]["role"], "assistant");
    assert_eq!(after_summary[1]["role"], "tool");
    assert_eq!(after_summary[1]["tool_call_id"], "large-read");
    assert!(after_summary[1].to_string().contains(&tool_output));
    runtime.finish_turn_with_text(turn_id, &resumed, "done");
}

/// *Prepare*: Automatic compaction is enabled with a 20,000-token threshold.
/// The turn's request is long, and the turn calls a tool.
/// *Do*: Complete the tool call with a result that, with the request,
/// crosses the threshold, then return a summary.
/// *Assert*: The summary request carries the whole request. The rebuilt
/// history carries the request truncated to a quarter of the room, with a
/// visible marker, and the new result goes back verbatim after the summary.
/// Nothing after the summary is reduced, so Core does not warn.
#[test]
fn a_long_request_is_carried_at_a_quarter_of_the_room_and_the_new_tool_result_goes_back_verbatim() {
    let turn_id = "turn-large-request-and-tool-result";
    let mut runtime = SynchronousRuntime::new_with_history(
        automatic_compaction_config_with_threshold(20_000),
        vec![Message::user_text("hello"), assistant_text("hi")],
    );
    let request = format!(
        "request start {} request end",
        "0123456789abcdef".repeat(3_500)
    );
    let first_agent = runtime
        .apply(
            user_message(turn_id, &request, "queue"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(turn_id, &request)),
        )
        .only_action();
    let arguments = json!({"path": "large.txt"});
    let calls = vec![tool_call("large-read", "read_file", arguments.clone())];
    let tool_action = runtime
        .complete_with_tool_calls(
            turn_id,
            &first_agent,
            &calls,
            [runtime_tool("file_system.read_file", arguments)],
        )
        .only_action();
    let tool_output = format!("large file contents {}", "0123456789abcdef".repeat(2_500));
    let tool_result = file_read_success(&tool_action, &tool_output);

    let compaction = runtime
        .apply(
            tool_result.clone(),
            running(turn_id)
                .dispatch(compaction_call("automatic", 1, 1))
                .observe_completed_tool_result(&tool_action, &tool_result),
        )
        .only_action();

    let compaction_text = Value::from(action_messages(&compaction).to_vec()).to_string();
    assert!(compaction_text.contains(&request));
    assert!(!compaction_text.contains("large file contents"));

    let resumed = runtime
        .apply(
            summary_completion(&compaction, "summary"),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(context_compacted(turn_id, &compaction, "summary")),
        )
        .only_action();
    let resumed_text = Value::from(action_messages(&resumed).to_vec()).to_string();
    assert!(resumed_text.contains("request start"));
    assert!(resumed_text.contains(TRUNCATION_MARKER_END));
    assert!(!resumed_text.contains(&request));
    assert!(resumed_text.contains(&tool_output));
}

/// *Prepare*: Automatic compaction is enabled with a 20,000-token threshold.
/// The turn's request is far over it, so Core compacts before the first agent
/// call. The agent then reads a file whose result alone is over the threshold.
/// *Do*: Answer both summary requests.
/// *Assert*: Both rebuilds carry the request truncated to a quarter of the
/// room, with a visible marker. The second cannot keep the new exchange whole
/// beside it, so the exchange comes back as a reduced view and Core warns
/// with `compaction_inputs_reduced`. The request does not give way to it.
#[test]
fn a_huge_task_message_and_a_new_tool_result_over_the_threshold_both_give_way() {
    let turn_id = "turn-huge-task-and-tool-result";
    let request = large_text("huge task");
    let mut runtime = SynchronousRuntime::new(automatic_compaction_config_with_threshold(20_000));
    let first = runtime
        .apply(
            user_message(turn_id, &request, "queue"),
            running(turn_id)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started(turn_id, &request)),
        )
        .only_action();
    let agent = runtime
        .apply(
            summary_completion(&first, "first summary"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(context_compacted(turn_id, &first, "first summary")),
        )
        .only_action();
    let carried = |action: &Value| {
        action_messages(action)
            .iter()
            .filter_map(message_text)
            .find(|text| text.starts_with("huge task"))
            .expect("the request is carried")
            .to_string()
    };
    let first_carried = carried(&agent);
    assert!(first_carried.contains(TRUNCATION_MARKER_END));
    assert!(first_carried.len() < request.len());

    let arguments = json!({"path": "large.txt"});
    let calls = vec![tool_call("large-read", "read_file", arguments.clone())];
    let tool_action = runtime
        .complete_with_tool_calls(
            turn_id,
            &agent,
            &calls,
            [runtime_tool("file_system.read_file", arguments)],
        )
        .only_action();
    let tool_output = format!("large file contents {}", "0123456789abcdef".repeat(5_000));
    let tool_result = file_read_success(&tool_action, &tool_output);
    let second = runtime
        .apply(
            tool_result.clone(),
            running(turn_id)
                .dispatch(compaction_call("automatic", 1, 1))
                .observe_completed_tool_result(&tool_action, &tool_result),
        )
        .only_action();

    let resumed = runtime
        .apply(
            summary_completion(&second, "second summary"),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(context_compacted(turn_id, &second, "second summary"))
                .observe(inputs_reduced(turn_id, 2, "clipped")),
        )
        .only_action();
    let second_carried = carried(&resumed);
    assert!(second_carried.contains(TRUNCATION_MARKER_END));
    assert!(second_carried.len() * 10 >= first_carried.len() * 9);
    let view = action_messages(&resumed)
        .iter()
        .filter_map(message_text)
        .find(|text| text.starts_with(REDUCED_VIEW_HEADER))
        .expect("the rebuilt history has the reduced view");
    assert!(view.contains("\nread_file("));
    assert!(view.contains("large file contents"));
}

/// *Prepare*: Automatic compaction is enabled with a 20,000-token threshold,
/// and the turn calls a tool after an earlier exchange.
/// *Do*: Complete the tool call with a result that alone exceeds the
/// threshold, then answer the summary request.
/// *Assert*: The new exchange cannot go back whole after the summary, so the
/// rebuilt history shows the call and its result as a reduced view in its
/// place, and Core warns with `compaction_inputs_reduced`.
#[test]
fn new_items_larger_than_the_threshold_come_back_as_a_reduced_view() {
    let turn_id = "turn-tool-result-too-large";
    let config = automatic_compaction_config_with_threshold(20_000);
    let mut runtime = SynchronousRuntime::new_with_history(
        config,
        vec![Message::user_text("hello"), assistant_text("hi")],
    );
    let tool_action = read_large_file(&mut runtime, turn_id);
    let tool_output = format!("large file contents {}", "0123456789abcdef".repeat(5_000));
    let tool_result = file_read_success(&tool_action, &tool_output);
    let compaction = runtime
        .apply(
            tool_result.clone(),
            running(turn_id)
                .dispatch(compaction_call("automatic", 1, 1))
                .observe_completed_tool_result(&tool_action, &tool_result),
        )
        .only_action();

    let resumed = runtime
        .apply(
            summary_completion(&compaction, "summary"),
            running(turn_id)
                .dispatch(llm_call(1))
                .observe(context_compacted(turn_id, &compaction, "summary"))
                .observe(inputs_reduced(turn_id, 2, "clipped")),
        )
        .only_action();
    let texts = action_messages(&resumed)
        .iter()
        .filter_map(message_text)
        .collect::<Vec<_>>();
    let view = texts
        .iter()
        .find(|text| text.starts_with(REDUCED_VIEW_HEADER))
        .expect("the rebuilt history has the reduced view");
    assert!(view.contains("\nread_file("));
    assert!(view.contains("\n<OK> {\"path\":"));
    assert!(view.contains("large file contents"));
    assert!(view.contains("[CONTENT OMITTED FOR SIZE: "));
    let resumed_text = Value::from(action_messages(&resumed).to_vec()).to_string();
    assert!(!resumed_text.contains(&tool_output));
    assert!(!resumed_text.contains("\"role\":\"tool\""));
}

#[test]
fn compacted_replacement_fits_before_resuming_the_agent() {
    let history = (0..1_000)
        .map(|index| Message::user_text(format!("short note {index}")))
        .collect();
    let mut runtime = SynchronousRuntime::new_with_history(
        automatic_compaction_config_with_threshold(10_000),
        history,
    );
    let turn_id = "turn-fitted-replacement";
    let compaction = runtime
        .apply(
            user_message(turn_id, "continue", "queue"),
            running(turn_id)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started(turn_id, "continue")),
        )
        .only_action();

    let resumed = runtime
        .apply(
            summary_completion(&compaction, "preserve recent notes"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(context_compacted(
                    turn_id,
                    &compaction,
                    "preserve recent notes",
                )),
        )
        .only_action();

    assert_eq!(resumed["purpose"], "agent");
    assert_eq!(resumed["model_input"]["messages"]["type"], "replace");
    let preserved_notes = action_messages(&resumed)
        .iter()
        .filter_map(message_text)
        .filter(|text| text.starts_with("short note "))
        .count();
    assert!(preserved_notes > 0);
    assert!(preserved_notes < 1_000);
    assert!(
        action_messages(&resumed)
            .iter()
            .filter_map(message_text)
            .any(|text| text == "continue")
    );
}

#[test]
fn untagged_plain_text_is_accepted_as_the_compaction_summary() {
    let turn_id = "turn-plain-summary";
    let request = large_text("request requiring compaction");
    let mut runtime = SynchronousRuntime::new(automatic_compaction_config());
    let compaction = runtime
        .apply(
            user_message(turn_id, &request, "queue"),
            running(turn_id)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started(turn_id, &request)),
        )
        .only_action();

    let resumed = runtime
        .apply(
            text_completion(&compaction, "plain handoff summary"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(context_compacted(
                    turn_id,
                    &compaction,
                    "plain handoff summary",
                )),
        )
        .only_action();
    runtime.finish_turn_with_text(turn_id, &resumed, "done");
}

/// A valid summary too long for the room left after the fixed parts is cut at
/// a line end, within 95% of that room, with the note after it. No items
/// follow the summary, so no omitted line is added. Core warns with
/// `compaction_summary_cut_off` instead of failing the turn.
#[test]
fn a_summary_too_long_for_the_room_is_cut_at_a_line_end_with_the_note() {
    let turn_id = "turn-oversized-valid-summary";
    let request = large_text("request requiring compaction");
    let mut runtime = SynchronousRuntime::new(automatic_compaction_config());
    let compaction = runtime
        .apply(
            user_message(turn_id, &request, "queue"),
            running(turn_id)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started(turn_id, &request)),
        )
        .only_action();
    let summary = (0..40_000)
        .map(|line| format!("summary line {line}"))
        .collect::<Vec<_>>()
        .join("\n");
    let completion = summary_completion(&compaction, &summary);
    let kept_bytes = previewed_kept_bytes(&runtime, &completion);
    assert!(kept_bytes < summary.len());

    let resumed = runtime
        .apply(
            completion,
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(context_compacted(turn_id, &compaction, &summary))
                .observe(summary_cut_off(
                    turn_id,
                    "too_long",
                    summary.len(),
                    kept_bytes,
                )),
        )
        .only_action();
    let texts = action_messages(&resumed)
        .iter()
        .filter_map(message_text)
        .collect::<Vec<_>>();
    let kept = &summary[..kept_bytes];
    assert!(summary[kept_bytes..].starts_with('\n'));
    assert!(
        texts
            .iter()
            .any(|text| text.contains(&format!("{kept}\n\n{SUMMARY_CUT_NOTE}\n")))
    );
    assert!(!texts.contains(&REDUCED_VIEW_OMITTED));
}

#[test]
fn fitted_compaction_projection_survives_checkpoint_restore() {
    let turn_id = "turn-restored-fitted-projection";
    let request = large_text("request before restore");
    let mut runtime = SynchronousRuntime::new(automatic_compaction_config());
    let compaction = runtime
        .apply(
            user_message(turn_id, &request, "queue"),
            running(turn_id)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started(turn_id, &request)),
        )
        .only_action();
    let projected_messages = action_messages(&compaction).to_vec();
    let checkpoint = runtime.checkpoint_value();

    runtime.restart_from_checkpoint();

    assert_eq!(runtime.checkpoint_value(), checkpoint);
    let resumed = runtime
        .apply(
            summary_completion(&compaction, "restored summary"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(context_compacted(turn_id, &compaction, "restored summary")),
        )
        .only_action();
    assert_eq!(action_messages(&compaction), projected_messages);
    runtime.finish_turn_with_text(turn_id, &resumed, "done");
}

#[test]
fn automatic_compaction_failure_preserves_canonical_history() {
    let turn_id = "turn-compaction-failure";
    let request = "request retained after failure";
    let mut runtime = runtime_needing_compaction(request);
    let compaction = start_continued_turn(&mut runtime, turn_id);
    let error = json!({
        "code": "provider_error",
        "message": "summary provider failed",
        "retryable": false,
        "details": null,
    });

    let fallback = runtime
        .apply(
            completion_failed(&compaction, "provider_error", "summary provider failed"),
            running(turn_id).dispatch(standalone_compaction_call("automatic", 0, 2)),
        )
        .only_action();
    runtime.apply(
        completion_failed(&fallback, "provider_error", "summary provider failed"),
        failed(turn_id, error.clone())
            .observe(context_compaction_failed(turn_id, &fallback, error.clone()))
            .observe(turn_failed(turn_id, error)),
    );

    let next_turn = runtime
        .apply(
            user_message("turn-after-failure", "continue", "queue"),
            running("turn-after-failure")
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started("turn-after-failure", "continue")),
        )
        .only_action();
    let resumed = runtime
        .apply(
            summary_completion(&next_turn, "recover preserved request"),
            running("turn-after-failure")
                .dispatch(llm_call(0))
                .observe(context_compacted(
                    "turn-after-failure",
                    &next_turn,
                    "recover preserved request",
                )),
        )
        .only_action();
    assert!(action_messages(&resumed).iter().any(|message| {
        message_text(message).is_some_and(|text| text.contains("request retained after failure"))
    }));
}
