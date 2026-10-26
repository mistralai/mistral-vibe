use serde_json::json;

use super::*;
use crate::core::UserMessageMode;
use crate::core::config::{ProvidedToolDefinition, ProvidedToolExposure, ToolGroupDefinition};
use crate::core::conversation::{ConversationMessage, ConversationSource};
use crate::core::step_protocol::{
    DeterminismContext, HarnessActionDirective, HarnessApplyResult, HarnessConfigurationChange,
    HarnessInput, HarnessNextAction, ModelMessageUpdate, ModelToolCatalogUpdate,
};
use crate::core::wire::content::ContentBlock;

/// *Prepare*: A valid, nonempty conversation seed and valid, nonempty initial history.
/// *Do*: Create a session with both.
/// *Assert*: Creation is refused for supplying both, not for an invalid input, so the
/// child's history has one source.
#[test]
fn creation_rejects_both_a_conversation_seed_and_initial_history() {
    // Prepare
    let seed = ConversationSeed {
        messages: vec![ConversationMessage {
            message: Message::user_text("inherited request"),
            turn_index: Some(0),
            source: ConversationSource::History,
        }],
    };
    let initial_history = vec![Message::user_text("plain initial history")];

    // Do
    let result = HarnessSession::create_with_conversation(
        crate::core::testing::config(),
        initial_history,
        Some(seed),
    );

    // Assert
    assert_eq!(
        result.err().as_deref(),
        Some("initial history and conversation seed cannot both be supplied")
    );
}

fn input(input_id: u64, command: HarnessCommand) -> HarnessInput {
    HarnessInput {
        protocol_version: STEP_PROTOCOL_VERSION,
        input_id,
        determinism: DeterminismContext {
            time_unix_ms: 1_700_000_000_000 + input_id,
            random_seed: input_id as u32,
        },
        command,
    }
}

///
/// *Prepare*: A session with model context has an exhausted tool-catalog revision.
/// *Do*: Reconfigure its capabilities so Core must commit a changed tool catalog.
/// *Assert*: The command is rejected without committing candidate state or consuming the input.
///
#[test]
fn tool_catalog_revision_overflow_rejects_without_committing_candidate() {
    // Prepare
    let mut session = HarnessSession::create(crate::core::testing::config()).unwrap();
    assert!(matches!(
        session.apply(input(
            1,
            HarnessCommand::ContextMessage {
                content: vec![ContentBlock::text("background")],
            },
        )),
        HarnessApplyResult::Accepted { .. }
    ));
    session.state.tool_catalog.revision = u64::MAX;
    let previous_state = session.state.clone();
    let previous_inspection = session.inspect();
    let mut capabilities = session.state.config.capabilities.clone();
    capabilities.tool_groups.push(ToolGroupDefinition {
        name: "workspace".to_string(),
        description: "Workspace tools".to_string(),
        metadata: None,
        icon_url: None,
        tools: vec![ProvidedToolDefinition {
            name: "lookup".to_string(),
            description: "Lookup".to_string(),
            input_schema: json!({ "type": "object" }),
            output_schema: None,
            exposure: ProvidedToolExposure::Direct,
        }],
    });

    // Do
    let result = session.apply(input(
        2,
        HarnessCommand::Reconfigure {
            changes: vec![HarnessConfigurationChange::Capabilities {
                value: capabilities,
            }],
        },
    ));

    // Assert
    let HarnessApplyResult::Rejected {
        rejection: HarnessCommandRejection::InvalidCommand { error },
    } = result
    else {
        panic!("expected an invalid-command rejection, got {result:?}")
    };
    assert_eq!(error.message, "model tool catalog revision overflowed");
    assert_eq!(session.state, previous_state);
    assert_eq!(session.inspect(), previous_inspection);
}

/// Settings with automatic compaction on a 262,144-token model.
fn automatic_compaction_config() -> HarnessConfig {
    let mut config = crate::core::testing::config();
    config.settings.context.compaction =
        crate::core::features::compaction::CompactionPolicy::Automatic;
    config
}

/// The same settings on a 32,000-token model.
fn small_model_config() -> HarnessConfig {
    let mut config = automatic_compaction_config();
    let facts = &mut config.settings.context.model_facts;
    facts.context_window = 32_000;
    facts.compaction_threshold = 16_000;
    facts.compaction_threshold_max = 24_000;
    config
}

fn large_text() -> String {
    "word ".repeat(60_000)
}

/// A Session whose newest message is a large result of a tool the model asked for.
fn session_ending_in_large_tool_result() -> HarnessSession {
    HarnessSession::create_with_history(
        automatic_compaction_config(),
        vec![
            Message::user_text("Read the log."),
            crate::core::testing::assistant_tool("read_log", json!({})),
            Message::tool_success_text("call-1".into(), "read_log".into(), large_text()),
        ],
    )
    .unwrap()
}

/// Restores `session`'s checkpoint under `config`, moved there from the
/// large model it was taken on.
fn moved_to(session: &HarnessSession, config: HarnessConfig) -> Result<HarnessSession, String> {
    HarnessSession::restore_moved_from(
        config,
        session.checkpoint().unwrap(),
        0,
        Some(&automatic_compaction_config()),
    )
}

///
/// *Prepare*: A Session with a short exchange, checkpointed on a large model.
/// *Do*: Restore it on a 32,000-token model and move it there.
/// *Assert*: The move is accepted, because the context is below the new threshold.
///
#[test]
fn context_below_the_new_threshold_fits_a_smaller_model() {
    // Prepare
    let session = HarnessSession::create_with_history(
        automatic_compaction_config(),
        vec![
            Message::user_text("Hello."),
            crate::core::testing::assistant_text("Hi."),
        ],
    )
    .unwrap();

    // Do
    let result = moved_to(&session, small_model_config()).map(drop);

    // Assert
    assert_eq!(result, Ok(()));
}

///
/// *Prepare*: A Session whose only request is larger than a 32,000-token model's threshold.
/// *Do*: Restore it on that model and move it there.
/// *Assert*: The move is accepted, because the next Turn's compaction can drop that history.
///
#[test]
fn context_the_next_turn_can_compact_fits_a_smaller_model() {
    // Prepare
    let session = HarnessSession::create_with_history(
        automatic_compaction_config(),
        vec![
            Message::user_text(large_text()),
            crate::core::testing::assistant_text("Noted."),
        ],
    )
    .unwrap();

    // Do
    let result = moved_to(&session, small_model_config()).map(drop);

    // Assert
    assert_eq!(result, Ok(()));
}

///
/// *Prepare*: A Session ending in a tool result larger than a 32,000-token model's threshold.
/// *Do*: Restore it on that model and move it there.
/// *Assert*: The move is accepted, because the next Turn's compaction shows that result
/// reduced.
///
#[test]
fn a_tool_result_larger_than_a_smaller_model_fits_it() {
    // Prepare
    let session = session_ending_in_large_tool_result();

    // Do
    let result = moved_to(&session, small_model_config()).map(drop);

    // Assert
    assert_eq!(result, Ok(()));
}

/// A Session ending in a large tool result whose provider counted its
/// requests at eight times their estimate: on a 32,000-token model, the
/// compaction budgets that correction leaves are smaller than the system
/// prompt and tools.
fn session_measured_far_above_its_estimate() -> HarnessSession {
    let mut session = session_ending_in_large_tool_result();
    session.state.estimate_correction = crate::core::testing::correction(8.0);
    session
}

///
/// *Prepare*: A Session ending in a large tool result.
/// *Do*: Check a move onto a 32,000-token model, counting its requests at eight times
/// their estimate.
/// *Assert*: The check refuses, naming the new window, because that count leaves no
/// room for the compaction the next Turn would start. A move resets the correction, so
/// this guards a count the Session measured some other way.
///
#[test]
fn context_no_compaction_can_fit_is_refused() {
    // Prepare
    let session = session_measured_far_above_its_estimate();
    let state = &session.state;

    // Do
    let result = crate::core::validate_model_switch(
        &small_model_config(),
        &state.resolved_tools,
        state.context.messages(),
        crate::core::features::compaction::RequestMeasurements::of(state),
    );

    // Assert
    let error = result.expect_err("context no compaction can fit is refused");
    assert!(
        error.detail().starts_with(
            "the Session's context cannot move to a model with a 32000-token context window \
             and a compaction threshold of 16000: "
        ),
        "{error:?}"
    );
}

///
/// *Prepare*: A Session whose requests were counted at eight times their estimate.
/// *Do*: Restore it on a 32,000-token model and move it there.
/// *Assert*: The move is accepted and the correction starts over, as a reconfiguration
/// onto that model does, because the correction measured the old model.
///
#[test]
fn restoring_onto_a_smaller_model_measures_the_context_afresh() {
    // Prepare
    let session = session_measured_far_above_its_estimate();

    // Do
    let restored = moved_to(&session, small_model_config());

    // Assert
    let restored = restored.expect("the move is accepted");
    assert_eq!(
        restored.state.estimate_correction,
        crate::core::features::compaction::EstimateCorrection::NONE
    );
}

///
/// *Prepare*: A Session whose requests were counted at 1.5 times their estimate.
/// *Do*: Restore it on the same model and move it there.
/// *Assert*: The move is accepted and keeps the correction, because the model facts
/// it measured are unchanged.
///
#[test]
fn restoring_onto_the_same_model_keeps_the_correction() {
    // Prepare
    let mut session = session_ending_in_large_tool_result();
    session.state.estimate_correction = crate::core::testing::correction(1.5);

    // Do
    let restored = moved_to(&session, automatic_compaction_config());

    // Assert
    let restored = restored.expect("the move is accepted");
    assert_eq!(
        restored.state.estimate_correction,
        crate::core::testing::correction(1.5)
    );
}

///
/// *Prepare*: A Session with a short exchange, its requests counted at 30 times their
/// estimate, and a compaction it has started but not yet requested.
/// *Do*: Restore it on a model with a 4,000-token compaction threshold, moved there
/// from the large model.
/// *Assert*: The restore succeeds with the correction starting over, because the
/// pending compaction is fitted to the new model only after the move resets the
/// correction the old model measured. Restored without the move, the old correction
/// leaves the compaction no room.
///
#[test]
fn a_pending_compaction_is_fitted_after_the_move() {
    // Prepare
    let mut session = HarnessSession::create_with_history(
        automatic_compaction_config(),
        vec![
            Message::user_text("Hello."),
            crate::core::testing::assistant_text("Hi."),
        ],
    )
    .unwrap();
    session.state.estimate_correction = crate::core::testing::correction(30.0);
    let started = session.apply(input(
        1,
        HarnessCommand::Compact {
            extra_instructions: String::new(),
        },
    ));
    assert!(
        matches!(started, HarnessApplyResult::Accepted { .. }),
        "{started:?}"
    );
    let mut config = automatic_compaction_config();
    let facts = &mut config.settings.context.model_facts;
    facts.context_window = 4_001;
    facts.compaction_threshold = 4_000;
    facts.compaction_threshold_max = 4_000;

    // Do
    let moved = moved_to(&session, config.clone());
    let unmoved = HarnessSession::restore(config, session.checkpoint().unwrap(), 0);

    // Assert
    let moved = moved.expect("the moved restore succeeds");
    assert_eq!(
        moved.state.estimate_correction,
        crate::core::features::compaction::EstimateCorrection::NONE
    );
    assert!(
        unmoved.is_err(),
        "the unmoved restore keeps the old correction"
    );
}

///
/// *Prepare*: A Session whose requests were counted at eight times their estimate.
/// *Do*: Reconfigure its settings onto a 32,000-token model.
/// *Assert*: The command is accepted and the correction starts over, because it measured
/// the old model: the check sees the size the next Turn's check will see.
///
#[test]
fn reconfiguring_onto_a_smaller_model_measures_the_context_afresh() {
    // Prepare
    let mut session = session_measured_far_above_its_estimate();

    // Do
    let result = session.apply(input(
        1,
        HarnessCommand::Reconfigure {
            changes: vec![HarnessConfigurationChange::Settings {
                value: Box::new(small_model_config().settings),
            }],
        },
    ));

    // Assert
    assert!(
        matches!(result, HarnessApplyResult::Accepted { .. }),
        "{result:?}"
    );
    assert_eq!(
        session.state.estimate_correction,
        crate::core::features::compaction::EstimateCorrection::NONE
    );
}

/// A Session with a short exchange whose provider, on the large model,
/// reported a context of 100,000 tokens.
fn session_reported_large_on_the_old_model() -> HarnessSession {
    let mut session = HarnessSession::create_with_history(
        automatic_compaction_config(),
        vec![
            Message::user_text("Hello."),
            crate::core::testing::assistant_text("Hi."),
        ],
    )
    .unwrap();
    session.state.last_reported_context_tokens = Some(100_000);
    session
}

///
/// *Prepare*: A Session without compaction whose request is larger than a 32,000-token window.
/// *Do*: Restore it on that model, also without compaction, and move it there.
/// *Assert*: The move refuses, naming the new window, because nothing can shrink the context.
///
#[test]
fn context_larger_than_a_smaller_window_without_compaction_is_refused() {
    // Prepare
    let session = HarnessSession::create_with_history(
        crate::core::testing::config(),
        vec![
            Message::user_text(large_text()),
            crate::core::testing::assistant_text("Noted."),
        ],
    )
    .unwrap();
    let mut config = small_model_config();
    config.settings.context.compaction =
        crate::core::features::compaction::CompactionPolicy::Disabled;

    // Do
    let result = HarnessSession::restore_moved_from(
        config,
        session.checkpoint().unwrap(),
        0,
        Some(&crate::core::testing::config()),
    )
    .map(drop);

    // Assert
    let error = result.expect_err("context over the new window is refused");
    assert!(
        error.starts_with(
            "the Session's context cannot move to a model with a 32000-token context window \
             and compaction disabled: "
        ),
        "{error}"
    );
}

/// A Session whose answered user message holds two images, 16,384 tokens
/// at the fixed image cost.
fn session_with_two_answered_images() -> HarnessSession {
    HarnessSession::create_with_history(
        automatic_compaction_config(),
        vec![
            Message::user(vec![
                ContentBlock::text("Fix what these screenshots show."),
                ContentBlock::image("aW1hZ2U=", "image/png"),
                ContentBlock::image("aW1hZ2U=", "image/png"),
            ]),
            crate::core::testing::assistant_text("Done."),
        ],
    )
    .unwrap()
}

///
/// *Prepare*: A Session whose newest user message holds images larger than a
/// 32,000-token model's threshold.
/// *Do*: Restore it on that model, which takes images, and move it there.
/// *Assert*: The move refuses, naming the new window, because the next Turn's compaction
/// keeps that message whole and cannot fit it.
///
#[test]
fn images_a_smaller_image_model_cannot_hold_refuse_the_move() {
    // Prepare
    let session = session_with_two_answered_images();

    // Do
    let result = moved_to(&session, small_model_config()).map(drop);

    // Assert
    let error = result.expect_err("images over the new threshold are refused");
    assert!(
        error.starts_with(
            "the Session's context cannot move to a model with a 32000-token context window \
             and a compaction threshold of 16000: "
        ),
        "{error}"
    );
}

///
/// *Prepare*: A Session whose newest user message, sent after the model's last reply,
/// holds images larger than a 32,000-token model's threshold.
/// *Do*: Restore it on that model, which takes images, and move it there.
/// *Assert*: The move is accepted. The carry only takes messages from before the last
/// reply, so the next Turn's compaction shows that message reduced, its images as
/// placeholders, and warns that it did.
///
#[test]
fn images_the_model_has_not_answered_yet_do_not_refuse_the_move() {
    // Prepare
    let session = HarnessSession::create_with_history(
        automatic_compaction_config(),
        vec![
            Message::user_text("Hello."),
            crate::core::testing::assistant_text("Hi."),
            Message::user(vec![
                ContentBlock::text("Fix what these screenshots show."),
                ContentBlock::image("aW1hZ2U=", "image/png"),
                ContentBlock::image("aW1hZ2U=", "image/png"),
            ]),
        ],
    )
    .unwrap();

    // Do
    let result = moved_to(&session, small_model_config()).map(drop);

    // Assert
    assert_eq!(result, Ok(()));
}

///
/// *Prepare*: The same Session.
/// *Do*: Restore it on a 32,000-token model without image input and move it there.
/// *Assert*: The move is accepted, because that model reads each image as a placeholder.
///
#[test]
fn images_never_refuse_a_move_to_a_model_without_image_input() {
    // Prepare
    let session = session_with_two_answered_images();
    let mut config = small_model_config();
    config.settings.context.model_facts.input_modalities =
        vec![crate::core::config::InputModality::Text];

    // Do
    let result = moved_to(&session, config).map(drop);

    // Assert
    assert_eq!(result, Ok(()));
}

///
/// *Prepare*: A short exchange whose provider reported a 100,000-token context on the
/// large model.
/// *Do*: Restore it on a 32,000-token model and move it there.
/// *Assert*: The move forgets the reported size, which measured the old model, so the
/// next Turn does not compact a context far below the new threshold.
///
#[test]
fn restoring_onto_a_smaller_model_forgets_the_old_reported_size() {
    // Prepare
    let session = session_reported_large_on_the_old_model();

    // Do
    let restored = moved_to(&session, small_model_config());

    // Assert
    let restored = restored.expect("the move is accepted");
    assert_eq!(restored.state.last_reported_context_tokens, None);
    assert!(
        !crate::core::features::compaction::model_input_reaches_compaction_threshold(
            &restored.state
        )
        .unwrap()
    );
}

///
/// *Prepare*: A short exchange whose provider reported a 100,000-token context.
/// *Do*: Restore it on the same model and move it there.
/// *Assert*: The move keeps the reported size, because the model facts it measured are
/// unchanged.
///
#[test]
fn restoring_onto_the_same_model_keeps_the_reported_size() {
    // Prepare
    let session = session_reported_large_on_the_old_model();

    // Do
    let restored = moved_to(&session, automatic_compaction_config());

    // Assert
    let restored = restored.expect("the move is accepted");
    assert_eq!(restored.state.last_reported_context_tokens, Some(100_000));
}

///
/// *Prepare*: A short exchange whose provider reported a 100,000-token context and
/// whose next request a Runtime counted at 150,000 tokens, both on the large model.
/// *Do*: Reconfigure its settings onto a 32,000-token model.
/// *Assert*: The command is accepted and forgets both counts, which measured the old
/// model, so the next Turn does not compact a context far below the new threshold.
///
#[test]
fn reconfiguring_onto_a_smaller_model_forgets_the_old_counts() {
    // Prepare
    let mut session = session_reported_large_on_the_old_model();
    crate::core::features::compaction::record_exact_request_tokens(&mut session.state, 150_000);

    // Do
    let result = session.apply(input(
        1,
        HarnessCommand::Reconfigure {
            changes: vec![HarnessConfigurationChange::Settings {
                value: Box::new(small_model_config().settings),
            }],
        },
    ));

    // Assert
    assert!(
        matches!(result, HarnessApplyResult::Accepted { .. }),
        "{result:?}"
    );
    assert_eq!(session.state.last_reported_context_tokens, None);
    assert_eq!(session.state.exact_request_tokens, None);
    assert!(
        !crate::core::features::compaction::model_input_reaches_compaction_threshold(
            &session.state
        )
        .unwrap()
    );
}

fn context_message(text: &str) -> HarnessCommand {
    HarnessCommand::ContextMessage {
        content: vec![ContentBlock::text(text)],
    }
}

fn checkpoint_after_two_inputs(config: HarnessConfig) -> Checkpoint {
    let mut session = HarnessSession::create(config).unwrap();
    for input_id in 1..=2 {
        assert!(matches!(
            session.apply(input(input_id, context_message("background"))),
            HarnessApplyResult::Accepted { .. }
        ));
    }
    session.checkpoint().unwrap()
}

///
/// *Prepare*: A session accepted two inputs and produced a checkpoint the Runtime stored beside its own input cursor.
/// *Do*: Restore that checkpoint at the recorded cursor and send both a consumed ID and the next one.
/// *Assert*: The restored session reports the cursor, rejects what the Runtime already consumed, and continues the sequence.
///
#[test]
fn restore_resumes_the_input_sequence_the_runtime_recorded() {
    // Prepare
    let config = crate::core::testing::config();
    let checkpoint = checkpoint_after_two_inputs(config.clone());

    // Do
    let mut restored = HarnessSession::restore(config, checkpoint, 2).unwrap();
    let consumed = restored.apply(input(2, context_message("already accepted")));
    let next = restored.apply(input(3, context_message("resumed")));

    // Assert
    assert!(matches!(
        consumed,
        HarnessApplyResult::Rejected {
            rejection: HarnessCommandRejection::StaleInput {
                received_input_id: 2,
                last_accepted_input_id: 2,
            },
        }
    ));
    assert!(matches!(next, HarnessApplyResult::Accepted { .. }));
    assert_eq!(restored.inspect().last_input_id, 3);
}

///
/// *Prepare*: A checkpoint is restored at a cursor a previous resident session reached.
/// *Do*: Take the first turn of the resumed session.
/// *Assert*: The completion carries a full model-input replacement, because the checkpoint restores no delivery caches.
///
#[test]
fn a_resumed_session_still_opens_with_a_full_model_input_replacement() {
    // Prepare
    let config = crate::core::testing::config();
    let checkpoint = checkpoint_after_two_inputs(config.clone());
    let mut restored = HarnessSession::restore(config, checkpoint, 2).unwrap();

    // Do
    let result = restored.apply(input(
        3,
        HarnessCommand::UserMessage {
            turn_id: "turn-resumed".to_string(),
            content: vec![ContentBlock::text("carry on")],
            mode: UserMessageMode::Queue,
        },
    ));

    // Assert
    let HarnessApplyResult::Accepted { transition } = result else {
        panic!("expected the resumed input to be accepted, got {result:?}")
    };
    let HarnessNextAction::Actions { directives } = &transition.next else {
        panic!("expected the turn to dispatch a completion")
    };
    let [
        HarnessActionDirective::Dispatch {
            action: Action::Completion { model_input, .. },
        },
    ] = directives.as_slice()
    else {
        panic!("expected a single dispatched completion, got {directives:?}")
    };
    assert!(matches!(
        model_input.messages,
        ModelMessageUpdate::Replace { .. }
    ));
    assert!(matches!(
        model_input.tool_catalog,
        ModelToolCatalogUpdate::Replace { .. }
    ));
}

///
/// *Prepare*: A checkpoint and a resumed cursor one past the largest ID the wire can carry.
/// *Do*: Restore the checkpoint at that cursor.
/// *Assert*: The restore fails rather than seating a session whose next ID cannot round-trip through JSON.
///
#[test]
fn restore_rejects_a_resumed_input_id_outside_the_json_safe_range() {
    // Prepare
    let config = crate::core::testing::config();
    let checkpoint = checkpoint_after_two_inputs(config.clone());

    // Do
    let result = HarnessSession::restore(config, checkpoint, 9_007_199_254_740_992);

    // Assert
    let Err(error) = result else {
        panic!("expected the restore to reject a cursor outside the JSON safe range")
    };
    assert_eq!(error, "resumed input ID must be a JSON safe integer");
}

///
/// *Prepare*: A session resumed at the largest input ID the wire can carry.
/// *Do*: Send the input that would follow it.
/// *Assert*: The sequence reports itself exhausted instead of advancing past a JSON safe integer.
///
#[test]
fn a_session_resumed_at_the_json_safe_limit_reports_an_exhausted_sequence() {
    // Prepare
    let config = crate::core::testing::config();
    let checkpoint = checkpoint_after_two_inputs(config.clone());
    let mut restored = HarnessSession::restore(config, checkpoint, 9_007_199_254_740_991).unwrap();
    let mut overflowing = input(1, context_message("one too many"));
    overflowing.input_id = 9_007_199_254_740_992;

    // Do
    let result = restored.apply(overflowing);

    // Assert
    let HarnessApplyResult::Rejected {
        rejection: HarnessCommandRejection::InvalidCommand { error },
    } = result
    else {
        panic!("expected an invalid-command rejection, got {result:?}")
    };
    assert_eq!(error.message, "input ID sequence is exhausted");
}
