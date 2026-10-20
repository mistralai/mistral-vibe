mod context;
mod execution;
mod projection;
mod standalone;

use serde::{Deserialize, Serialize};

use crate::core::config::{ImageDeliveryMode, ImageTokenCost};
use crate::core::error::CoreError;

pub(in crate::core) use context::{
    SummaryAcceptance, SummaryRejection, accept_summary, fit_latest_user_message_to_budget,
    latest_preserved_user_index, minimum_replacement_fits, prompt_message, validate_prompt,
};
pub(in crate::core) use execution::{
    CompactionAttemptResolution, CompactionRequest, CompactionResolution,
    CompactionStartResolution, PendingCompaction, fail_compaction, fail_compaction_or_fall_back,
    finish_compaction, start_compaction,
};
pub(in crate::core) use projection::{
    CompactionProjection, CompactionProjectionFit, CompactionRequestKind, NewestToolExchange,
};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct CompactionBudget {
    pub token_threshold: Option<u64>,
    pub image_delivery: ImageDeliveryMode,
    pub image_cost: ImageTokenCost,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct CompactionBudgets {
    pub request: CompactionBudget,
    pub replacement: CompactionBudget,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(tag = "mode", rename_all = "snake_case", deny_unknown_fields)]
pub(crate) enum CompactionPolicy {
    Disabled,
    Automatic { token_threshold: u64 },
}

impl CompactionPolicy {
    pub(crate) fn token_threshold(self) -> Option<u64> {
        match self {
            Self::Disabled => None,
            Self::Automatic { token_threshold } => Some(token_threshold),
        }
    }

    pub(crate) fn validate(self) -> Result<(), CoreError> {
        if matches!(self, Self::Automatic { token_threshold: 0 }) {
            return Err(CoreError::invalid_configuration(
                "settings.context.compaction.token_threshold",
                "automatic compaction token_threshold must be greater than zero",
            ));
        }
        Ok(())
    }
}

/// What Core does after a compaction attempt fails.
///
/// The Runtime chooses it because the standalone request carries a `request`
/// field and needs its own request shaping, which a Runtime has to support.
/// A Runtime that omits the setting keeps the earlier behaviour.
#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum CompactionFallback {
    /// Resend the same in-context request once after an invalid summary, and
    /// fail the compaction when the provider call fails.
    #[default]
    Resend,
    /// Send one standalone request after any failed in-context attempt.
    Standalone,
}

impl CompactionFallback {
    pub(crate) fn is_resend(&self) -> bool {
        *self == Self::Resend
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum CompactionTrigger {
    Manual,
    Automatic,
}
