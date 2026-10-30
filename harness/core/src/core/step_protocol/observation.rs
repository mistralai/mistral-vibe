use serde::Deserialize;
use serde::Serialize;
use serde_json::Value;

use crate::core::features::compaction::CompactionTrigger;
use crate::core::features::notifications::Notification;
use crate::core::step_protocol::Action;
use crate::core::tools::external::ExternalTool;
use crate::core::wire::completion::{CompletionCandidate, TokenUsage};
use crate::core::wire::content::ContentBlock;
use crate::core::wire::tool::{ProtocolError, ToolResult};

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum TurnStopReason {
    IterationLimit,
    Skipped,
}

/// What one Core feature function produced: actions for the Runtime to run and
/// the observations describing what happened, both in emission order.
///
/// Feature functions return this instead of pushing into a shared
/// `&mut Vec<Observation>`, so emission order is visible at the call site and
/// each function is testable without constructing a whole `HarnessState`.
#[derive(Clone, Debug, Default, PartialEq)]
pub(crate) struct Outcome {
    pub actions: Vec<Action>,
    pub observations: Vec<Observation>,
}

impl Outcome {
    /// The command was accepted and produced no action.
    pub(crate) fn quiet() -> Self {
        Self::default()
    }

    pub(crate) fn action(action: Action) -> Self {
        Self {
            actions: vec![action],
            ..Self::default()
        }
    }

    pub(crate) fn actions(actions: Vec<Action>) -> Self {
        Self {
            actions,
            ..Self::default()
        }
    }

    pub(crate) fn observe(&mut self, observation: Observation) {
        self.observations.push(observation);
    }

    /// Folds a callee's outcome into this one, keeping emission order: whatever
    /// this outcome already observed happened before the callee ran.
    pub(crate) fn absorb(&mut self, other: Self) {
        self.actions.extend(other.actions);
        self.observations.extend(other.observations);
    }

    /// Same as `absorb`, for the common tail-call shape where the callee's
    /// outcome becomes this function's result.
    pub(crate) fn after(mut self, other: Self) -> Self {
        self.absorb(other);
        self
    }
}

#[cfg(test)]
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub(crate) struct AcceptedToolResult {
    pub turn_id: String,
    pub action_id: String,
    pub operation_id: String,
    pub result: ToolResult,
}

/// One command's worth of Core output, handed to `HarnessSession`.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct Transition {
    pub effects: Vec<Action>,
    pub observations: Vec<Observation>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum CandidateDiscardCause {
    Failure { error: ProtocolError },
    Skipped,
    Retry,
    Rejected,
    Steer,
    Interrupt,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum ActionAbandonedCause {
    Steer,
    Interrupt,
    RuntimeFailure,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum ToolDiscoveryKind {
    BestMatch,
    Details,
    AllConnectorCapabilities,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub(crate) struct ToolDiscoverySummary {
    pub kind: ToolDiscoveryKind,
    pub tool_count: usize,
    pub connector_notice_count: usize,
    pub group_namespaces: Vec<String>,
}

/// What a warning is about. Warnings report a degraded but handled situation;
/// the session continues.
#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum WarningCode {
    /// The provider refused a request as too long. Core counts requests as
    /// larger from now on and makes room.
    RequestTooLong,
    /// The summary was cut off twice and is kept truncated.
    CompactionSummaryCutOff,
    /// Items after the summary did not fit and are shown reduced.
    CompactionInputsReduced,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum Observation {
    TurnStarted {
        turn_id: String,
        content: Vec<ContentBlock>,
    },
    TurnSteeringReceived {
        turn_id: String,
        content: Vec<ContentBlock>,
    },
    TurnSteered {
        turn_id: String,
        content: Vec<ContentBlock>,
    },
    NotificationReceived {
        #[serde(default, skip_serializing_if = "Option::is_none")]
        turn_id: Option<String>,
        notification: Notification,
    },
    NotificationDelivered {
        turn_id: String,
        notification: Notification,
    },
    AgentCompletionCandidateDiscarded {
        turn_id: String,
        action_id: String,
        cause: CandidateDiscardCause,
    },
    AssistantMessageCommitted {
        turn_id: String,
        action_id: String,
        candidate: CompletionCandidate,
    },
    ToolExecutionStarted {
        turn_id: String,
        call_id: String,
    },
    ToolExecutionFinished {
        turn_id: String,
        call_id: String,
        result: ToolResult,
    },
    ContextCompacted {
        turn_id: Option<String>,
        action_id: String,
        compaction_id: String,
        attempt: u32,
        trigger: CompactionTrigger,
        summary: String,
        usage: Option<TokenUsage>,
    },
    ContextCompactionFailed {
        turn_id: Option<String>,
        action_id: String,
        compaction_id: String,
        attempt: u32,
        trigger: CompactionTrigger,
        error: ProtocolError,
    },
    Warning {
        /// Null for a warning outside a turn, such as from a between-turn
        /// compaction.
        turn_id: Option<String>,
        code: WarningCode,
        message: String,
        details: Value,
    },
    TurnFailed {
        turn_id: String,
        error: ProtocolError,
    },
    TurnCompleted {
        turn_id: String,
        output: Vec<ContentBlock>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        stop_reason: Option<TurnStopReason>,
    },
    TurnInterrupted {
        turn_id: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        reason: Option<String>,
        /// The content of the latest assistant message of the interrupted Turn.
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        output: Vec<ContentBlock>,
    },
    ToolResultCommitted {
        turn_id: String,
        action_id: String,
        call_id: String,
        call: ExternalTool,
        result: ToolResult,
    },
    ToolDiscoveryFinished {
        turn_id: String,
        call_id: String,
        summary: ToolDiscoverySummary,
    },
    LargeOutputSerialized {
        turn_id: String,
        call_id: String,
        tool_name: String,
        serialized_char_count: usize,
    },
    ActionAbandoned {
        turn_id: String,
        action_id: String,
        cause: ActionAbandonedCause,
    },
}
