//! The compaction steps that read and update the session state: starting a
//! compaction, finishing a summary request, and correcting the estimate when
//! the provider refuses a request as too long.
//!
//! `turn.rs` routes completion events here and applies the resolution each
//! step returns to the turn.

use super::request_size::{
    RequestMeasurements, compaction_inputs, corrected_request_tokens,
    estimate_agent_request_tokens, record_compacted,
};
use super::{
    CompactionAttemptResolution, CompactionLimits, CompactionRequest, CompactionStartResolution,
    CompactionTooLarge, EstimateCorrection, PendingCompaction, fail_compaction_or_fall_back,
    finish_compaction, is_context_overflow, plan_compaction, refit_refused_compaction,
    start_compaction,
};
use crate::core::config::HarnessConfig;
use crate::core::error::CoreError;
use crate::core::prompt::build_system_prompt;
use crate::core::state::HarnessState;
use crate::core::step_protocol::{CandidateDiscardCause, Observation, Outcome, WarningCode};
use crate::core::tools::resolved::ResolvedTools;
use crate::core::wire::completion::CompletionCandidate;
use crate::core::wire::message::{Message, StoredMessage};
use crate::core::wire::tool::ProtocolError;

pub(crate) fn start_automatic_compaction(
    state: &mut HarnessState,
    turn_id: &str,
    iterations: u32,
) -> Result<CompactionStartResolution, CoreError> {
    start(state, CompactionRequest::automatic(turn_id, iterations))
}

pub(crate) fn start_manual_compaction(
    state: &mut HarnessState,
    extra_instructions: &str,
) -> Result<CompactionStartResolution, CoreError> {
    start(state, CompactionRequest::manual(extra_instructions))
}

fn start(
    state: &mut HarnessState,
    request: CompactionRequest<'_>,
) -> Result<CompactionStartResolution, CoreError> {
    let task_id = state.config.task_id.clone();
    let generated_system = state.generated_system_message();
    start_compaction(
        &mut state.context,
        &mut state.compaction_count,
        generated_system,
        &task_id,
        request,
        compaction_inputs(
            &state.config.settings,
            state.resolved_tools.top_level_tools(),
            state.estimate_correction,
        ),
    )
}

/// Why the next turn could not carry `messages` under `config`, if it could
/// not.
///
/// Context below the compaction threshold needs nothing. Context at or above
/// it is carried by the automatic compaction the next turn starts, so this
/// returns why that compaction could not start. `measurements` are the ones
/// the next turn's check will use, so this sees the size that check sees.
pub(crate) fn next_turn_compaction_unfit(
    config: &HarnessConfig,
    resolved_tools: &ResolvedTools,
    messages: &[StoredMessage],
    measurements: RequestMeasurements,
) -> Result<Option<CompactionTooLarge>, CoreError> {
    let settings = &config.settings;
    let Some(limits) = CompactionLimits::from_settings(&settings.context) else {
        return Ok(None);
    };
    let tools = resolved_tools.top_level_tools();
    if messages.is_empty()
        || corrected_request_tokens(settings, tools, messages, measurements)? < limits.threshold
    {
        return Ok(None);
    }
    let generated_system = StoredMessage::generated_system(Message::system_text(
        build_system_prompt(config, resolved_tools.tool_group_inventory_prompt_section()),
    ));
    Ok(plan_compaction(
        messages,
        generated_system,
        "",
        compaction_inputs(settings, tools, measurements.correction),
    )?
    .err())
}

/// Finishes the pending summary request with the provider's reply or error.
///
/// Returns what the step observed and the attempt's resolution, which the
/// turn applies.
pub(crate) fn finish_summary_request(
    state: &mut HarnessState,
    pending: PendingCompaction,
    reply: Result<CompletionCandidate, ProtocolError>,
) -> Result<(Outcome, CompactionAttemptResolution), CoreError> {
    let (outcome, resolution) = match reply {
        Ok(candidate) => {
            let resolution = finish_compaction(
                &mut state.context,
                pending,
                candidate,
                compaction_inputs(
                    &state.config.settings,
                    state.resolved_tools.top_level_tools(),
                    state.estimate_correction,
                ),
            )?;
            (Outcome::quiet(), resolution)
        }
        Err(error) if is_context_overflow(&error) => {
            refit_refused_summary_request(state, pending, error)?
        }
        Err(error) => {
            let resolution = fail_compaction_or_fall_back(
                &mut state.context,
                pending,
                compaction_inputs(
                    &state.config.settings,
                    state.resolved_tools.top_level_tools(),
                    state.estimate_correction,
                ),
                error,
            )?;
            (Outcome::quiet(), resolution)
        }
    };
    if matches!(resolution, CompactionAttemptResolution::Compacted(_)) {
        record_compacted(state);
    }
    Ok((outcome, resolution))
}

/// The provider refused a summary request as too long.
///
/// As for an agent request, the refusal is a measurement: the estimate
/// correction grows, and the same phase's request is refitted under the
/// budget the grown correction leaves and under the size the refused request
/// was counted at. An in-context request at its minimal size goes to Standalone;
/// a standalone or cut-off retry request refused at its minimal size fails
/// the compaction. Without automatic compaction there is no budget to refit
/// to, so the refusal is an ordinary failure.
fn refit_refused_summary_request(
    state: &mut HarnessState,
    pending: PendingCompaction,
    error: ProtocolError,
) -> Result<(Outcome, CompactionAttemptResolution), CoreError> {
    let settings = &state.config.settings;
    let tools = state.resolved_tools.top_level_tools();
    let inputs = compaction_inputs(settings, tools, state.estimate_correction);
    let Some(limits) = CompactionLimits::from_settings(&settings.context) else {
        let resolution = fail_compaction_or_fall_back(&mut state.context, pending, inputs, error)?;
        return Ok((Outcome::quiet(), resolution));
    };
    let refused_estimate = pending
        .projection()
        .request_estimate(tools, inputs.budgets.request)?;
    let correction = state
        .estimate_correction
        .after_refusal(limits.context_window, refused_estimate);
    state.estimate_correction = correction;
    let mut outcome = Outcome::quiet();
    outcome.observe(request_too_long_warning(
        pending
            .in_turn_identity()
            .map(|(turn_id, _)| turn_id.to_string()),
        refused_estimate,
        correction,
        limits.context_window,
    ));
    let refit_limit = EstimateCorrection::refit_limit(refused_estimate);
    let resolution = refit_refused_compaction(
        &mut state.context,
        pending,
        refit_limit,
        compaction_inputs(settings, tools, correction),
        error,
    )?;
    Ok((outcome, resolution))
}

/// What the turn does after the provider refused its agent request as too
/// long. Each carries what the step observed.
pub(crate) enum AgentRefusal {
    /// The request fails with `error`.
    Fail {
        outcome: Outcome,
        error: ProtocolError,
    },
    /// The request is discarded and the turn compacts.
    Compact {
        outcome: Outcome,
        start: CompactionStartResolution,
    },
}

/// The provider refused the agent request as too long.
///
/// The refusal is a measurement: the request was at least the context window
/// in size, so the estimate correction grows (see [`EstimateCorrection`]) and
/// the check that sent the request runs again, which now finds it over the
/// threshold and compacts. One case ends the turn with a retryable error
/// instead: the refused request was the first after a compaction rebuilt the
/// history, so compacting again would loop. Without automatic compaction the
/// refusal is an ordinary failure.
pub(crate) fn correct_refused_agent_request(
    state: &mut HarnessState,
    action_id: &str,
    error: ProtocolError,
) -> Result<AgentRefusal, CoreError> {
    let Some(limits) = CompactionLimits::from_settings(&state.config.settings.context) else {
        return Ok(AgentRefusal::Fail {
            outcome: Outcome::quiet(),
            error,
        });
    };
    let refused_estimate = estimate_agent_request_tokens(state)?;
    let correction = state
        .estimate_correction
        .after_refusal(limits.context_window, refused_estimate);
    state.estimate_correction = correction;
    let active = state.require_active()?;
    let turn_id = active.turn_id.clone();
    let iterations = active.iterations;
    let mut outcome = Outcome::quiet();
    outcome.observe(request_too_long_warning(
        Some(turn_id.clone()),
        refused_estimate,
        correction,
        limits.context_window,
    ));
    // The corrected refused request is at least the window, which is above
    // the threshold, so it always reaches the threshold: the one case that
    // cannot compact is a context already rebuilt since the last reply.
    if std::mem::take(&mut state.rebuilt_since_reply) {
        return Ok(AgentRefusal::Fail {
            outcome,
            error: refusal_after_compaction_error(error),
        });
    }
    outcome.observe(Observation::AgentCompletionCandidateDiscarded {
        turn_id: turn_id.clone(),
        action_id: action_id.to_string(),
        cause: CandidateDiscardCause::Retry,
    });
    let start = start_automatic_compaction(state, &turn_id, iterations)?;
    Ok(AgentRefusal::Compact { outcome, start })
}

/// The turn's failure for a refusal Core cannot compact away: the context was
/// rebuilt since the last reply, and compacting again in the same turn would
/// loop. It keeps the provider's code and is retryable: the next request is
/// counted at the grown correction.
fn refusal_after_compaction_error(error: ProtocolError) -> ProtocolError {
    ProtocolError {
        code: error.code,
        message: "The provider refused the request as too long even after compaction. Check \
                  the model's context window."
            .to_string(),
        retryable: true,
        details: serde_json::json!({
            "reason": "after_compaction",
            "provider_message": error.message,
            "provider_details": error.details,
        }),
    }
}

fn request_too_long_warning(
    turn_id: Option<String>,
    refused_estimate: u64,
    correction: EstimateCorrection,
    context_window: u64,
) -> Observation {
    Observation::Warning {
        turn_id,
        code: WarningCode::RequestTooLong,
        message: format!(
            "The model refused a request as too long. Requests are now counted as {:.2} times \
             their estimated size.",
            correction.factor()
        ),
        details: serde_json::json!({
            "refused_estimate": refused_estimate,
            "estimate_correction": correction.factor(),
            "context_window": context_window,
        }),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::testing::*;

    fn refuse_as_too_long(state: HarnessState) -> Step {
        let action_id = awaiting_action_id(&state);
        advance(
            state,
            HarnessCommand::CompletionFailed {
                action_id,
                error: ProtocolError {
                    code: "context_overflow".to_string(),
                    message: "prompt is too long".to_string(),
                    retryable: false,
                    details: Value::Null,
                },
            },
        )
        .unwrap()
    }

    #[test]
    fn a_refused_request_with_nothing_to_compact_away_fails_the_turn() {
        // The refused request was at least the window, which is above the
        // threshold: however small its estimate, the corrected size reaches
        // it. Here the request is only the user's message, which the rebuilt
        // context keeps, so compaction cannot make it fit.
        let mut state = started();
        let estimate = estimate_agent_request_tokens(&state).unwrap();
        set_threshold(&mut state.config.settings, estimate * 2);
        let window = state.config.settings.context.model_facts.context_window;

        let refused = refuse_as_too_long(state);

        assert_eq!(
            refused.state.estimate_correction,
            correction(window as f64 / estimate as f64)
        );
        let Some(TurnOutcome::Failed { error }) = turn_outcome(&refused.state) else {
            panic!("the turn fails");
        };
        assert_eq!(error.code, "compaction_replacement_too_large");
    }

    #[test]
    fn a_refused_request_over_the_corrected_threshold_compacts() {
        let request = "a long request ".repeat(4_000);
        let mut state = advance(
            initial_state(config()).unwrap(),
            user_message(&request, UserMessageMode::Queue),
        )
        .unwrap()
        .state;
        let estimate = estimate_agent_request_tokens(&state).unwrap();
        set_threshold(&mut state.config.settings, estimate + 1);
        // A window just over the request: the growth floor decides.
        state.config.settings.context.model_facts.context_window = estimate + 2;

        let refused = refuse_as_too_long(state);

        assert_eq!(refused.state.estimate_correction, correction(4.0 / 3.0));
        assert!(matches!(
            refused.state.active().map(|active| &active.phase),
            Some(ActivePhase::AwaitingCompaction { .. })
        ));
    }
}
