use super::*;
use crate::core::config::ImageDeliveryMode;
use crate::core::features::compaction::overflow::EstimateCorrection;
use crate::core::prompt::PromptSettings;
use crate::core::wire::message::{AssistantPart, AssistantSemanticPart, ToolArguments};

fn prompts() -> Prompts {
    PromptSettings::default().compaction
}

fn budget(token_threshold: Option<u64>) -> CompactionBudget {
    CompactionBudget {
        token_threshold,
        image_delivery: ImageDeliveryMode::Native,
        image_cost: crate::core::testing::test_model_facts().image_cost,
    }
}

fn system() -> StoredMessage {
    StoredMessage::generated_system(Message::system_text("agent system prompt"))
}

fn user(text: impl Into<String>) -> StoredMessage {
    StoredMessage::visible(Message::user_text(text.into()))
}

fn reply(text: &str, calls: &[(&str, &str)]) -> StoredMessage {
    let mut parts = vec![AssistantPart::Content(ContentBlock::text(text))];
    parts.extend(calls.iter().map(|(id, name)| {
        AssistantPart::Semantic(AssistantSemanticPart::ToolCall {
            id: id.to_string(),
            name: name.to_string(),
            arguments: ToolArguments::Json {
                raw: "{}".to_string(),
                value: serde_json::json!({}),
            },
            meta: None,
        })
    }));
    StoredMessage::visible(Message::assistant(parts))
}

fn result(id: &str, name: &str, text: impl Into<String>) -> StoredMessage {
    StoredMessage::visible(Message::tool(
        id.to_string(),
        name.to_string(),
        ToolOutcome::Success,
        vec![ContentBlock::text(text.into())],
        None,
    ))
}

/// An earlier exchange, then a reply with two tool calls whose results
/// came back, then a steer from the user.
fn history() -> Vec<StoredMessage> {
    vec![
        system(),
        user(format!("Fix the parser {}", "a".repeat(4_000))),
        reply("Reading.", &[("call-0", "read_file")]),
        result("call-0", "read_file", "b".repeat(8_000)),
        reply("Running both.", &[("call-1", "bash"), ("call-2", "grep")]),
        result("call-1", "bash", "c".repeat(40_000)),
        result("call-2", "grep", "d".repeat(40_000)),
        user("also check the tests"),
    ]
}

fn fitted(fit: CompactionProjectionFit) -> CompactionProjection {
    let CompactionProjectionFit::Fitted(projection) = fit else {
        panic!("the projection fits");
    };
    projection
}

fn initial(canonical: &[StoredMessage], token_threshold: Option<u64>) -> CompactionProjection {
    fitted(
        CompactionProjection::initial(
            canonical,
            system(),
            "",
            &[],
            budget(token_threshold),
            &prompts(),
        )
        .expect("projection is valid"),
    )
}

fn in_context() -> CompactionProjection {
    initial(
        &[system(), user("Fix the parser"), reply("Done.", &[])],
        None,
    )
}

fn last_text(projection: &CompactionProjection) -> Option<String> {
    let message = serde_json::to_value(&projection.model_messages().last()?.message).ok()?;
    (message["role"] == "user")
        .then(|| message["content"][0]["text"].as_str().map(str::to_string))
        .flatten()
}

fn estimate(projection: &CompactionProjection) -> u64 {
    projection
        .request_estimate(&[], budget(None))
        .expect("estimate")
}

/// The refit limit after `projection`'s request was refused, as the turn
/// computes it.
fn refit_limit(projection: &CompactionProjection) -> u64 {
    EstimateCorrection::refit_limit(estimate(projection))
}

#[test]
fn a_compaction_starts_in_context_and_sends_the_projection() {
    let projection = in_context();
    assert_eq!(projection.phase(), &CompactionPhase::InContext);
    assert_eq!(projection.attempt(), 0);
    assert_eq!(projection.model_messages(), projection.messages());
}

#[test]
fn the_in_context_request_is_the_last_accepted_input_and_its_reply() {
    let canonical = history();
    let projection = initial(&canonical, None);
    let request = projection.model_messages();
    // The prefix is the request that produced the reply, then the reply,
    // byte for byte, so the provider can serve it from its cache.
    assert_eq!(
        serde_json::to_string(&request[..5]).unwrap(),
        serde_json::to_string(&canonical[..5]).unwrap()
    );
    // One placeholder per tool call of the reply, paired by ID and name,
    // in call order; the real results and the steer stay out.
    let placeholders = &request[5..request.len() - 1];
    let paired = placeholders
        .iter()
        .map(|stored| match &stored.message {
            Message::Tool {
                tool_call_id,
                name,
                content,
                ..
            } => {
                assert!(stored.is_injected());
                assert_eq!(
                    content,
                    &vec![ContentBlock::text(prompts().tool_result_placeholder)]
                );
                (tool_call_id.as_str(), name.as_str())
            }
            other => panic!("expected a placeholder result, got {other:?}"),
        })
        .collect::<Vec<_>>();
    assert_eq!(paired, [("call-1", "bash"), ("call-2", "grep")]);
    let request_text = serde_json::to_string(request).unwrap();
    assert!(!request_text.contains(&"c".repeat(100)));
    assert!(!request_text.contains("also check the tests"));
    validate_prompt(request).expect("the request ends with the compaction prompt");
}

#[test]
fn a_reply_without_tool_calls_gets_no_placeholder() {
    let projection = in_context();
    assert_eq!(projection.model_messages().len(), 4);
    assert!(
        !projection
            .model_messages()
            .iter()
            .any(|stored| matches!(stored.message, Message::Tool { .. }))
    );
}

#[test]
fn a_history_without_a_reply_starts_standalone() {
    let projection = initial(&[system(), user("Fix the parser")], None);
    assert_eq!(projection.phase(), &CompactionPhase::Standalone);
    assert_eq!(projection.attempt(), 0);
    assert_eq!(projection.request_kind(), CompactionRequestKind::Standalone);
}

#[test]
fn the_in_context_request_removes_the_oldest_history_to_fit() {
    let canonical = history();
    let full = estimate(&initial(&canonical, None));
    let projection = initial(&canonical, Some(full - 1));
    assert_eq!(projection.phase(), &CompactionPhase::InContext);
    assert!(estimate(&projection) < full - 1);
    // The oldest exchange went, with its call and result together; the
    // reply and its placeholders stayed.
    let text = serde_json::to_string(projection.model_messages()).unwrap();
    assert!(!text.contains("call-0"));
    assert!(text.contains("call-1") && text.contains("call-2"));
}

#[test]
fn an_in_context_request_over_the_budget_at_its_minimal_size_goes_standalone() {
    let canonical = vec![
        system(),
        user("Fix the parser"),
        reply(&"e".repeat(20_000), &[("call-1", "bash")]),
    ];
    let projection = initial(&canonical, Some(2_000));
    assert_eq!(projection.phase(), &CompactionPhase::Standalone);
    assert_eq!(projection.attempt(), 0);
    assert!(estimate(&projection) < 2_000);
}

#[test]
fn a_failed_in_context_request_falls_back_to_standalone() {
    let standalone = in_context()
        .after_failure(&[], budget(None), &prompts())
        .expect("standalone renders")
        .expect("standalone follows in-context");
    assert_eq!(standalone.phase(), &CompactionPhase::Standalone);
    assert_eq!(standalone.request_kind(), CompactionRequestKind::Standalone);
    assert_eq!(standalone.attempt(), 1);
    assert_eq!(standalone.model_messages().len(), 2);
}

#[test]
fn nothing_follows_a_failed_standalone_or_cut_off_retry_request() {
    let prompts = prompts();
    let standalone = in_context()
        .after_failure(&[], budget(None), &prompts)
        .unwrap()
        .unwrap();
    assert!(
        standalone
            .after_failure(&[], budget(None), &prompts)
            .unwrap()
            .is_none()
    );
    let retry = standalone
        .after_cut_off(None, &[], budget(None), &prompts)
        .unwrap()
        .unwrap();
    assert!(
        retry
            .after_failure(&[], budget(None), &prompts)
            .unwrap()
            .is_none()
    );
}

#[test]
fn a_cut_off_summary_is_asked_for_again_with_the_instruction() {
    let prompts = prompts();
    for start in [
        in_context(),
        in_context()
            .after_failure(&[], budget(None), &prompts)
            .unwrap()
            .unwrap(),
    ] {
        let attempt = start.attempt();
        let retry = start
            .after_cut_off(Some("partial".to_string()), &[], budget(None), &prompts)
            .unwrap()
            .expect("a first cut-off is retried");
        assert_eq!(
            retry.phase(),
            &CompactionPhase::CutOffRetry {
                attempt: 1,
                partial: Some("partial".to_string()),
            }
        );
        assert_eq!(retry.attempt(), attempt + 1);
        assert_eq!(retry.request_kind(), CompactionRequestKind::Standalone);
        assert_eq!(last_text(&retry), Some(prompts.cut_off_retry.clone()));
    }
}

#[test]
fn an_empty_cut_off_text_is_no_partial() {
    let retry = in_context()
        .after_cut_off(Some("  \n".to_string()), &[], budget(None), &prompts())
        .unwrap()
        .unwrap();
    assert_eq!(
        retry.phase(),
        &CompactionPhase::CutOffRetry {
            attempt: 1,
            partial: None,
        }
    );
}

#[test]
fn a_cut_off_retry_cut_off_again_has_no_retry_left() {
    let prompts = prompts();
    let retry = in_context()
        .after_cut_off(Some("partial".to_string()), &[], budget(None), &prompts)
        .unwrap()
        .unwrap();
    assert!(
        retry
            .after_cut_off(
                Some("a longer partial".to_string()),
                &[],
                budget(None),
                &prompts
            )
            .unwrap()
            .is_none()
    );
}

#[test]
fn the_longer_partial_is_kept() {
    assert_eq!(
        longer(Some("short".to_string()), Some("much longer".to_string())),
        Some("much longer".to_string())
    );
    assert_eq!(
        longer(Some("much longer".to_string()), Some("short".to_string())),
        Some("much longer".to_string())
    );
    assert_eq!(
        longer(None, Some("new".to_string())),
        Some("new".to_string())
    );
    assert_eq!(
        longer(Some("old".to_string()), None),
        Some("old".to_string())
    );
}

#[test]
fn a_refused_in_context_request_is_refitted_in_the_same_phase() {
    let prompts = prompts();
    let projection = initial(&history(), Some(1_000_000));
    let limit = refit_limit(&projection);
    // The budget the grown correction leaves is still above the refused
    // request: the refusal itself bounds the refit.
    let refit = projection
        .after_refusal(limit, &[], budget(Some(1_000_000)), &prompts)
        .unwrap()
        .expect("a smaller in-context request fits");
    assert_eq!(refit.phase(), &CompactionPhase::InContext);
    assert_eq!(refit.attempt(), 1);
    assert_eq!(refit.refit_limit(), Some(limit));
    assert!(estimate(&refit) < limit);
    let text = serde_json::to_string(refit.model_messages()).unwrap();
    assert!(!text.contains("call-0"));
    assert!(text.contains("call-1") && text.contains("call-2"));

    // A smaller budget from the grown correction bounds it too.
    let smaller = projection
        .after_refusal(limit, &[], budget(Some(limit / 2)), &prompts)
        .unwrap()
        .unwrap();
    assert!(estimate(&smaller) < limit / 2);

    // A later refusal never raises the limit a earlier one set.
    let again = refit
        .after_refusal(limit * 2, &[], budget(Some(1_000_000)), &prompts)
        .unwrap()
        .unwrap();
    assert_eq!(again.refit_limit(), Some(limit));
}

#[test]
fn an_in_context_request_refused_at_its_minimal_size_goes_standalone() {
    let prompts = prompts();
    let projection = initial(
        &[
            system(),
            user("Fix the parser"),
            reply(&"e".repeat(20_000), &[("call-1", "bash")]),
        ],
        Some(1_000_000),
    );
    let refused = estimate(&projection);
    let standalone = projection
        .after_refusal(
            refit_limit(&projection),
            &[],
            budget(Some(1_000_000)),
            &prompts,
        )
        .unwrap()
        .expect("the standalone request is smaller");
    // Nothing older than the reply can go, so the refit is standalone, with
    // the long reply truncated in its transcript.
    assert_eq!(standalone.phase(), &CompactionPhase::Standalone);
    assert_eq!(standalone.attempt(), 1);
    assert!(estimate(&standalone) < refused);
}

#[test]
fn a_standalone_request_is_refitted_until_its_minimal_size_is_refused() {
    let prompts = prompts();
    let mut projection = initial(&history(), Some(1_000_000))
        .after_failure(&[], budget(Some(1_000_000)), &prompts)
        .unwrap()
        .unwrap();
    let mut refusals = 0;
    loop {
        let refused = estimate(&projection);
        let Some(refit) = projection
            .after_refusal(
                refit_limit(&projection),
                &[],
                budget(Some(1_000_000)),
                &prompts,
            )
            .unwrap()
        else {
            break;
        };
        assert_eq!(refit.phase(), &CompactionPhase::Standalone);
        assert!(estimate(&refit) < refused);
        projection = refit;
        refusals += 1;
    }
    // Each refusal shrinks the request by at least a quarter, so the
    // refits reach the smallest request quickly.
    assert!(refusals < 20, "{refusals} refits");
    // The last request sent kept only the newest part of the transcript.
    let text = serde_json::to_string(projection.model_messages()).unwrap();
    assert!(!text.contains(&"a".repeat(4_000)));
}

#[test]
fn a_refused_cut_off_retry_keeps_its_phase_and_partial() {
    let prompts = prompts();
    let retry = initial(&history(), Some(1_000_000))
        .after_cut_off(
            Some("partial".to_string()),
            &[],
            budget(Some(1_000_000)),
            &prompts,
        )
        .unwrap()
        .unwrap();
    let refit = retry
        .after_refusal(refit_limit(&retry), &[], budget(Some(1_000_000)), &prompts)
        .unwrap()
        .unwrap();
    assert_eq!(refit.phase(), retry.phase());
    assert_eq!(last_text(&refit), Some(prompts.cut_off_retry.clone()));
    assert!(estimate(&refit) < estimate(&retry));
}

#[test]
fn restore_renders_the_phase_and_rejects_an_impossible_attempt() {
    let prompts = prompts();
    let projection = in_context();
    let restore = |attempt, phase, refit_limit| {
        CompactionProjection::restore(
            projection.messages().to_vec(),
            attempt,
            phase,
            refit_limit,
            &[],
            budget(None),
            &prompts,
        )
    };
    let retry = fitted(
        restore(
            2,
            CompactionPhase::CutOffRetry {
                attempt: 1,
                partial: None,
            },
            None,
        )
        .unwrap(),
    );
    assert_eq!(last_text(&retry), Some(prompts.cut_off_retry.clone()));
    // A first request starts standalone without a reply, and a refusal
    // refits in context at a later attempt.
    assert!(restore(0, CompactionPhase::Standalone, None).is_ok());
    assert!(restore(2, CompactionPhase::InContext, None).is_ok());
    assert!(
        restore(
            1,
            CompactionPhase::CutOffRetry {
                attempt: 2,
                partial: None,
            },
            None,
        )
        .is_err()
    );
    assert!(
        restore(
            0,
            CompactionPhase::CutOffRetry {
                attempt: 1,
                partial: None,
            },
            None,
        )
        .is_err()
    );
    // No request fits a zero limit.
    assert!(matches!(
        restore(1, CompactionPhase::Standalone, Some(0)).unwrap(),
        CompactionProjectionFit::TooLarge
    ));
}

#[test]
fn restore_refits_under_the_recorded_refit_limit() {
    let prompts = prompts();
    let projection = initial(&history(), Some(1_000_000));
    let refit = projection
        .after_refusal(
            refit_limit(&projection),
            &[],
            budget(Some(1_000_000)),
            &prompts,
        )
        .unwrap()
        .unwrap();
    let restored = fitted(
        CompactionProjection::restore(
            refit.messages().to_vec(),
            refit.attempt(),
            refit.phase().clone(),
            refit.refit_limit(),
            &[],
            budget(Some(1_000_000)),
            &prompts,
        )
        .unwrap(),
    );
    assert_eq!(restored, refit);
}
