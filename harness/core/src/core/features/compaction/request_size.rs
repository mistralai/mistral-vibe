//! The size of the next agent request, as the compaction check sees it.
//!
//! Core estimates a request from its bytes and multiplies the estimate by the
//! correction `r` the provider's reports and refusals measure (see
//! [`EstimateCorrection`]). An exact count, when a Runtime gave one, replaces
//! both (see [`ExactRequestTokens`]).

use super::{
    CompactionBudgets, CompactionInputs, CompactionLimits, EstimateCorrection, ExactRequestTokens,
    RequestRevision,
};
use crate::core::config::HarnessSettings;
use crate::core::error::CoreError;
use crate::core::model_input_budget::estimate_model_input_tokens;
use crate::core::state::HarnessState;
#[cfg(test)]
use crate::core::state::{ActivePhase, TurnState};
use crate::core::step_protocol::ToolDefinition;
use crate::core::wire::message::{Message, StoredMessage};

/// What a compaction works with, given the session's settings, tool
/// catalog and estimate correction.
pub(crate) fn compaction_inputs<'a>(
    settings: &'a HarnessSettings,
    tools: &'a [ToolDefinition],
    correction: EstimateCorrection,
) -> CompactionInputs<'a> {
    CompactionInputs {
        tools,
        budgets: CompactionBudgets::from_settings(&settings.context, correction),
        prompts: &settings.prompts.compaction,
    }
}

pub(crate) fn model_input_reaches_compaction_threshold(
    state: &HarnessState,
) -> Result<bool, CoreError> {
    let Some(limits) = CompactionLimits::from_settings(&state.config.settings.context) else {
        return Ok(false);
    };
    Ok(corrected_model_input_tokens(state)? >= limits.threshold)
}

/// Records what the provider reported for the agent request the current
/// context made.
///
/// Call it before the reply joins the context: the correction compares the
/// reported input with the estimate of the request that was sent.
pub(crate) fn record_reply_size(
    state: &mut HarnessState,
    input_tokens: Option<u64>,
    context_tokens: Option<u64>,
) -> Result<(), CoreError> {
    if let Some(input_tokens) = input_tokens {
        let estimate = estimate_agent_request_tokens(state)?;
        state.estimate_correction = EstimateCorrection::measured(input_tokens, estimate);
    }
    state.rebuilt_since_reply = false;
    // A reply without usage leaves no reported size: a stale one from an
    // earlier reply would under-count everything added since.
    state.last_reported_context_tokens = context_tokens;
    Ok(())
}

/// Records that a compaction replaced the context.
///
/// The previous provider-reported size describes the replaced context. It is
/// cleared only once the history changes, so a failed or interrupted attempt
/// keeps the over-threshold signal for the next turn.
pub(crate) fn record_compacted(state: &mut HarnessState) {
    state.last_reported_context_tokens = None;
    state.rebuilt_since_reply = true;
}

/// Records `tokens`, an exact count of the agent request the current context
/// makes, for the compaction check to use in place of its estimate.
#[cfg_attr(
    not(test),
    allow(
        dead_code,
        reason = "no Runtime counts requests yet; see ExactRequestTokens"
    )
)]
pub(crate) fn record_exact_request_tokens(state: &mut HarnessState, tokens: u64) {
    state.exact_request_tokens = Some(ExactRequestTokens::new(tokens, request_revision(state)));
}

/// Which agent request the current context and tool catalog make, unless
/// either changed since its revision was committed.
fn request_revision(state: &HarnessState) -> Option<RequestRevision> {
    Some(RequestRevision {
        context: state.context.committed_revision()?,
        tool_catalog: state.tool_catalog.committed_revision()?,
    })
}

/// The size of the next agent request as the check sees it.
pub(crate) fn corrected_model_input_tokens(state: &HarnessState) -> Result<u64, CoreError> {
    corrected_request_tokens(
        &state.config.settings,
        state.resolved_tools.top_level_tools(),
        state.context.messages(),
        RequestMeasurements::of(state),
    )
}

/// What the session measured about the size of its requests.
#[derive(Clone, Copy, Debug)]
pub(crate) struct RequestMeasurements {
    /// The provider's reported context size after the last reply.
    pub last_reported: Option<u64>,
    pub correction: EstimateCorrection,
    pub exact: Option<ExactRequestTokens>,
    /// A compaction rebuilt the context since the last reply, so a refusal of
    /// the next request fails the turn instead of compacting again.
    pub rebuilt_since_reply: bool,
}

impl RequestMeasurements {
    /// What `state` measured. An exact count is kept only while it describes
    /// the request the current context and tool catalog make.
    pub(crate) fn of(state: &HarnessState) -> Self {
        let revision = request_revision(state);
        Self {
            last_reported: state.last_reported_context_tokens,
            correction: state.estimate_correction,
            exact: state
                .exact_request_tokens
                .filter(|exact| exact.describes(revision)),
            rebuilt_since_reply: state.rebuilt_since_reply,
        }
    }
}

/// The size of the agent request `messages` and `tools` make, as the check
/// sees it.
///
/// An exact count of this request, when a Runtime gave one, is the size.
/// Otherwise the byte estimate, multiplied by the session's correction `r`, is
/// one view. The provider's last reported size plus the corrected estimate of
/// the messages added since the reply it reported on is the other. The check
/// trusts whichever is larger.
pub(crate) fn corrected_request_tokens(
    settings: &HarnessSettings,
    tools: &[ToolDefinition],
    messages: &[StoredMessage],
    measurements: RequestMeasurements,
) -> Result<u64, CoreError> {
    if let Some(exact) = measurements.exact {
        return Ok(exact.tokens);
    }
    let image_delivery = settings.context.effective_image_delivery();
    let image_cost = settings.context.model_facts.image_cost;
    let estimate = estimate_request_tokens(settings, tools, messages)?;
    let r = measurements.correction;
    let whole = r.apply(estimate);
    let Some(reported) = measurements.last_reported else {
        return Ok(whole);
    };
    let after_last_reply = messages
        .iter()
        .rposition(|message| matches!(message.message, Message::Assistant { .. }))
        .map_or(messages, |index| &messages[index + 1..]);
    let added = if after_last_reply.is_empty() {
        0
    } else {
        r.apply(estimate_model_input_tokens(
            after_last_reply,
            &[],
            image_delivery.agent,
            image_cost,
        )?)
    };
    Ok(whole.max(reported.saturating_add(added)))
}

/// The uncorrected estimate of the agent request the current context makes,
/// tools included.
pub(crate) fn estimate_agent_request_tokens(state: &HarnessState) -> Result<u64, CoreError> {
    estimate_request_tokens(
        &state.config.settings,
        state.resolved_tools.top_level_tools(),
        state.context.messages(),
    )
}

fn estimate_request_tokens(
    settings: &HarnessSettings,
    tools: &[ToolDefinition],
    messages: &[StoredMessage],
) -> Result<u64, CoreError> {
    estimate_model_input_tokens(
        messages,
        tools,
        settings.context.effective_image_delivery().agent,
        settings.context.model_facts.image_cost,
    )
}

/// The estimate of the pending summary request, as a refusal of it reports.
#[cfg(test)]
pub(crate) fn estimate_summary_request_tokens(state: &HarnessState) -> Result<u64, CoreError> {
    let invalid_state = || CoreError::invalid_state(state.command_state());
    let pending = match &state.turn {
        TurnState::Compacting { pending } => pending,
        TurnState::Active(active) => match &active.phase {
            ActivePhase::AwaitingCompaction { pending } => pending,
            _ => return Err(invalid_state()),
        },
        _ => return Err(invalid_state()),
    };
    let tools = state.resolved_tools.top_level_tools();
    let inputs = compaction_inputs(&state.config.settings, tools, state.estimate_correction);
    pending
        .projection()
        .request_estimate(tools, inputs.budgets.request)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::testing::*;

    #[test]
    fn an_accepted_reply_measures_the_estimate_correction() {
        let state = started_with(with_threshold(1_000_000));
        let estimate = estimate_agent_request_tokens(&state).unwrap();

        let state = reply_with_input_tokens(state, estimate * 2);
        assert_eq!(state.estimate_correction, correction(2.0));
        assert_eq!(state.last_reported_context_tokens, Some(estimate * 2 + 7));

        // A reply reported under the estimate measures no correction.
        let state = advance(
            state,
            user_message_for("turn-2", "again", UserMessageMode::Queue),
        )
        .unwrap()
        .state;
        let state = reply_with_input_tokens(state, 1);
        assert_eq!(state.estimate_correction, EstimateCorrection::NONE);
    }

    #[test]
    fn the_check_multiplies_the_estimate_by_the_correction() {
        let mut state = started();
        let estimate = estimate_agent_request_tokens(&state).unwrap();
        set_threshold(&mut state.config.settings, estimate + 1);

        assert_eq!(corrected_model_input_tokens(&state).unwrap(), estimate);
        assert!(!model_input_reaches_compaction_threshold(&state).unwrap());

        state.estimate_correction = correction(1.33);
        let corrected = (estimate as f64 * 1.33).ceil() as u64;
        assert_eq!(corrected_model_input_tokens(&state).unwrap(), corrected);
        assert!(model_input_reaches_compaction_threshold(&state).unwrap());
    }

    #[test]
    fn the_check_adds_the_corrected_new_items_to_the_reported_size() {
        let state = started_with(with_threshold(1_000_000));
        let estimate = estimate_agent_request_tokens(&state).unwrap();
        let state = reply_with_input_tokens(state, estimate);
        let mut state = advance(
            state,
            user_message_for("turn-2", "a newer question", UserMessageMode::Queue),
        )
        .unwrap()
        .state;
        let messages = state.context.messages();
        let last_reply = messages
            .iter()
            .rposition(|message| matches!(message.message, Message::Assistant { .. }))
            .unwrap();
        let added = estimate_model_input_tokens(
            &messages[last_reply + 1..],
            &[],
            ImageDeliveryMode::Native,
            state.config.settings.context.model_facts.image_cost,
        )
        .unwrap();
        assert!(added > 0);

        // The reported size is far above the estimate of the whole request.
        state.last_reported_context_tokens = Some(500_000);
        assert_eq!(
            corrected_model_input_tokens(&state).unwrap(),
            500_000 + added
        );

        let r = correction(1.33);
        state.estimate_correction = r;
        assert_eq!(
            corrected_model_input_tokens(&state).unwrap(),
            500_000 + r.apply(added)
        );

        // Without a reported size only the corrected whole request counts.
        state.last_reported_context_tokens = None;
        assert_eq!(
            corrected_model_input_tokens(&state).unwrap(),
            r.apply(estimate_agent_request_tokens(&state).unwrap())
        );
    }

    #[test]
    fn an_exact_request_count_replaces_the_estimate_while_it_describes_the_context() {
        let mut state = started_with(with_threshold(1_000_000));
        state.estimate_correction = correction(2.0);
        state.last_reported_context_tokens = Some(500_000);

        // The count wins over both views, even when it is the smaller one.
        record_exact_request_tokens(&mut state, 1_234);
        assert_eq!(corrected_model_input_tokens(&state).unwrap(), 1_234);

        // Once the context changes the count is stale and the estimate counts.
        let state = reply_with_input_tokens(state, 1);
        let mut without_count = state.clone();
        without_count.exact_request_tokens = None;
        assert_eq!(
            corrected_model_input_tokens(&state).unwrap(),
            corrected_model_input_tokens(&without_count).unwrap()
        );
        assert_ne!(corrected_model_input_tokens(&state).unwrap(), 1_234);
    }

    #[test]
    fn an_exact_request_count_goes_stale_when_the_context_changes_at_the_same_size() {
        let mut state = started_with(with_threshold(1_000_000));
        let estimate = estimate_agent_request_tokens(&state).unwrap();
        let message_count = state.context.message_count();
        record_exact_request_tokens(&mut state, 1_234);
        assert_eq!(corrected_model_input_tokens(&state).unwrap(), 1_234);

        // System instructions of the same length rebuild the system prompt
        // with other text at the same size and message count.
        let mut state = reconfigure(
            state,
            HarnessConfigUpdate {
                system_instructions: Some("SYSTEM".to_string()),
                settings: None,
                capabilities: None,
                skill_catalog_fingerprint: None,
                plugins: None,
            },
        )
        .unwrap();
        assert_eq!(state.context.message_count(), message_count);
        assert_eq!(estimate_agent_request_tokens(&state).unwrap(), estimate);
        assert_eq!(corrected_model_input_tokens(&state).unwrap(), estimate);
        state.commit_model_input_changes().unwrap();
        assert_eq!(corrected_model_input_tokens(&state).unwrap(), estimate);
    }

    #[test]
    fn a_change_of_model_facts_resets_the_estimate_correction() {
        let reconfigured = |state: HarnessState, settings: HarnessSettings| {
            reconfigure(
                state,
                HarnessConfigUpdate {
                    system_instructions: None,
                    settings: Some(settings),
                    capabilities: None,
                    skill_catalog_fingerprint: None,
                    plugins: None,
                },
            )
            .unwrap()
        };
        let mut state = started_with(with_threshold(100_000));
        state.estimate_correction = correction(1.5);

        // A change unrelated to the model keeps the correction.
        let mut settings = state.config.settings.clone();
        settings.turn.max_iterations = Some(3);
        let mut state = reconfigured(state, settings);
        assert_eq!(state.estimate_correction, correction(1.5));

        // A new threshold means new model facts.
        let mut settings = state.config.settings.clone();
        settings.context.model_facts.compaction_threshold = 50_000;
        state = reconfigured(state, settings);
        assert_eq!(state.estimate_correction, EstimateCorrection::NONE);
    }
}
