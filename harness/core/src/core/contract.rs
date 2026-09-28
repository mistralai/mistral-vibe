//! Tests for the properties that define the Core, as distinct from tests for
//! what it computes.
//!
//! `docs/README.md` and `spec/README.md` describe a Core that is pure, total,
//! and deliberately ignorant of transport. Ordinary behaviour tests do not
//! catch a regression in any of those: a Core that reads the wall clock, or
//! that starts filling in context payloads the Runtime owns, still produces the
//! right answer once. These tests fail when the seam moves.

use super::testing::*;
use crate::core::step_protocol::ModelMessageUpdate;

/// The Core never fills the LLM context payload.
///
/// `HarnessSession` rewrites this field before the action crosses the Runtime
/// boundary. It stores the cursor, while `ModelContext` owns the staleness
/// rule. If the Core ever populated it, the Core would be deciding context
/// transport, the exact conflation `spec/README.md` forbids and the reason the
/// Step Protocol sends incremental commands instead of whole state.
#[test]
fn core_emits_completion_actions_without_an_llm_context_payload() {
    let mut state = initial_state(config()).unwrap();

    let actions = advance_in_place(
        &mut state,
        user_message("work", UserMessageMode::Queue),
        test_determinism(),
    )
    .unwrap()
    .effects;

    let message_updates = actions
        .iter()
        .filter_map(|action| match action {
            Action::Completion { model_input, .. } => Some(&model_input.messages),
            Action::RuntimeBuiltinTool { .. }
            | Action::ProvidedTool { .. }
            | Action::Hook { .. }
            | Action::Filesystem { .. } => None,
        })
        .collect::<Vec<_>>();
    assert!(
        !message_updates.is_empty(),
        "expected the turn to dispatch a completion"
    );
    for update in message_updates {
        assert_eq!(
            update,
            &ModelMessageUpdate::Append {
                base_revision: 0,
                revision: 0,
                messages: Vec::new(),
            },
            "the Core must leave the context payload for HarnessSession to fill"
        );
    }
}

/// Reducing is a function of `(state, command, determinism)` and nothing else.
///
/// The Core runs inside Temporal workflow replay, so a hidden read of the clock
/// or of process entropy would surface as a nondeterministic workflow rather
/// than as a failing behaviour test. Reducing the same input twice from equal
/// states must give equal states and equal outcomes.
#[test]
fn reducing_the_same_command_twice_from_equal_states_agrees() {
    let first = started();
    let mut second = first.clone();
    let mut first = first;
    let action_id = awaiting_action_id(&first);

    let result = |action_id: &str| HarnessCommand::CompletionSucceeded {
        action_id: action_id.to_string(),
        result: CompletionResult {
            parts: vec![CompletionResultPart::Content(ContentBlock::text(
                "same answer",
            ))],
            finish_reason: CompletionFinishReason::Stop,
            usage: None,
        },
    };

    let first_outcome =
        advance_in_place(&mut first, result(&action_id), test_determinism()).unwrap();
    let second_outcome =
        advance_in_place(&mut second, result(&action_id), test_determinism()).unwrap();

    assert_eq!(first_outcome, second_outcome);
    assert_eq!(first, second);
}

/// A turn that reaches a terminal state stops emitting actions.
///
/// The Runtime drives the loop by executing what the Core hands it, so a Core
/// that returned an action alongside a terminal turn would leave the Runtime
/// running work for a turn that is already reported finished.
#[test]
fn a_terminal_turn_emits_no_further_actions() {
    let completed = advance_llm(started(), assistant_text("final answer"));

    assert!(matches!(
        turn_outcome(&completed.state),
        Some(TurnOutcome::Completed { .. })
    ));
    assert!(
        completed.effects.is_empty(),
        "a completed turn dispatched {:?}",
        completed.effects
    );
}
