//! Summaries cut off at the output limit, and the brief retry.

use super::*;

/// Bare text is accepted as a summary only when the response ended normally.
/// Bare text cut off at the output limit is incomplete: it is asked for again
/// once. When the retry is cut off too, Core keeps the longer cut-off text
/// with the note after it (Truncate), warns with `compaction_summary_cut_off`
/// and resumes the turn.
#[test]
fn bare_summary_cut_off_twice_at_the_output_limit_is_kept_with_the_note() {
    let turn_id = "turn-truncated-summary";
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

    let partial = "a bare summary that stops mid";
    let fallback = runtime
        .apply(
            length_completion(&compaction, partial),
            running(turn_id).dispatch(standalone_compaction_call("automatic", 0, 2)),
        )
        .only_action();
    assert_cut_off_retry_request(&fallback, "request requiring compaction");

    let resumed = runtime
        .apply(
            length_completion(&fallback, "<summary>still unfinished"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(context_compacted(turn_id, &fallback, partial))
                .observe(summary_cut_off(
                    turn_id,
                    "cut_off",
                    partial.len(),
                    partial.len(),
                )),
        )
        .only_action();
    let held = format!("{partial}\n\n{SUMMARY_CUT_NOTE}");
    assert!(
        action_messages(&resumed)
            .iter()
            .filter_map(message_text)
            .any(|text| text.contains(&held))
    );
    runtime.finish_turn_with_text(turn_id, &resumed, "done");
}

/// *Prepare*: A Turn whose summary request was cut off once and whose
/// cut-off retry is out.
/// *Do*: The retry returns no text at all, cut off at the output limit.
/// *Assert*: The first cut-off text is the longer one, so Core keeps it with
/// the note.
#[test]
fn an_empty_cut_off_retry_keeps_the_first_cut_off_text() {
    let turn_id = "turn-empty-cut-off-retry";
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
    let partial = "the first cut-off text";
    let retry = runtime
        .apply(
            length_completion(&compaction, &format!("<summary>{partial}")),
            running(turn_id).dispatch(standalone_compaction_call("automatic", 0, 2)),
        )
        .only_action();

    runtime.apply(
        length_completion(&retry, "  "),
        running(turn_id)
            .dispatch(llm_call(0))
            .observe(context_compacted(turn_id, &retry, partial))
            .observe(summary_cut_off(
                turn_id,
                "cut_off",
                partial.len(),
                partial.len(),
            )),
    );
}

/// *Prepare*: A Turn whose summary request was cut off with no text at all,
/// or with empty delimiters.
/// *Do*: The cut-off retry is cut off with no text either.
/// *Assert*: An empty cut-off text counts as none, and a closing delimiter is
/// no text: there is nothing to keep, so the compaction and the turn fail.
#[test]
fn cut_off_twice_with_no_text_fails_the_compaction() {
    for (index, first) in ["<summary>", "<summary></summary>"].into_iter().enumerate() {
        let turn_id = format!("turn-empty-cut-offs-{index}");
        let turn_id = turn_id.as_str();
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
        let retry = runtime
            .apply(
                length_completion(&compaction, first),
                running(turn_id).dispatch(standalone_compaction_call("automatic", 0, 2)),
            )
            .only_action();
        let error = json!({
            "code": "invalid_compaction_summary",
            "message": "compaction response reached the output token limit before a complete summary",
            "retryable": false,
            "details": null,
        });

        runtime.apply(
            length_completion(&retry, ""),
            failed(turn_id, error.clone())
                .observe(context_compaction_failed(turn_id, &retry, error.clone()))
                .observe(turn_failed(turn_id, error)),
        );
    }
}

/// *Prepare*: A Turn whose in-context summary request was stopped at the
/// reasoning limit, which a Runtime reports as a reasoning-only reply cut off
/// at its output limit.
/// *Do*: The cut-off retry is cut off with whitespace only.
/// *Assert*: Reasoning and whitespace count as no text: no summary is made of
/// the note alone, and the compaction and the turn fail.
#[test]
fn reasoning_then_whitespace_cut_off_fails_without_an_empty_summary() {
    let turn_id = "turn-reasoning-cut-offs";
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
    let mut stopped = reasoning_only_completion(&compaction);
    stopped["result"]["finish_reason"] = json!("length");
    let retry = runtime
        .apply(
            stopped,
            running(turn_id).dispatch(standalone_compaction_call("automatic", 0, 2)),
        )
        .only_action();
    let error = json!({
        "code": "invalid_compaction_summary",
        "message": "compaction response reached the output token limit before a complete summary",
        "retryable": false,
        "details": null,
    });

    runtime.apply(
        length_completion(&retry, " \n\t<summary>\n "),
        failed(turn_id, error.clone())
            .observe(context_compaction_failed(turn_id, &retry, error.clone()))
            .observe(turn_failed(turn_id, error)),
    );
}

#[test]
fn delimited_summary_at_the_output_limit_is_accepted() {
    let turn_id = "turn-complete-summary-at-limit";
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
            length_completion(&compaction, "<summary>complete summary</summary>"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(context_compacted(turn_id, &compaction, "complete summary")),
        )
        .only_action();
    runtime.finish_turn_with_text(turn_id, &resumed, "done");
}

/// *Prepare*: A Turn whose in-context summary request is out.
/// *Do*: The summary is cut off at the output limit, then the retry returns a
/// complete summary.
/// *Assert*: The retry is the standalone request with the instruction to
/// answer briefly, and its summary replaces the history.
#[test]
fn a_cut_off_summary_is_asked_for_again_briefly() {
    let turn_id = "turn-cut-off-retry";
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
    let retry = runtime
        .apply(
            length_completion(&compaction, "<summary>a summary that stops mid"),
            running(turn_id).dispatch(standalone_compaction_call("automatic", 0, 2)),
        )
        .only_action();
    assert_cut_off_retry_request(&retry, "request requiring compaction");
    let resumed = runtime
        .apply(
            summary_completion(&retry, "brief summary"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(context_compacted(turn_id, &retry, "brief summary")),
        )
        .only_action();
    runtime.finish_turn_with_text(turn_id, &resumed, "done");
}

/// *Prepare*: A cut-off retry is out and survives a checkpoint restore.
/// *Do*: Ask for the request again after the restore.
/// *Assert*: The checkpoint names the phase and the cut-off text so far, and
/// the restored request is the same.
#[test]
fn a_cut_off_retry_survives_checkpoint_restore() {
    let turn_id = "turn-cut-off-retry-restore";
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
    let retry = runtime
        .apply(
            length_completion(&compaction, "<summary>a summary that stops mid"),
            running(turn_id).dispatch(standalone_compaction_call("automatic", 0, 2)),
        )
        .only_action();
    let projection = &runtime.checkpoint_value()["turn"]["phase"]["projection"];
    assert_eq!(projection["phase"], "cut_off_retry");
    assert_eq!(
        projection["cut_off_retry"],
        json!({"attempt": 1, "partial": "a summary that stops mid"})
    );

    runtime.restart_from_checkpoint();
    let restored = runtime
        .apply(
            json!({
                "type": "completion_model_input_resync_requested",
                "action_id": action_id(&retry),
            }),
            running(turn_id).refresh(standalone_compaction_call("automatic", 0, 2)),
        )
        .only_action();
    assert_eq!(action_messages(&restored), action_messages(&retry));
    assert_cut_off_retry_request(&restored, "request requiring compaction");
}

/// *Prepare*: A turn whose summary request was cut off with some text, so
/// the cut-off retry is out with that text saved.
/// *Do*: The retry answers with a tool call, which a summary may not have.
/// *Assert*: Nothing follows a failed cut-off retry, so Core keeps the saved
/// text with the note instead of failing, warns with
/// `compaction_summary_cut_off` and resumes the turn.
#[test]
fn a_failed_cut_off_retry_keeps_the_saved_text() {
    let turn_id = "turn-cut-off-retry-failed";
    let mut runtime = runtime_needing_compaction("request requiring compaction");
    let compaction = start_continued_turn(&mut runtime, turn_id);
    let partial = "what was written before the cut";
    let retry = runtime
        .apply(
            length_completion(&compaction, &format!("<summary>{partial}")),
            running(turn_id).dispatch(standalone_compaction_call("automatic", 0, 2)),
        )
        .only_action();

    let mut compacted = context_compacted(turn_id, &retry, partial);
    compacted["usage"] = Value::Null;
    let resumed = runtime
        .apply(
            summary_with_tool_call_completion(&retry, "a summary with a tool call"),
            running(turn_id)
                .dispatch(llm_call(0))
                .observe(compacted)
                .observe(summary_cut_off(
                    turn_id,
                    "cut_off",
                    partial.len(),
                    partial.len(),
                )),
        )
        .only_action();
    assert!(
        action_messages(&resumed)
            .iter()
            .filter_map(message_text)
            .any(|text| text.contains(&format!("{partial}\n\n{SUMMARY_CUT_NOTE}")))
    );
    runtime.finish_turn_with_text(turn_id, &resumed, "done");
}
