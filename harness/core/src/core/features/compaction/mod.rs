mod context;
mod execution;
mod projection;

use serde::{Deserialize, Serialize};

use crate::core::config::ImageDeliveryMode;
use crate::core::error::CoreError;

pub(in crate::core) use context::{
    SummaryAcceptance, SummaryRejection, accept_summary, fit_latest_user_message_to_budget,
    latest_preserved_user_index, minimum_replacement_fits, prompt_message, validate_prompt,
};
pub(in crate::core) use execution::{
    CompactionAttemptResolution, CompactionRequest, CompactionResolution,
    CompactionStartResolution, PendingCompaction, fail_compaction, finish_compaction,
    start_compaction,
};
pub(in crate::core) use projection::{CompactionProjection, CompactionProjectionFit};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct CompactionBudget {
    pub token_threshold: Option<u64>,
    pub image_delivery: ImageDeliveryMode,
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

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum CompactionTrigger {
    Manual,
    Automatic,
}
