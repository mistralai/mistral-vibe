use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::core::helpers::{AgentResultError, AgentResultTermination};
use crate::core::step_protocol::TurnStopReason;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "outcome", rename_all = "snake_case", deny_unknown_fields)]
pub(super) enum CheckpointAgentResultTermination {
    Completed {
        #[serde(default)]
        output: Value,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        stop_reason: Option<CheckpointAgentResultStopReason>,
    },
    Failed {
        error: CheckpointAgentResultError,
    },
    Cancelled {
        #[serde(default, skip_serializing_if = "Option::is_none")]
        reason: Option<String>,
        #[serde(default, skip_serializing_if = "Value::is_null")]
        output: Value,
    },
}

impl CheckpointAgentResultTermination {
    pub(super) fn capture(termination: &AgentResultTermination) -> Self {
        match termination {
            AgentResultTermination::Completed {
                output,
                stop_reason,
            } => Self::Completed {
                output: output.clone(),
                stop_reason: stop_reason.map(CheckpointAgentResultStopReason::capture),
            },
            AgentResultTermination::Failed { error } => Self::Failed {
                error: CheckpointAgentResultError {
                    message: error.message.clone(),
                    code: error.code.clone(),
                    details: error.details.clone(),
                },
            },
            AgentResultTermination::Cancelled { reason, output } => Self::Cancelled {
                reason: reason.clone(),
                output: output.clone(),
            },
        }
    }

    pub(super) fn restore(self) -> AgentResultTermination {
        match self {
            Self::Completed {
                output,
                stop_reason,
            } => AgentResultTermination::Completed {
                output,
                stop_reason: stop_reason.map(CheckpointAgentResultStopReason::restore),
            },
            Self::Failed { error } => AgentResultTermination::Failed {
                error: AgentResultError {
                    message: error.message,
                    code: error.code,
                    details: error.details,
                },
            },
            Self::Cancelled { reason, output } => {
                AgentResultTermination::Cancelled { reason, output }
            }
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub(super) enum CheckpointAgentResultStopReason {
    IterationLimit,
    Skipped,
}

impl CheckpointAgentResultStopReason {
    fn capture(stop_reason: TurnStopReason) -> Self {
        match stop_reason {
            TurnStopReason::IterationLimit => Self::IterationLimit,
            TurnStopReason::Skipped => Self::Skipped,
        }
    }

    fn restore(self) -> TurnStopReason {
        match self {
            Self::IterationLimit => TurnStopReason::IterationLimit,
            Self::Skipped => TurnStopReason::Skipped,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub(super) struct CheckpointAgentResultError {
    message: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    code: Option<String>,
    #[serde(default)]
    details: Value,
}
