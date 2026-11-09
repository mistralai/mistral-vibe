//! Requests the provider refuses as too long: the estimate correction and the refit.

use super::*;

/// *Prepare*: Automatic compaction is enabled with a 20,000-token threshold.
/// An earlier turn called a tool with the ID `large-read` and got a large
/// result. The new turn calls a tool with the same ID and gets a result.
/// *Do*: Refuse the summary request as too long.
/// *Assert*: The refit removes the earlier exchange with the reused ID and
/// keeps the newest call with its placeholder.
#[test]
fn a_refit_removes_an_earlier_exchange_with_a_reused_tool_call_id() {
    let earlier_call = json!({
        "role": "assistant",
        "content": [{
            "type": "tool_call",
            "id": "large-read",
            "name": "read_file",
            "arguments": {"type": "json", "raw": "{}", "value": {}},
        }],
    });
    let earlier_result = json!({
        "role": "tool",
        "tool_call_id": "large-read",
        "name": "read_file",
        "outcome": "success",
        "content": [{
            "type": "text",
            "text": format!("earlier file contents {}", "0123456789abcdef".repeat(2_500)),
        }],
    });
    let history = [
        Message::user_text("read the earlier file"),
        serde_json::from_value(earlier_call).expect("history message is valid"),
        serde_json::from_value(earlier_result).expect("history message is valid"),
        assistant_text("earlier answer"),
    ]
    .into();
    let config = automatic_compaction_config_with_threshold(20_000);
    let mut runtime = SynchronousRuntime::new_with_history(config.clone(), history);
    let turn_id = "turn-reused-tool-call-id";
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
    let compaction_text = Value::from(action_messages(&compaction).to_vec()).to_string();
    assert!(compaction_text.contains("earlier file contents"));
    let refused_estimate = summary_request_estimate(&runtime.checkpoint_value(), config.clone());
    let window = context_window(&config);
    let correction = grown_correction(1.0, window, refused_estimate);

    let refit = runtime
        .apply(
            completion_failed(
                &compaction,
                "context_overflow",
                "prompt is too long for the model",
            ),
            running(turn_id)
                .dispatch(compaction_call("automatic", 1, 2))
                .observe(request_too_long(
                    turn_id,
                    refused_estimate,
                    correction,
                    window,
                )),
        )
        .only_action();

    let messages = action_messages(&refit);
    let refit_text = Value::from(messages.to_vec()).to_string();
    assert!(!refit_text.contains("earlier file contents"));
    let placeholder = &messages[messages.len() - 2];
    assert_eq!(placeholder["tool_call_id"], "large-read");
    assert_eq!(message_text(placeholder), Some(TOOL_RESULT_PLACEHOLDER));
    assert_eq!(messages[messages.len() - 3]["role"], "assistant");
}

fn context_overflow_error(retryable: bool) -> Value {
    json!({
        "code": "context_overflow",
        "message": "prompt is too long for the model",
        "retryable": retryable,
        "details": null,
    })
}

/// The model's context window in `config`.
fn context_window(config: &HarnessConfig) -> u64 {
    serde_json::to_value(config).expect("config serializes")["settings"]["context"]["model_facts"]
        ["context_window"]
        .as_u64()
        .expect("the model facts have a window")
}

/// The estimate correction after a refusal as too long, as Core grows it:
/// at least the 4/3 floor, and at least window ÷ refused estimate.
fn grown_correction(previous: f64, window: u64, refused_estimate: u64) -> f64 {
    (previous * (4.0 / 3.0)).max(window as f64 / refused_estimate as f64)
}

fn request_too_long(
    turn_id: &str,
    refused_estimate: u64,
    correction: f64,
    context_window: u64,
) -> Value {
    json!({
        "type": "warning",
        "turn_id": turn_id,
        "code": "request_too_long",
        "message": format!(
            "The model refused a request as too long. Requests are now counted as {correction:.2} \
             times their estimated size."
        ),
        "details": {
            "refused_estimate": refused_estimate,
            "estimate_correction": correction,
            "context_window": context_window,
        },
    })
}

/// The turn's failure when Core cannot compact a refused request away.
fn uncompactable_refusal(reason: &str, message: &str) -> Value {
    json!({
        "code": "context_overflow",
        "message": message,
        "retryable": true,
        "details": {
            "reason": reason,
            "provider_message": "prompt is too long for the model",
            "provider_details": null,
        },
    })
}

const REFUSED_AFTER_COMPACTION: &str = "The provider refused the request as too long even after compaction. Check the model's \
     context window.";

fn retried_candidate(turn_id: &str, action: &Value) -> Value {
    json!({
        "type": "agent_completion_candidate_discarded",
        "turn_id": turn_id,
        "action_id": action_id(action),
        "cause": {"type": "retry"},
    })
}

/// A request just under the threshold by estimate, so the provider's refusal
/// is the first sign that it is too long, and one 4/3 growth of the
/// correction puts it over.
fn refusable_request(config: &HarnessConfig, threshold: u64) -> (SynchronousRuntime, Value, u64) {
    let turn_id = "turn-agent-request-refused";
    let request = large_text("a request the provider refuses");
    let mut runtime = SynchronousRuntime::new(config.clone());
    let agent = runtime
        .apply(
            user_message(turn_id, &request, "queue"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(turn_id, &request)),
        )
        .only_action();
    let estimate = agent_request_estimate(&runtime.checkpoint_value(), config.clone());
    assert!(
        estimate < threshold && estimate * 4 / 3 >= threshold,
        "the request estimate {estimate} must be just under the threshold {threshold}"
    );
    (runtime, agent, estimate)
}

#[test]
fn agent_request_refused_as_too_long_corrects_the_estimate_and_compacts() {
    // The provider refuses a request Core estimated under the threshold. The
    // refusal is a measurement: the correction grows by 4/3, the check runs
    // again, and the request is now over, so Core compacts and resends.
    let turn_id = "turn-agent-request-refused";
    let threshold = 60_000;
    let config = automatic_compaction_config_with_threshold(threshold);
    let (mut runtime, agent, estimate) = refusable_request(&config, threshold);
    let window = context_window(&config);
    let correction = 4.0 / 3.0;
    assert_eq!(grown_correction(1.0, window, estimate), correction);

    let compaction = runtime
        .apply(
            completion_failed(
                &agent,
                "context_overflow",
                "prompt is too long for the model",
            ),
            running(turn_id)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(request_too_long(turn_id, estimate, correction, window))
                .observe(retried_candidate(turn_id, &agent)),
        )
        .only_action();
    assert_eq!(
        runtime.checkpoint_value()["estimate_correction"],
        correction
    );

    // The correction survives a restart.
    runtime.restart_from_checkpoint();
    assert_eq!(
        runtime.checkpoint_value()["estimate_correction"],
        correction
    );

    let resumed = runtime
        .apply(
            summary_completion(&compaction, "summary"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(context_compacted(turn_id, &compaction, "summary")),
        )
        .only_action();
    assert_eq!(runtime.checkpoint_value()["rebuilt_since_reply"], true);

    // The next accepted reply clears the rebuilt flag and re-measures the
    // correction: its reported input is under the estimate, so it is back to 1
    // and leaves the checkpoint.
    runtime.finish_turn_with_text(turn_id, &resumed, "done");
    let checkpoint = runtime.checkpoint_value();
    assert!(checkpoint.get("rebuilt_since_reply").is_none());
    assert!(checkpoint.get("estimate_correction").is_none());
}

#[test]
fn agent_request_refused_right_after_compaction_fails_the_turn_retryably() {
    // The first agent request after a compaction is refused too. Compacting
    // again in the same turn would loop, so the turn fails with a retryable
    // error and keeps the compacted history; the correction has grown for
    // the next turn.
    let turn_id = "turn-agent-request-refused";
    let threshold = 60_000;
    let config = automatic_compaction_config_with_threshold(threshold);
    let (mut runtime, agent, estimate) = refusable_request(&config, threshold);
    let window = context_window(&config);
    let first = grown_correction(1.0, window, estimate);
    let compaction = runtime
        .apply(
            completion_failed(
                &agent,
                "context_overflow",
                "prompt is too long for the model",
            ),
            running(turn_id)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(request_too_long(turn_id, estimate, first, window))
                .observe(retried_candidate(turn_id, &agent)),
        )
        .only_action();
    let resumed = runtime
        .apply(
            summary_completion(&compaction, "summary"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(context_compacted(turn_id, &compaction, "summary")),
        )
        .only_action();
    let compacted = runtime.checkpoint_value()["context"].clone();
    let rebuilt_estimate = agent_request_estimate(&runtime.checkpoint_value(), config.clone());

    let second = grown_correction(first, window, rebuilt_estimate);
    let error = uncompactable_refusal("after_compaction", REFUSED_AFTER_COMPACTION);
    runtime.apply(
        completion_failed(
            &resumed,
            "context_overflow",
            "prompt is too long for the model",
        ),
        failed(turn_id, error.clone())
            .observe(request_too_long(turn_id, rebuilt_estimate, second, window))
            .observe(failed_candidate(turn_id, &resumed, error.clone()))
            .observe(turn_failed(turn_id, error)),
    );
    let checkpoint = runtime.checkpoint_value();
    assert_eq!(checkpoint["context"], compacted);
    assert_eq!(checkpoint["estimate_correction"], second);
    assert!(checkpoint.get("rebuilt_since_reply").is_none());
}

/// *Prepare*: A first turn whose request is far under the threshold by
/// estimate.
/// *Do*: The provider refuses it as too long.
/// *Assert*: The refused request was at least the window, so the correction
/// grows to window ÷ estimate and the request counts over the threshold.
/// Nothing in it can be dropped: the user's message is all there is, and the
/// rebuilt context keeps it. The compaction fails before any summary request,
/// and so does the turn. This is the degenerate case of the refusal: a
/// refused request is never under the threshold once corrected.
#[test]
fn agent_request_refused_with_nothing_to_compact_away_fails_the_turn() {
    let turn_id = "turn-agent-request-refused";
    let threshold = 60_000;
    let config = automatic_compaction_config_with_threshold(threshold);
    let compaction_id = compaction_action_id(&config.task_id, 1);
    let window = context_window(&config);
    let mut runtime = SynchronousRuntime::new(config.clone());
    let agent = runtime
        .apply(
            user_message(turn_id, "small request", "queue"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(turn_id, "small request")),
        )
        .only_action();
    let estimate = agent_request_estimate(&runtime.checkpoint_value(), config);
    assert!(estimate * 2 < threshold);
    let correction = grown_correction(1.0, window, estimate);
    assert_eq!(correction, window as f64 / estimate as f64);
    let error = json!({
        "code": "compaction_replacement_too_large",
        "message": "compaction summary and required continuation context cannot fit the configured token threshold",
        "retryable": false,
        "details": null,
    });

    runtime.apply(
        completion_failed(
            &agent,
            "context_overflow",
            "prompt is too long for the model",
        ),
        failed(turn_id, error.clone())
            .observe(request_too_long(turn_id, estimate, correction, window))
            .observe(retried_candidate(turn_id, &agent))
            .observe(json!({
                "type": "context_compaction_failed",
                "turn_id": turn_id,
                "action_id": compaction_id,
                "compaction_id": compaction_id,
                "attempt": 1,
                "trigger": "automatic",
                "error": error.clone(),
            }))
            .observe(turn_failed(turn_id, error)),
    );
    assert_eq!(
        runtime.checkpoint_value()["estimate_correction"],
        correction
    );
}

#[test]
fn context_overflow_without_automatic_compaction_is_an_ordinary_failure() {
    // Without automatic compaction there is nothing to make smaller, so the
    // refusal ends the turn as the provider reported it.
    let turn_id = "turn-provider-overflow";
    let mut runtime = SynchronousRuntime::new(config());
    let agent = runtime
        .apply(
            user_message(turn_id, "small request", "queue"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(turn_id, "small request")),
        )
        .only_action();
    let error = context_overflow_error(false);

    runtime.apply(
        completion_failed(
            &agent,
            "context_overflow",
            "prompt is too long for the model",
        ),
        failed(turn_id, error.clone())
            .observe(failed_candidate(turn_id, &agent, error.clone()))
            .observe(turn_failed(turn_id, error)),
    );
    assert!(
        runtime
            .checkpoint_value()
            .get("estimate_correction")
            .is_none()
    );
}

#[test]
fn a_provider_error_other_than_context_overflow_is_an_ordinary_failure() {
    // Only the adapter's `context_overflow` code is a refusal as too long;
    // Core does not read provider messages.
    let turn_id = "turn-provider-error";
    let mut runtime = SynchronousRuntime::new(automatic_compaction_config());
    let agent = runtime
        .apply(
            user_message(turn_id, "small request", "queue"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(turn_id, "small request")),
        )
        .only_action();
    let error = json!({
        "code": "context_window_exceeded",
        "message": "provider rejected context",
        "retryable": false,
        "details": null,
    });

    runtime.apply(
        completion_failed(
            &agent,
            "context_window_exceeded",
            "provider rejected context",
        ),
        failed(turn_id, error.clone())
            .observe(failed_candidate(turn_id, &agent, error.clone()))
            .observe(turn_failed(turn_id, error)),
    );
}

fn refuse_as_too_long(action: &Value) -> Value {
    completion_failed(
        action,
        "context_overflow",
        "prompt is too long for the model",
    )
}

/// *Prepare*: A turn whose in-context summary request already removed every
/// earlier exchange it could.
/// *Do*: The provider refuses the summary request as too long.
/// *Assert*: Core warns, and since the in-context request cannot shrink by a
/// quarter it sends the standalone request, under the refit limit. Its
/// summary resumes the turn.
#[test]
fn an_in_context_summary_request_refused_at_its_minimal_size_goes_standalone() {
    let turn_id = "turn-in-context-minimal-refused";
    let config = automatic_compaction_config();
    let mut runtime = runtime_needing_compaction("request requiring compaction");
    let compaction = start_continued_turn(&mut runtime, turn_id);
    let refused_estimate = summary_request_estimate(&runtime.checkpoint_value(), config.clone());
    let window = context_window(&config);
    let correction = grown_correction(1.0, window, refused_estimate);

    let standalone = runtime
        .apply(
            refuse_as_too_long(&compaction),
            running(turn_id)
                .dispatch(standalone_compaction_call("automatic", 0, 2))
                .observe(request_too_long(
                    turn_id,
                    refused_estimate,
                    correction,
                    window,
                )),
        )
        .only_action();

    assert_standalone_request(&standalone, "request requiring compaction");
    let refit_estimate = summary_request_estimate(&runtime.checkpoint_value(), config);
    assert!(
        refit_estimate * 4 <= refused_estimate * 3,
        "{refit_estimate}"
    );
    let resumed = runtime
        .apply(
            summary_completion(&standalone, "standalone summary"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(context_compacted(
                    turn_id,
                    &standalone,
                    "standalone summary",
                )),
        )
        .only_action();
    runtime.finish_turn_with_text(turn_id, &resumed, "done");
}

/// *Prepare*: A turn whose compaction went to the standalone request.
/// *Do*: The provider refuses every summary request as too long.
/// *Assert*: Each refusal warns and refits the transcript at least a quarter
/// smaller. When the smallest standalone request is refused, the compaction
/// and the turn fail with a retryable error that names the cause.
#[test]
fn a_summary_request_refused_at_every_size_fails_at_its_smallest() {
    let turn_id = "turn-summary-refused-at-every-size";
    let config = automatic_compaction_config();
    let mut runtime = runtime_needing_compaction("request requiring compaction");
    let mut action = start_continued_turn(&mut runtime, turn_id);
    let window = context_window(&config);
    let mut correction = 1.0_f64;
    let mut refused_estimate =
        summary_request_estimate(&runtime.checkpoint_value(), config.clone());
    // One in-context refusal, then seven standalone refits before the smallest.
    // The count grows with the system prompt, which sets the in-context estimate.
    for attempt in 1..9 {
        correction = grown_correction(correction, window, refused_estimate);
        action = runtime
            .apply(
                refuse_as_too_long(&action),
                running(turn_id)
                    .dispatch(standalone_compaction_call("automatic", 0, attempt + 1))
                    .observe(request_too_long(
                        turn_id,
                        refused_estimate,
                        correction,
                        window,
                    )),
            )
            .only_action();
        let refit_estimate = summary_request_estimate(&runtime.checkpoint_value(), config.clone());
        assert!(
            refit_estimate * 4 <= refused_estimate * 3,
            "{refit_estimate}"
        );
        refused_estimate = refit_estimate;
    }

    correction = grown_correction(correction, window, refused_estimate);
    let error = json!({
        "code": "context_overflow",
        "message": "The provider refused the compaction request as too long even at its smallest \
                    size. Check the model's context window.",
        "retryable": true,
        "details": {
            "reason": "minimal_summary_request",
            "provider_message": "prompt is too long for the model",
            "provider_details": null,
        },
    });
    runtime.apply(
        refuse_as_too_long(&action),
        failed(turn_id, error.clone())
            .observe(request_too_long(
                turn_id,
                refused_estimate,
                correction,
                window,
            ))
            .observe(context_compaction_failed(turn_id, &action, error.clone()))
            .observe(turn_failed(turn_id, error)),
    );
}

/// *Prepare*: A turn whose summary request was cut off with some text, so
/// the cut-off retry is out with that text saved.
/// *Do*: The provider refuses the retry as too long at every size.
/// *Assert*: Each refusal refits the retry smaller. When the smallest is
/// refused, Core tries to keep the saved text with the note, but the refusals
/// have grown the estimate correction until not even the fixed parts fit, so
/// the compaction and the turn fail with the refusal's retryable error.
#[test]
fn a_cut_off_retry_refused_at_its_smallest_fails_when_nothing_fits() {
    let turn_id = "turn-cut-off-retry-refused";
    let mut runtime = runtime_needing_compaction("request requiring compaction");
    let compaction = start_continued_turn(&mut runtime, turn_id);
    let mut action = runtime
        .apply(
            length_completion(&compaction, "<summary>what was written before the cut"),
            running(turn_id).dispatch(standalone_compaction_call("automatic", 0, 2)),
        )
        .only_action();

    let mut refits = 0;
    let observations = loop {
        let refusal = refuse_as_too_long(&action);
        let observations = runtime.preview_observations(refusal.clone());
        let observations = observations
            .as_array()
            .expect("observations are a list")
            .clone();
        let [warning] = observations.as_slice() else {
            break observations;
        };
        refits += 1;
        assert!(refits < 30, "the refits end");
        let attempt = action["attempt"].as_u64().expect("an attempt") + 1;
        action = runtime
            .apply(
                refusal,
                running(turn_id)
                    .dispatch(standalone_compaction_call("automatic", 0, attempt))
                    .observe(warning.clone()),
            )
            .only_action();
        assert_cut_off_retry_request(&action, "request requiring compaction");
    };
    assert!(refits > 0);
    let [warning, ..] = observations.as_slice() else {
        panic!("the refusal warns first");
    };
    assert_eq!(warning["code"], "request_too_long");
    let error = json!({
        "code": "context_overflow",
        "message": "The provider refused the compaction request as too long even at its smallest \
                    size. Check the model's context window.",
        "retryable": true,
        "details": {
            "reason": "minimal_summary_request",
            "provider_message": "prompt is too long for the model",
            "provider_details": null,
        },
    });
    runtime.apply(
        refuse_as_too_long(&action),
        failed(turn_id, error.clone())
            .observe(warning.clone())
            .observe(context_compaction_failed(turn_id, &action, error.clone()))
            .observe(turn_failed(turn_id, error)),
    );
}

/// An earlier assistant tool call with ID `id` and its result, as history.
fn history_tool_exchange(id: &str, result_text: String) -> [Message; 2] {
    let call = json!({
        "role": "assistant",
        "content": [{
            "type": "tool_call",
            "id": id,
            "name": "read_file",
            "arguments": {"type": "json", "raw": "{}", "value": {}},
        }],
    });
    let result = json!({
        "role": "tool",
        "tool_call_id": id,
        "name": "read_file",
        "outcome": "success",
        "content": [{"type": "text", "text": result_text}],
    });
    [
        serde_json::from_value(call).expect("history message is valid"),
        serde_json::from_value(result).expect("history message is valid"),
    ]
}

/// Asserts that each tool call in the request is followed by its result
/// before the next assistant message.
fn assert_every_tool_call_has_a_result(action: &Value) {
    let mut unanswered = Vec::<String>::new();
    for message in action_messages(action) {
        match message["role"].as_str() {
            Some("assistant") => {
                assert!(
                    unanswered.is_empty(),
                    "tool calls without results: {unanswered:?}"
                );
                unanswered = message["content"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter(|part| part["type"] == "tool_call")
                    .map(|part| {
                        part["id"]
                            .as_str()
                            .expect("tool call has an ID")
                            .to_string()
                    })
                    .collect();
            }
            Some("tool") => {
                let id = message["tool_call_id"]
                    .as_str()
                    .expect("tool result has an ID");
                unanswered.retain(|call_id| call_id != id);
            }
            _ => {}
        }
    }
    assert!(
        unanswered.is_empty(),
        "tool calls without results: {unanswered:?}"
    );
}

/// *Prepare*: Automatic compaction is enabled with a 20,000-token threshold.
/// An earlier turn called a tool with the ID `reused-read` twice: the first
/// call got a large result, the second a small one. A new turn calls a tool
/// and gets a result that, with the earlier large result, crosses the
/// threshold, so Core sends a summary request.
/// *Do*: Refuse the summary request as too long.
/// *Assert*: The refit removes the first earlier call and its result only.
/// The second earlier call keeps its result, the newest call keeps its
/// placeholder, and every tool call in the request has a result.
#[test]
fn a_refit_removing_an_older_call_keeps_the_result_of_a_later_call_with_the_same_id() {
    let mut history = vec![Message::user_text("read the earlier files")];
    history.extend(history_tool_exchange(
        "reused-read",
        format!(
            "first earlier contents {}",
            "0123456789abcdef".repeat(2_500)
        ),
    ));
    history.extend(history_tool_exchange(
        "reused-read",
        "second earlier contents".to_string(),
    ));
    history.push(assistant_text("earlier answer"));
    let config = automatic_compaction_config_with_threshold(20_000);
    let mut runtime = SynchronousRuntime::new_with_history(config.clone(), history);
    let turn_id = "turn-older-reused-tool-call-id";
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
    let compaction_text = Value::from(action_messages(&compaction).to_vec()).to_string();
    assert!(compaction_text.contains("first earlier contents"));
    let refused_estimate = summary_request_estimate(&runtime.checkpoint_value(), config.clone());
    let window = context_window(&config);
    let correction = grown_correction(1.0, window, refused_estimate);

    let refit = runtime
        .apply(
            refuse_as_too_long(&compaction),
            running(turn_id)
                .dispatch(compaction_call("automatic", 1, 2))
                .observe(request_too_long(
                    turn_id,
                    refused_estimate,
                    correction,
                    window,
                )),
        )
        .only_action();

    let messages = action_messages(&refit);
    let refit_text = Value::from(messages.to_vec()).to_string();
    assert!(!refit_text.contains("first earlier contents"));
    assert!(refit_text.contains("second earlier contents"));
    assert!(!refit_text.contains("large file contents"));
    let placeholder = &messages[messages.len() - 2];
    assert_eq!(placeholder["tool_call_id"], "large-read");
    assert_eq!(message_text(placeholder), Some(TOOL_RESULT_PLACEHOLDER));
    assert_every_tool_call_has_a_result(&refit);
}

/// *Prepare*: Automatic compaction is enabled with a 20,000-token threshold
/// and one iteration per turn. An earlier turn called a tool with the ID
/// `large-read` twice: the first call got a large result, the second a small
/// one. The last turn called a tool with the same ID and ended at the
/// iteration limit. A manual compaction sends a summary request.
/// *Do*: Refuse the summary request as too long.
/// *Assert*: The refit removes the first earlier call and its result only.
/// The second earlier call keeps its result, the newest call keeps its
/// placeholder, and every tool call in the request has a result.
#[test]
fn a_manual_refit_keeps_the_newest_result_when_older_calls_reused_its_id() {
    let mut history = vec![Message::user_text("read the earlier files")];
    history.extend(history_tool_exchange(
        "large-read",
        format!("earlier file contents {}", "0123456789abcdef".repeat(2_500)),
    ));
    history.extend(history_tool_exchange(
        "large-read",
        "second earlier contents".to_string(),
    ));
    history.push(assistant_text("earlier answer"));
    let mut config = automatic_compaction_config_with_threshold(20_000);
    config.settings.turn.max_iterations = Some(1);
    let mut runtime = SynchronousRuntime::new_with_history(config.clone(), history);
    let turn_id = "turn-manual-after-reused-tool-call-id";
    let tool_action = read_large_file(&mut runtime, turn_id);
    let tool_output = format!("large file contents {}", "0123456789abcdef".repeat(2_500));
    let tool_result = file_read_success(&tool_action, &tool_output);
    runtime.apply(
        tool_result.clone(),
        completed(turn_id, vec![])
            .observe_completed_tool_result(&tool_action, &tool_result)
            .observe(turn_completed_at_iteration_limit(turn_id, vec![])),
    );
    let compaction = runtime
        .apply(
            compact("keep going"),
            compacting("manual").dispatch(compaction_call("manual", 0, 1)),
        )
        .only_action();
    let compaction_text = Value::from(action_messages(&compaction).to_vec()).to_string();
    assert!(compaction_text.contains("earlier file contents"));
    let refused_estimate = summary_request_estimate(&runtime.checkpoint_value(), config.clone());
    let window = context_window(&config);
    let correction = grown_correction(1.0, window, refused_estimate);
    let mut warning = request_too_long("", refused_estimate, correction, window);
    warning["turn_id"] = Value::Null;

    let refit = runtime
        .apply(
            refuse_as_too_long(&compaction),
            compacting("manual")
                .dispatch(compaction_call("manual", 0, 2))
                .observe(warning),
        )
        .only_action();

    let messages = action_messages(&refit);
    let refit_text = Value::from(messages.to_vec()).to_string();
    assert!(!refit_text.contains("earlier file contents"));
    assert!(refit_text.contains("second earlier contents"));
    assert!(!refit_text.contains("large file contents"));
    let placeholder = &messages[messages.len() - 2];
    assert_eq!(placeholder["tool_call_id"], "large-read");
    assert_eq!(message_text(placeholder), Some(TOOL_RESULT_PLACEHOLDER));
    assert_every_tool_call_has_a_result(&refit);
}

/// A Session whose manual compaction left a large summary and no reply since,
/// reopened on a model with a smaller window, and the first request of its
/// next turn, which the estimate puts under the new threshold.
fn request_after_reopening_on_a_smaller_window(
    turn_id: &str,
) -> (SynchronousRuntime, HarnessConfig, Value, u64) {
    let history = vec![
        Message::user_text("an earlier request"),
        assistant_text("an earlier answer"),
    ];
    let mut runtime = SynchronousRuntime::new_with_history(
        automatic_compaction_config_with_threshold(200_000),
        history,
    );
    let compaction = runtime
        .apply(
            compact(""),
            compacting("manual").dispatch(compaction_call("manual", 0, 1)),
        )
        .only_action();
    let summary = large_text("an earlier summary");
    let mut compacted = context_compacted("", &compaction, &summary);
    compacted["turn_id"] = Value::Null;
    compacted["trigger"] = json!("manual");
    runtime.apply(
        summary_completion(&compaction, &summary),
        idle().observe(compacted),
    );
    assert_eq!(runtime.checkpoint_value()["rebuilt_since_reply"], true);

    let threshold = 60_000;
    let config = automatic_compaction_config_with_threshold(threshold);
    runtime.reopen_with_config(config.clone());
    let agent = runtime
        .apply(
            user_message(turn_id, "continue", "queue"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(turn_id, "continue")),
        )
        .only_action();
    let estimate = agent_request_estimate(&runtime.checkpoint_value(), config.clone());
    assert!(
        estimate < threshold,
        "the request estimate {estimate} must be under the threshold {threshold}"
    );
    (runtime, config, agent, estimate)
}

/// *Prepare*: A manual compaction rebuilt the history, and the Session is
/// reopened on a model with a smaller window before any reply.
/// *Do*: The provider refuses the next turn's first request as too long, then
/// answers the request the compaction leaves.
/// *Assert*: The new model's first refusal compacts instead of failing as
/// refused after compaction, and the turn succeeds.
#[test]
fn a_refusal_on_a_reopened_smaller_model_compacts_after_an_earlier_compaction() {
    let turn_id = "turn-after-reopening";
    let (mut runtime, config, agent, estimate) =
        request_after_reopening_on_a_smaller_window(turn_id);
    let window = context_window(&config);
    let correction = grown_correction(1.0, window, estimate);

    let compaction = runtime
        .apply(
            refuse_as_too_long(&agent),
            running(turn_id)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(request_too_long(turn_id, estimate, correction, window))
                .observe(retried_candidate(turn_id, &agent)),
        )
        .only_action();
    let resumed = runtime
        .apply(
            summary_completion(&compaction, "summary"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(context_compacted(turn_id, &compaction, "summary")),
        )
        .only_action();
    runtime.finish_turn_with_text(turn_id, &resumed, "done");
}

/// *Prepare*: As above, the reopened model's first refusal compacted.
/// *Do*: The provider refuses the request the compaction leaves too.
/// *Assert*: Compacting again would loop, so the turn fails as refused after
/// compaction.
#[test]
fn a_second_refusal_on_a_reopened_smaller_model_fails_after_its_compaction() {
    let turn_id = "turn-after-reopening";
    let (mut runtime, config, agent, estimate) =
        request_after_reopening_on_a_smaller_window(turn_id);
    let window = context_window(&config);
    let first = grown_correction(1.0, window, estimate);
    let compaction = runtime
        .apply(
            refuse_as_too_long(&agent),
            running(turn_id)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(request_too_long(turn_id, estimate, first, window))
                .observe(retried_candidate(turn_id, &agent)),
        )
        .only_action();
    let resumed = runtime
        .apply(
            summary_completion(&compaction, "summary"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(context_compacted(turn_id, &compaction, "summary")),
        )
        .only_action();
    let rebuilt_estimate = agent_request_estimate(&runtime.checkpoint_value(), config);

    let second = grown_correction(first, window, rebuilt_estimate);
    let error = uncompactable_refusal("after_compaction", REFUSED_AFTER_COMPACTION);
    runtime.apply(
        refuse_as_too_long(&resumed),
        failed(turn_id, error.clone())
            .observe(request_too_long(turn_id, rebuilt_estimate, second, window))
            .observe(failed_candidate(turn_id, &resumed, error.clone()))
            .observe(turn_failed(turn_id, error)),
    );
}
