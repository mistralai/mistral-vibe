//! The fallback to a standalone summary request after an in-context one fails.

use super::*;

#[test]
fn empty_summary_falls_back_to_a_standalone_request_then_fails_without_losing_canonical_context() {
    let turn_id = "turn-empty-summary";
    let request = "request retained after empty compaction";
    let mut runtime = runtime_needing_compaction(request);
    let compaction = start_continued_turn(&mut runtime, turn_id);
    let original_messages = action_messages(&compaction).to_vec();

    let retry = runtime
        .apply(
            reasoning_only_completion(&compaction),
            running(turn_id).dispatch(standalone_compaction_call("automatic", 0, 2)),
        )
        .only_action();

    assert_eq!(compaction["compaction_id"], retry["compaction_id"]);
    assert_ne!(action_id(&compaction), action_id(&retry));
    assert_ne!(action_messages(&retry), original_messages);
    assert_standalone_request(&retry, "request retained after empty compaction");

    let error = json!({
        "code": "invalid_compaction_summary",
        "message": "compaction response was empty",
        "retryable": false,
        "details": null,
    });
    runtime.apply(
        reasoning_only_completion(&retry),
        failed(turn_id, error.clone())
            .observe(context_compaction_failed(turn_id, &retry, error.clone()))
            .observe(turn_failed(turn_id, error)),
    );

    let next_turn = runtime
        .apply(
            user_message("turn-after-empty-summary", "continue", "queue"),
            running("turn-after-empty-summary")
                .dispatch(compaction_call("automatic", 0, 1))
                .observe(turn_started("turn-after-empty-summary", "continue")),
        )
        .only_action();
    let resumed = runtime
        .apply(
            summary_completion(&next_turn, "recovered context"),
            running("turn-after-empty-summary")
                .dispatch(llm_call(0))
                .observe(context_compacted(
                    "turn-after-empty-summary",
                    &next_turn,
                    "recovered context",
                )),
        )
        .only_action();
    assert!(action_messages(&resumed).iter().any(|message| {
        message_text(message)
            .is_some_and(|text| text.contains("request retained after empty compaction"))
    }));
}

#[test]
fn summary_with_tool_calls_falls_back_to_a_standalone_request() {
    let turn_id = "turn-summary-tool-call";
    let request = "request requiring compaction";
    let mut runtime = runtime_needing_compaction(request);
    let compaction = start_continued_turn(&mut runtime, turn_id);

    let retry = runtime
        .apply(
            summary_with_tool_call_completion(&compaction, "do not accept this"),
            running(turn_id).dispatch(standalone_compaction_call("automatic", 0, 2)),
        )
        .only_action();

    assert_eq!(retry["compaction_id"], compaction["compaction_id"]);
    let resumed = runtime
        .apply(
            summary_completion(&retry, "valid summary"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(context_compacted(turn_id, &retry, "valid summary")),
        )
        .only_action();
    runtime.finish_turn_with_text(turn_id, &resumed, "done");
}

/// A provider that serves repeated requests from a response cache returns the
/// same malformed summary for the same request. The fallback is a different
/// request, so the replayed failure does not repeat and compaction succeeds.
#[test]
fn malformed_summary_is_recovered_by_a_different_standalone_request() {
    let turn_id = "turn-malformed-summary";
    let request = "request requiring compaction";
    let mut runtime = runtime_needing_compaction(request);
    let compaction = start_continued_turn(&mut runtime, turn_id);
    let malformed = "<summary>the response stops before its closing tag";

    let fallback = runtime
        .apply(
            text_completion(&compaction, malformed),
            running(turn_id).dispatch(standalone_compaction_call("automatic", 0, 2)),
        )
        .only_action();

    assert_ne!(action_id(&fallback), action_id(&compaction));
    assert_ne!(action_messages(&fallback), action_messages(&compaction));
    assert_standalone_request(&fallback, "request requiring compaction");
    let resumed = runtime
        .apply(
            summary_completion(&fallback, "standalone summary"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(context_compacted(turn_id, &fallback, "standalone summary")),
        )
        .only_action();
    assert!(action_messages(&resumed).iter().any(|message| {
        message_text(message).is_some_and(|text| text.contains("standalone summary"))
    }));
    runtime.finish_turn_with_text(turn_id, &resumed, "done");
}

/// A failed in-context request, for example a stream that broke off, does not
/// fail the turn: Core sends the standalone request instead.
#[test]
fn failed_in_context_request_falls_back_to_a_standalone_request() {
    let turn_id = "turn-in-context-failed";
    let request = "request requiring compaction";
    let mut runtime = runtime_needing_compaction(request);
    let compaction = start_continued_turn(&mut runtime, turn_id);

    let fallback = runtime
        .apply(
            completion_failed(&compaction, "model_stream_failed", "the stream broke off"),
            running(turn_id).dispatch(standalone_compaction_call("automatic", 0, 2)),
        )
        .only_action();

    assert_standalone_request(&fallback, "request requiring compaction");
    let resumed = runtime
        .apply(
            summary_completion(&fallback, "standalone summary"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(context_compacted(turn_id, &fallback, "standalone summary")),
        )
        .only_action();
    runtime.finish_turn_with_text(turn_id, &resumed, "done");
}

#[test]
fn standalone_fallback_survives_checkpoint_restore() {
    let turn_id = "turn-standalone-restore";
    let request = "request requiring compaction";
    let mut runtime = runtime_needing_compaction(request);
    let compaction = start_continued_turn(&mut runtime, turn_id);
    let fallback = runtime
        .apply(
            reasoning_only_completion(&compaction),
            running(turn_id).dispatch(standalone_compaction_call("automatic", 0, 2)),
        )
        .only_action();
    assert_eq!(
        runtime.checkpoint_value()["turn"]["phase"]["projection"]["phase"],
        "standalone"
    );

    runtime.restart_from_checkpoint();
    let restored = runtime
        .apply(
            json!({
                "type": "completion_model_input_resync_requested",
                "action_id": action_id(&fallback),
            }),
            running(turn_id).refresh(standalone_compaction_call("automatic", 0, 2)),
        )
        .only_action();

    assert_eq!(action_id(&restored), action_id(&fallback));
    assert_eq!(action_messages(&restored), action_messages(&fallback));
    let resumed = runtime
        .apply(
            summary_completion(&restored, "restored summary"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(context_compacted(turn_id, &restored, "restored summary")),
        )
        .only_action();
    runtime.finish_turn_with_text(turn_id, &resumed, "done");
}

#[test]
fn manual_compaction_falls_back_after_invalid_summary_then_returns_idle() {
    let mut runtime = SynchronousRuntime::new_with_history(
        config(),
        vec![Message::user_text("hello"), assistant_text("hi")],
    );
    let compaction = runtime
        .apply(
            compact("preserve current work"),
            compacting("manual").dispatch(compaction_call("manual", 0, 1)),
        )
        .only_action();
    let retry = runtime
        .apply(
            reasoning_only_completion(&compaction),
            compacting("manual").dispatch(standalone_compaction_call("manual", 0, 2)),
        )
        .only_action();
    let standalone_prompt = action_messages(&retry)
        .last()
        .and_then(message_text)
        .expect("standalone request ends with its prompt");
    assert!(standalone_prompt.ends_with(
        "<extra_compaction_instructions>\npreserve current work\n</extra_compaction_instructions>"
    ));
    let error = json!({
        "code": "invalid_compaction_summary",
        "message": "compaction response was empty",
        "retryable": false,
        "details": null,
    });

    runtime.apply(
        reasoning_only_completion(&retry),
        idle().observe(manual_context_compaction_failed(&retry, error)),
    );
}

/// *Prepare*: A Turn whose in-context summary request failed, so the
/// standalone request is out.
/// *Do*: The standalone request fails too.
/// *Assert*: Nothing follows the standalone request: the compaction fails
/// with the Runtime's error and the Turn fails.
#[test]
fn a_failed_standalone_request_fails_the_compaction() {
    let turn_id = "turn-standalone-failed";
    let request = "request requiring compaction";
    let mut runtime = runtime_needing_compaction(request);
    let compaction = start_continued_turn(&mut runtime, turn_id);
    assert!(compaction.get("request").is_none(), "{compaction}");
    let fallback = runtime
        .apply(
            completion_failed(&compaction, "model_stream_failed", "provider failed"),
            running(turn_id).dispatch(standalone_compaction_call("automatic", 0, 2)),
        )
        .only_action();
    assert_standalone_request(&fallback, "request requiring compaction");

    let error = json!({
        "code": "model_stream_failed",
        "message": "provider failed again",
        "retryable": false,
        "details": null,
    });
    runtime.apply(
        completion_failed(&fallback, "model_stream_failed", "provider failed again"),
        failed(turn_id, error.clone())
            .observe(context_compaction_failed(turn_id, &fallback, error.clone()))
            .observe(turn_failed(turn_id, error)),
    );
}

/// The context settings no longer take a fallback: Standalone always follows
/// a failed in-context request.
#[test]
fn a_config_naming_a_compaction_fallback_is_rejected() {
    let mut value = serde_json::to_value(automatic_compaction_config()).expect("config serializes");
    assert!(
        value["settings"]["context"]
            .get("compaction_fallback")
            .is_none()
    );
    value["settings"]["context"]["compaction_fallback"] = json!("standalone");
    let error = serde_json::from_value::<HarnessConfig>(value)
        .expect_err("an unknown context setting is rejected");
    assert!(error.to_string().contains("compaction_fallback"), "{error}");
}
