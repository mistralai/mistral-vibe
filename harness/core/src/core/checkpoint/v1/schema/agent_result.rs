use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::core::helpers::{AgentResultError, AgentResultTermination};

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "outcome", rename_all = "snake_case", deny_unknown_fields)]
pub(super) enum CheckpointAgentResultTermination {
    Completed {
        #[serde(default)]
        output: Value,
    },
    Failed {
        error: CheckpointAgentResultError,
    },
    Cancelled {
        #[serde(default, skip_serializing_if = "Option::is_none")]
        reason: Option<String>,
    },
}

impl CheckpointAgentResultTermination {
    pub(super) fn capture(termination: &AgentResultTermination) -> Self {
        match termination {
            AgentResultTermination::Completed { output } => Self::Completed {
                output: output.clone(),
            },
            AgentResultTermination::Failed { error } => Self::Failed {
                error: CheckpointAgentResultError {
                    message: error.message.clone(),
                    code: error.code.clone(),
                    details: error.details.clone(),
                },
            },
            AgentResultTermination::Cancelled { reason } => Self::Cancelled {
                reason: reason.clone(),
            },
        }
    }

    pub(super) fn restore(self) -> AgentResultTermination {
        match self {
            Self::Completed { output } => AgentResultTermination::Completed { output },
            Self::Failed { error } => AgentResultTermination::Failed {
                error: AgentResultError {
                    message: error.message,
                    code: error.code,
                    details: error.details,
                },
            },
            Self::Cancelled { reason } => AgentResultTermination::Cancelled { reason },
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
