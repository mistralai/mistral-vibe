//! Context compaction: the check, the summary-request phases and the rebuild.
//! Design and diagrams: `docs/compaction.html`.

mod context;
mod execution;
mod orchestration;
mod overflow;
mod projection;
mod prompts;
mod request_size;
mod standalone;
mod transcript;

use serde::{Deserialize, Serialize};

use crate::core::config::{ContextSettings, ImageCostRule, ImageDeliveryMode};
use crate::core::step_protocol::ToolDefinition;

/// The sizes automatic compaction works with, all read from the model facts
/// the host fills in.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct CompactionLimits {
    /// Compaction starts when the next agent request reaches this size.
    pub threshold: u64,
    /// The most a summary request may hold: the largest threshold the window
    /// allows.
    pub summary_budget: u64,
    /// The model's context window.
    pub context_window: u64,
}

impl CompactionLimits {
    /// The limits automatic compaction uses, or `None` when it is disabled.
    pub(crate) fn from_settings(settings: &ContextSettings) -> Option<Self> {
        let threshold = settings.compaction_threshold()?;
        let facts = &settings.model_facts;
        Some(Self {
            threshold,
            summary_budget: facts.compaction_threshold_max,
            context_window: facts.context_window,
        })
    }

    /// Whether the facts the estimate correction was measured against changed
    /// between two configurations, so the correction no longer applies.
    pub(crate) fn model_facts_changed(current: &ContextSettings, next: &ContextSettings) -> bool {
        current.model_facts != next.model_facts
            || current.effective_image_delivery() != next.effective_image_delivery()
    }
}

pub(in crate::core) use context::{
    Rebuilt, Reduction, SummaryAcceptance, SummaryCut, SummaryCutCause, SummaryRejection,
    accept_cut_off_summary, accept_summary, fit_latest_user_message_to_budget,
    latest_preserved_user_index, minimum_replacement_fits, prompt_message, validate_prompt,
};
pub(in crate::core) use execution::{
    CompactionAttemptResolution, CompactionRequest, CompactionResolution,
    CompactionStartResolution, CompactionTooLarge, PendingCompaction, fail_compaction_or_fall_back,
    finish_compaction, plan_compaction, refit_refused_compaction, start_compaction,
};
pub(in crate::core) use orchestration::{
    AgentRefusal, correct_refused_agent_request, finish_summary_request,
    next_turn_compaction_unfit, start_automatic_compaction, start_manual_compaction,
};
pub(in crate::core) use overflow::{
    EstimateCorrection, ExactRequestTokens, RequestRevision, is_context_overflow,
};
pub(in crate::core) use projection::{
    CompactionPhase, CompactionProjection, CompactionProjectionFit, CompactionRequestKind,
};
pub(crate) use prompts::Prompts;
pub(in crate::core) use request_size::{
    RequestMeasurements, corrected_request_tokens, model_input_reaches_compaction_threshold,
    record_reply_size,
};
#[cfg(test)]
pub(in crate::core) use request_size::{
    estimate_agent_request_tokens, estimate_summary_request_tokens, record_exact_request_tokens,
};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct CompactionBudget {
    pub token_threshold: Option<u64>,
    pub image_delivery: ImageDeliveryMode,
    pub image_cost: ImageCostRule,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct CompactionBudgets {
    /// The most a summary request may hold, by estimate.
    pub request: CompactionBudget,
    /// The most the rebuilt history may hold, by estimate.
    pub replacement: CompactionBudget,
}

impl CompactionBudgets {
    /// The budgets in uncorrected estimates: a request whose estimate stays
    /// under them stays under the limits once multiplied by the correction
    /// `r`. Without automatic compaction they are unbounded.
    pub(crate) fn from_settings(
        settings: &ContextSettings,
        correction: EstimateCorrection,
    ) -> Self {
        let limits = CompactionLimits::from_settings(settings);
        let image_delivery = settings.effective_image_delivery();
        let image_cost = settings.model_facts.image_cost;
        Self {
            request: CompactionBudget {
                token_threshold: limits
                    .map(|limits| correction.uncorrected_limit(limits.summary_budget)),
                image_delivery: image_delivery.compaction,
                image_cost,
            },
            replacement: CompactionBudget {
                token_threshold: limits
                    .map(|limits| correction.uncorrected_limit(limits.threshold)),
                image_delivery: image_delivery.agent,
                image_cost,
            },
        }
    }
}

/// What every summary request and rebuild of a compaction works with.
#[derive(Clone, Copy, Debug)]
pub(crate) struct CompactionInputs<'a> {
    /// The agent's tool catalog: an in-context request declares it.
    pub tools: &'a [ToolDefinition],
    pub budgets: CompactionBudgets,
    pub prompts: &'a Prompts,
}

/// Whether Core compacts on its own. The threshold is a model fact, so it lives
/// in `ModelFacts`, not here.
#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(tag = "mode", rename_all = "snake_case", deny_unknown_fields)]
pub(crate) enum CompactionPolicy {
    Disabled,
    Automatic,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum CompactionTrigger {
    Manual,
    Automatic,
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::testing::config;

    fn context(compaction: CompactionPolicy) -> ContextSettings {
        let mut context = config().settings.context;
        context.compaction = compaction;
        context
    }

    #[test]
    fn the_summary_request_gets_the_max_threshold_and_the_rebuild_the_threshold() {
        let settings = context(CompactionPolicy::Automatic);
        let facts = &settings.model_facts;
        assert!(facts.compaction_threshold < facts.compaction_threshold_max);

        let budgets = CompactionBudgets::from_settings(&settings, EstimateCorrection::NONE);

        assert_eq!(
            budgets.request.token_threshold,
            Some(facts.compaction_threshold_max)
        );
        assert_eq!(
            budgets.replacement.token_threshold,
            Some(facts.compaction_threshold)
        );
    }

    #[test]
    fn both_budgets_are_in_uncorrected_estimates() {
        let settings = context(CompactionPolicy::Automatic);
        let facts = &settings.model_facts;
        let correction = EstimateCorrection::restore(1.5).unwrap();

        let budgets = CompactionBudgets::from_settings(&settings, correction);

        assert_eq!(
            budgets.request.token_threshold,
            Some(correction.uncorrected_limit(facts.compaction_threshold_max))
        );
        assert_eq!(
            budgets.replacement.token_threshold,
            Some(correction.uncorrected_limit(facts.compaction_threshold))
        );
        assert!(budgets.request.token_threshold < Some(facts.compaction_threshold_max));
    }

    #[test]
    fn both_budgets_are_unbounded_without_automatic_compaction() {
        let budgets = CompactionBudgets::from_settings(
            &context(CompactionPolicy::Disabled),
            EstimateCorrection::NONE,
        );

        assert_eq!(budgets.request.token_threshold, None);
        assert_eq!(budgets.replacement.token_threshold, None);
    }
}
