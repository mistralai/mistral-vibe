use super::*;
use uuid::Uuid;

fn config_with_lifecycle_hook(point: HookPoint) -> HarnessConfig {
    let mut config = config();
    crate::core::testing::add_always_hook(&mut config, point);
    config
}

fn hook_failed(action_id: &str) -> Value {
    json!({
        "type": "hook_failed",
        "action_id": action_id,
        "error": {
            "code": "hook_failed_for_test",
            "message": "the lifecycle hook failed",
            "retryable": false,
            "details": null,
        },
    })
}

fn complete_pre_agent_hook(action_id: &str, user_text: &str) -> Value {
    json!({
        "type": "hook_completed",
        "action_id": action_id,
        "result": {
            "hook": "pre_agent_turn",
            "output": {
                "type": "continue",
                "user_content": [{"type": "text", "text": user_text}],
            },
        },
    })
}

fn pending_post_llm_hook(
    config: HarnessConfig,
    turn_id: &str,
    draft: &str,
) -> (HarnessSession, String, String) {
    let mut session = HarnessSession::create(config).expect("session creates");
    let started =
        accepted_value(session.apply(input(1, user_message(turn_id, "answer this", "queue"))));
    let completion_action_id = dispatched_action_id(&started).to_string();
    let hook_pending = accepted_value(session.apply(input(
        2,
        completion(
            &completion_action_id,
            json!([{"type": "text", "text": draft}]),
        ),
    )));
    let hook_action_id = dispatched_action_id(&hook_pending).to_string();
    (session, completion_action_id, hook_action_id)
}

///
/// *Prepare*: A completed turn is followed by a second turn waiting on a pre-agent hook, then checkpointed and restored.
/// *Do*: Fail the restored pre-agent hook.
/// *Assert*: The Core restores the exact previous terminal turn and emits no observation or follow-up action for the skipped second turn.
///
#[test]
fn failed_restored_pre_agent_hook_recovers_the_previous_terminal_turn() {
    // Prepare
    let config = config_with_lifecycle_hook(HookPoint::PreAgentTurn);
    let mut session = HarnessSession::create(config.clone()).expect("session creates");
    let first_hook = accepted_value(session.apply(input(
        1,
        user_message("turn-first", "first request", "queue"),
    )));
    let first_hook_action_id = dispatched_action_id(&first_hook).to_string();
    let first_completion = accepted_value(session.apply(input(
        2,
        complete_pre_agent_hook(&first_hook_action_id, "first request"),
    )));
    let first_completion_action_id = dispatched_action_id(&first_completion).to_string();
    let first_completed = accepted_value(session.apply(input(
        3,
        completion(
            &first_completion_action_id,
            json!([{"type": "text", "text": "first answer"}]),
        ),
    )));
    assert_eq!(first_completed["transition"]["turn"]["status"], "completed");
    let previous_terminal_checkpoint = checkpoint_value(&session);

    let second_hook = accepted_value(session.apply(input(
        4,
        user_message("turn-second", "second request", "queue"),
    )));
    let second_hook_action_id = dispatched_action_id(&second_hook).to_string();
    let pending_checkpoint = session.checkpoint().expect("pending hook checkpoints");
    let mut restored =
        HarnessSession::restore(config, pending_checkpoint, 0).expect("pending hook restores");

    // Do
    let failed = accepted_value(restored.apply(input(1, hook_failed(&second_hook_action_id))));

    // Assert
    assert_eq!(failed["transition"]["turn"]["status"], "completed");
    assert_eq!(failed["transition"]["turn"]["turn_id"], "turn-first");
    assert_eq!(failed["transition"]["observations"], json!([]));
    assert_eq!(failed["transition"]["next"], json!({"type": "none"}));
    assert_eq!(checkpoint_value(&restored), previous_terminal_checkpoint);
}

///
/// *Prepare*: Separate active turns are waiting on pre-LLM, post-LLM, and post-agent lifecycle hooks.
/// *Do*: Fail each pending hook through serialized HarnessSession inputs.
/// *Assert*: Every failure discards the correlated completion candidate before failing the turn, with no further action.
///
#[test]
fn failed_completion_lifecycle_hooks_keep_discard_and_failure_order() {
    // Prepare
    let cases = [
        HookPoint::PreLlmCall,
        HookPoint::PostLlmCall,
        HookPoint::PostAgentTurn,
    ];
    let mut pending = Vec::new();
    for (index, point) in cases.into_iter().enumerate() {
        let turn_id = format!("turn-hook-failure-{index}");
        let mut session =
            HarnessSession::create(config_with_lifecycle_hook(point)).expect("session creates");
        let started =
            accepted_value(session.apply(input(1, user_message(&turn_id, "answer this", "queue"))));
        let (next_input_id, hook_action_id, completion_action_id) =
            if point == HookPoint::PreLlmCall {
                (2, dispatched_action_id(&started).to_string(), None)
            } else {
                let completion_action_id = dispatched_action_id(&started).to_string();
                let hook_pending = accepted_value(session.apply(input(
                    2,
                    completion(
                        &completion_action_id,
                        json!([{"type": "text", "text": "draft answer"}]),
                    ),
                )));
                (
                    3,
                    dispatched_action_id(&hook_pending).to_string(),
                    Some(completion_action_id),
                )
            };
        pending.push((
            session,
            turn_id,
            next_input_id,
            hook_action_id,
            completion_action_id,
        ));
    }

    // Do
    let failed = pending
        .into_iter()
        .map(
            |(mut session, turn_id, input_id, hook_action_id, completion_action_id)| {
                let result =
                    accepted_value(session.apply(input(input_id, hook_failed(&hook_action_id))));
                (turn_id, hook_action_id, completion_action_id, result)
            },
        )
        .collect::<Vec<_>>();

    // Assert
    for (turn_id, hook_action_id, completion_action_id, result) in failed {
        let observations = result["transition"]["observations"]
            .as_array()
            .expect("failure observations are an array");
        assert_eq!(observations.len(), 2);
        assert_eq!(
            observations[0]["type"],
            "agent_completion_candidate_discarded"
        );
        assert_eq!(observations[0]["turn_id"], turn_id);
        let observed_completion_action_id = observations[0]["action_id"]
            .as_str()
            .expect("discard observation contains a completion action ID");
        Uuid::parse_str(observed_completion_action_id)
            .expect("discarded completion action ID is a UUID");
        assert_ne!(observed_completion_action_id, hook_action_id);
        if let Some(completion_action_id) = completion_action_id {
            assert_eq!(observed_completion_action_id, completion_action_id);
        }
        assert_eq!(observations[0]["cause"]["type"], "failure");
        assert_eq!(
            observations[0]["cause"]["error"]["code"],
            "hook_failed_for_test"
        );
        assert_eq!(observations[1]["type"], "turn_failed");
        assert_eq!(observations[1]["turn_id"], turn_id);
        assert_eq!(observations[1]["error"]["code"], "hook_failed_for_test");
        assert_eq!(result["transition"]["turn"]["status"], "failed");
        assert_eq!(result["transition"]["next"], json!({"type": "none"}));
    }
}

///
/// *Prepare*: A restored active turn is waiting on its pre-LLM hook.
/// *Do*: Complete the hook with a skip result.
/// *Assert*: The correlated completion is discarded as skipped and the turn completes empty without another action.
///
#[test]
fn restored_pre_llm_skip_completes_without_calling_the_model() {
    // Prepare
    let config = config_with_lifecycle_hook(HookPoint::PreLlmCall);
    let mut session = HarnessSession::create(config.clone()).expect("session creates");
    let pending = accepted_value(session.apply(input(
        1,
        user_message("turn-pre-llm-skip", "skip this call", "queue"),
    )));
    let hook_action_id = dispatched_action_id(&pending).to_string();
    let checkpoint = session
        .checkpoint()
        .expect("pending pre-LLM hook checkpoints");
    let mut restored = HarnessSession::restore(config, checkpoint, 0).expect("checkpoint restores");

    // Do
    let skipped = accepted_value(restored.apply(input(
        1,
        json!({
            "type": "hook_completed",
            "action_id": hook_action_id,
            "result": {
                "hook": "pre_llm_call",
                "output": {
                    "type": "skip",
                    "reason": [{"type": "text", "text": "runtime policy"}],
                },
            },
        }),
    )));

    // Assert
    let observations = skipped["transition"]["observations"]
        .as_array()
        .expect("observations are an array");
    assert_eq!(observations.len(), 2);
    assert_eq!(
        observations[0]["type"],
        "agent_completion_candidate_discarded"
    );
    let completion_action_id = observations[0]["action_id"]
        .as_str()
        .expect("discard observation contains a completion action ID");
    Uuid::parse_str(completion_action_id).expect("discarded completion action ID is a UUID");
    assert_ne!(completion_action_id, hook_action_id);
    assert_eq!(observations[0]["cause"], json!({"type": "skipped"}));
    assert_eq!(observations[1]["type"], "turn_completed");
    assert_eq!(observations[1]["output"], json!([]));
    assert_eq!(skipped["transition"]["turn"]["status"], "completed");
    assert_eq!(skipped["transition"]["next"], json!({"type": "none"}));
}

///
/// *Prepare*: A restored post-LLM hook holds an uncommitted draft completion.
/// *Do*: Request a retry with feedback.
/// *Assert*: The draft is discarded, feedback enters model input, and the next completion uses iteration one.
///
#[test]
fn restored_completion_retry_adds_feedback_without_committing_the_draft() {
    // Prepare
    let mut config = config_with_lifecycle_hook(HookPoint::PostLlmCall);
    config.settings.turn.max_iterations = Some(3);
    let (session, completion_action_id, hook_action_id) =
        pending_post_llm_hook(config.clone(), "turn-retry", "rejected draft");
    let checkpoint = session
        .checkpoint()
        .expect("pending post-LLM hook checkpoints");
    let mut restored = HarnessSession::restore(config, checkpoint, 0).expect("checkpoint restores");

    // Do
    let retried = accepted_value(restored.apply(input(
        1,
        json!({
            "type": "hook_completed",
            "action_id": hook_action_id,
            "result": {
                "hook": "post_llm_call",
                "output": {
                    "type": "retry",
                    "feedback": [{"type": "text", "text": "add evidence"}],
                },
            },
        }),
    )));

    // Assert
    let observations = retried["transition"]["observations"]
        .as_array()
        .expect("observations are an array");
    assert_eq!(observations.len(), 1);
    assert_eq!(
        observations[0]["type"],
        "agent_completion_candidate_discarded"
    );
    assert_eq!(observations[0]["action_id"], completion_action_id);
    assert_eq!(observations[0]["cause"], json!({"type": "retry"}));
    let action = &retried["transition"]["next"]["directives"][0]["action"];
    assert_eq!(action["type"], "llm_call");
    assert_eq!(action["iteration"], 1);
    let model_input =
        serde_json::to_string(&action["model_input"]).expect("model input serializes");
    assert!(model_input.contains("add evidence"));
    assert!(!model_input.contains("rejected draft"));
    assert_eq!(retried["transition"]["turn"]["status"], "running");
}

///
/// *Prepare*: A restored post-LLM hook holds an uncommitted draft completion.
/// *Do*: Reject the candidate with a reason.
/// *Assert*: The turn completes empty, the draft never enters later model input, and no further action is dispatched for the rejected turn.
///
#[test]
fn restored_completion_rejection_keeps_the_draft_out_of_context() {
    // Prepare
    let config = config_with_lifecycle_hook(HookPoint::PostLlmCall);
    let (session, completion_action_id, hook_action_id) =
        pending_post_llm_hook(config.clone(), "turn-reject", "unsafe draft");
    let checkpoint = session
        .checkpoint()
        .expect("pending post-LLM hook checkpoints");
    let mut restored = HarnessSession::restore(config, checkpoint, 0).expect("checkpoint restores");

    // Do
    let rejected = accepted_value(restored.apply(input(
        1,
        json!({
            "type": "hook_completed",
            "action_id": hook_action_id,
            "result": {
                "hook": "post_llm_call",
                "output": {
                    "type": "reject",
                    "reason": [{"type": "text", "text": "unsafe"}],
                },
            },
        }),
    )));
    let next_turn = accepted_value(restored.apply(input(
        2,
        user_message("turn-after-reject", "try something else", "queue"),
    )));

    // Assert
    let observations = rejected["transition"]["observations"]
        .as_array()
        .expect("observations are an array");
    assert_eq!(observations.len(), 2);
    assert_eq!(
        observations[0]["type"],
        "agent_completion_candidate_discarded"
    );
    assert_eq!(observations[0]["action_id"], completion_action_id);
    assert_eq!(observations[0]["cause"], json!({"type": "rejected"}));
    assert_eq!(observations[1]["type"], "turn_completed");
    assert_eq!(observations[1]["output"], json!([]));
    assert_eq!(rejected["transition"]["turn"]["status"], "completed");
    assert_eq!(rejected["transition"]["next"], json!({"type": "none"}));
    let next_model_input = serde_json::to_string(
        &next_turn["transition"]["next"]["directives"][0]["action"]["model_input"],
    )
    .expect("next model input serializes");
    assert!(!next_model_input.contains("unsafe draft"));
}
