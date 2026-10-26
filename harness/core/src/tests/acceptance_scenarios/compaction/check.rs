//! When the check starts a compaction: before the agent call, after a reported size, around steering.

use super::*;

#[test]
fn oversized_agent_input_compacts_before_any_agent_provider_call() {
    let turn_id = "turn-preflight-compaction";
    let request = large_text("current request");
    let mut runtime = SynchronousRuntime::new(automatic_compaction_config());

    let compaction = runtime
        .apply(
            user_message(turn_id, &request, "queue"),
            running(turn_id)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started(turn_id, &request)),
        )
        .only_action();

    assert_eq!(compaction["purpose"], "compaction");
    assert_eq!(compaction["model_input"]["messages"]["type"], "replace");
    let projected_request = action_messages(&compaction)
        .iter()
        .filter_map(message_text)
        .find(|text| text.contains("current request"))
        .expect("compaction must preserve a fitted copy of the latest user request");
    assert!(projected_request.len() < request.len());
    assert!(projected_request.contains("tokens truncated"));

    let resumed = runtime
        .apply(
            summary_completion(&compaction, "preserve the current request"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(context_compacted(
                    turn_id,
                    &compaction,
                    "preserve the current request",
                )),
        )
        .only_action();
    assert_eq!(resumed["purpose"], "agent");
    assert_eq!(resumed["model_input"]["messages"]["type"], "replace");
    let resumed_request = action_messages(&resumed)
        .iter()
        .filter_map(message_text)
        .find(|text| text.contains("current request"))
        .expect("replacement context must preserve the latest user request");
    assert!(resumed_request.len() <= 20_000 * 4);
}

#[test]
fn oversized_agent_input_compacts_before_a_pre_llm_hook() {
    let turn_id = "turn-preflight-before-hook";
    let request = large_text("current request");
    let mut config = automatic_compaction_config();
    add_always_hook(&mut config, HookPoint::PreLlmCall);
    let mut runtime = SynchronousRuntime::new(config);

    let compaction = runtime
        .apply(
            user_message(turn_id, &request, "queue"),
            running(turn_id)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started(turn_id, &request)),
        )
        .only_action();

    assert_eq!(compaction["purpose"], "compaction");
}

#[test]
fn provider_reported_usage_over_threshold_triggers_compaction() {
    // Regression (VIBE-4298): the automatic-compaction trigger must react to the
    // provider-reported context size, not only the crude serialized-bytes
    // estimate (`bytes / APPROX_BYTES_PER_TOKEN`). For models whose tokenizer
    // counts far more tokens than that estimate (e.g. GLM-5.2), a tiny
    // conversation can still blow past the real context budget while the
    // estimate stays comfortably under it -- so compaction never fires even
    // though the status bar (fed by the provider usage) shows the overflow.
    let turn_id = "turn-provider-usage-over-threshold";
    let threshold = 20_000;
    let mut runtime =
        SynchronousRuntime::new(automatic_compaction_config_with_threshold(threshold));

    // A minimal turn whose serialized messages are nowhere near the threshold.
    let agent = runtime.start_turn(turn_id, "hi");

    // The provider reports a context well above the threshold. The reply's
    // own output carries it, so the reported input stays near the estimate and
    // the estimate correction stays at 1: only the reported size can trigger.
    let usage = json!({
        "input_tokens": 1,
        "output_tokens": threshold + 5_000,
        "total_tokens": threshold + 5_001,
        "cached_input_tokens": 0,
        "cache_write_tokens": 0,
        "reasoning_tokens": 0,
    });
    let completion = json!({
        "type": "completion_succeeded",
        "action_id": action_id(&agent),
        "result": {
            "parts": [{"type": "text", "text": "ok"}],
            "finish_reason": "stop",
            "usage": usage,
        },
    });
    let committed_candidate = json!({
        "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
        "finish_reason": "stop",
        "usage": usage,
    });
    runtime.apply(
        completion,
        completed(turn_id, vec![json!({"type": "text", "text": "ok"})])
            .observe(json!({
                "type": "assistant_message_committed",
                "turn_id": turn_id,
                "action_id": action_id(&agent),
                "candidate": committed_candidate,
            }))
            .observe(turn_completed(turn_id, "ok")),
    );

    // Next turn: the messages are still tiny, but the provider already reported
    // that the live context exceeds the budget. The harness must compact before
    // the first agent provider call instead of dispatching an ordinary llm_call.
    let next_turn = "turn-provider-usage-over-threshold-2";
    let content = json!([{"type": "text", "text": "again"}]);
    let compaction = runtime
        .apply(
            user_message_with_content(next_turn, content.clone()),
            running(next_turn)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started_with_content(next_turn, content)),
        )
        .only_action();
    assert_eq!(compaction["purpose"], "compaction");

    // After compaction the reported size is cleared, so the resumed agent call
    // is dispatched normally instead of compacting again in a loop.
    let resumed = runtime
        .apply(
            summary_completion(&compaction, "summary"),
            running(next_turn)
                .dispatch(llm_call(0))
                .observe(context_compacted(next_turn, &compaction, "summary")),
        )
        .only_action();
    assert_eq!(resumed["purpose"], "agent");
}

#[test]
fn failed_automatic_compaction_keeps_provider_reported_over_budget_signal() {
    // The serialized-bytes estimate stays under the threshold, so the only
    // over-budget signal is the last provider-reported size. Compaction must
    // keep that signal until it actually replaces the live context; otherwise a
    // failed attempt leaves the next turn with no reason to compact again. An
    // older exchange of about 10,000 tokens by estimate makes the reported
    // 25,000 a correction of about 2.5, so the in-context request fits once
    // that exchange is removed.
    let turn_id = "turn-keep-reported-after-failed-compaction";
    let threshold = 20_000;
    let mut runtime = SynchronousRuntime::new_with_history(
        automatic_compaction_config_with_threshold(threshold),
        vec![
            Message::user_text(format!("old request {}", "0123456789abcdef".repeat(2_500))),
            assistant_text("old answer"),
        ],
    );

    let agent = runtime
        .apply(
            user_message(turn_id, "hi", "queue"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(turn_started(turn_id, "hi")),
        )
        .only_action();
    let usage = json!({
        "input_tokens": threshold + 5_000,
        "output_tokens": 10,
        "total_tokens": threshold + 5_010,
        "cached_input_tokens": 0,
        "cache_write_tokens": 0,
        "reasoning_tokens": 0,
    });
    let completion = json!({
        "type": "completion_succeeded",
        "action_id": action_id(&agent),
        "result": {
            "parts": [{"type": "text", "text": "ok"}],
            "finish_reason": "stop",
            "usage": usage,
        },
    });
    let committed_candidate = json!({
        "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
        "finish_reason": "stop",
        "usage": usage,
    });
    runtime.apply(
        completion,
        completed(turn_id, vec![json!({"type": "text", "text": "ok"})])
            .observe(json!({
                "type": "assistant_message_committed",
                "turn_id": turn_id,
                "action_id": action_id(&agent),
                "candidate": committed_candidate,
            }))
            .observe(turn_completed(turn_id, "ok")),
    );

    // First over-budget turn compacts, then the summary provider fails.
    let failing_turn = "turn-keep-reported-after-failed-compaction-2";
    let failing_content = json!([{"type": "text", "text": "again"}]);
    let compaction = runtime
        .apply(
            user_message_with_content(failing_turn, failing_content.clone()),
            running(failing_turn)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started_with_content(failing_turn, failing_content)),
        )
        .only_action();
    assert_eq!(compaction["purpose"], "compaction");

    let error = json!({
        "code": "provider_error",
        "message": "summary provider failed",
        "retryable": false,
        "details": null,
    });
    let fallback = runtime
        .apply(
            completion_failed(&compaction, "provider_error", "summary provider failed"),
            running(failing_turn).dispatch(standalone_compaction_call("automatic", 0, 2)),
        )
        .only_action();
    runtime.apply(
        completion_failed(&fallback, "provider_error", "summary provider failed"),
        failed(failing_turn, error.clone())
            .observe(context_compaction_failed(
                failing_turn,
                &fallback,
                error.clone(),
            ))
            .observe(turn_failed(failing_turn, error)),
    );

    // The failed compaction never replaced the context, so the reported size is
    // still the only over-budget signal. The next turn must compact again.
    let retry_turn = "turn-keep-reported-after-failed-compaction-3";
    let retry_content = json!([{"type": "text", "text": "retry"}]);
    let retry_compaction = runtime
        .apply(
            user_message_with_content(retry_turn, retry_content.clone()),
            running(retry_turn)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started_with_content(retry_turn, retry_content)),
        )
        .only_action();
    assert_eq!(retry_compaction["purpose"], "compaction");
}

#[test]
fn compaction_projection_is_fitted_before_its_first_provider_call() {
    let mut runtime = SynchronousRuntime::new_with_history(
        automatic_compaction_config(),
        vec![
            Message::user_text(large_text("old request")),
            assistant_text(large_text("old answer")),
            Message::user_text("new request"),
            assistant_text("working on it"),
        ],
    );
    let turn_id = "turn-fitted-projection";

    let compaction = start_continued_turn(&mut runtime, turn_id);

    let texts = action_messages(&compaction)
        .iter()
        .filter_map(message_text)
        .collect::<Vec<_>>();
    assert!(texts.iter().all(|text| !text.contains("old request")));
    assert!(texts.iter().all(|text| !text.contains("old answer")));
    assert!(texts.iter().any(|text| text.contains("new request")));
    assert!(
        texts
            .last()
            .is_some_and(|text| text.contains("CONTEXT CHECKPOINT COMPACTION"))
    );
}

#[test]
fn steering_waits_for_preflight_compaction_then_enters_the_resumed_agent_input() {
    let turn_id = "turn-steered-preflight-compaction";
    let request = large_text("original direction");
    let mut runtime = SynchronousRuntime::new(automatic_compaction_config());
    let compaction = runtime
        .apply(
            user_message(turn_id, &request, "queue"),
            running(turn_id)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started(turn_id, &request)),
        )
        .only_action();

    runtime.receive_steering(turn_id, "new direction", &[&compaction]);
    let resumed = runtime
        .apply(
            summary_completion(&compaction, "steered summary"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(context_compacted(turn_id, &compaction, "steered summary"))
                .observe(turn_steered(turn_id, "new direction")),
        )
        .only_action();

    assert!(
        action_messages(&resumed)
            .iter()
            .any(|message| message_text(message) == Some("new direction"))
    );
    runtime.finish_turn_with_text(turn_id, &resumed, "done");
}

#[test]
fn large_steering_during_compaction_is_preflighted_before_agent_dispatch() {
    let turn_id = "turn-large-steering-during-compaction";
    let request = large_text("original direction");
    let steering = large_text("new oversized direction");
    let mut runtime = SynchronousRuntime::new(automatic_compaction_config());
    let first_compaction = runtime
        .apply(
            user_message(turn_id, &request, "queue"),
            running(turn_id)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started(turn_id, &request)),
        )
        .only_action();

    runtime.receive_steering(turn_id, &steering, &[&first_compaction]);
    let second_compaction = runtime
        .apply(
            summary_completion(&first_compaction, "first summary"),
            running(turn_id)
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(context_compacted(
                    turn_id,
                    &first_compaction,
                    "first summary",
                ))
                .observe(turn_steered(turn_id, &steering)),
        )
        .only_action();

    assert_eq!(second_compaction["purpose"], "compaction");
    assert_ne!(
        second_compaction["compaction_id"],
        first_compaction["compaction_id"]
    );

    let resumed = runtime
        .apply(
            summary_completion(&second_compaction, "steering summary"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(context_compacted(
                    turn_id,
                    &second_compaction,
                    "steering summary",
                )),
        )
        .only_action();
    runtime.finish_turn_with_text(turn_id, &resumed, "done");
}
