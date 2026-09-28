use serde::Deserialize;
use serde::Serialize;

use crate::core::features::compaction::CompactionTrigger;
use crate::core::features::notifications::Notification;
use crate::core::step_protocol::Action;
use crate::core::turn::TurnOutcome;
use crate::core::wire::completion::{CompletionCandidate, TokenUsage};
use crate::core::wire::content::ContentBlock;
use crate::core::wire::tool::{ProtocolError, ToolResult};

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum TurnStopReason {
    IterationLimit,
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
    /// Set when this command ended a turn.
    ///
    /// Separate from `TurnState::Terminal` because a queued follow-up turn can
    /// start within the same command: state is `Active` again by the time the
    /// transition is built, but the Runtime still needs to hear that the
    /// previous turn finished and with what.
    pub completed: Option<TurnCompletion>,
}

/// A turn reaching its end, as reported to the Runtime for one command.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct TurnCompletion {
    pub turn_id: String,
    pub outcome: TurnOutcome,
}

impl Outcome {
    /// The command was accepted and produced no action, e.g. a queued message.
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

    /// Records the turn this command ended.
    pub(crate) fn complete_turn(&mut self, turn_id: String, outcome: TurnOutcome) {
        self.completed = Some(TurnCompletion { turn_id, outcome });
    }

    /// Folds a callee's outcome into this one, keeping emission order: whatever
    /// this outcome already observed happened before the callee ran.
    ///
    /// A callee's completion wins, because the callee ran later: when
    /// `complete_output` finishes one turn and starts a queued one, the queued
    /// turn's own path is what decides the final report.
    pub(crate) fn absorb(&mut self, other: Self) {
        self.actions.extend(other.actions);
        self.observations.extend(other.observations);
        self.completed = other.completed.or(self.completed.take());
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
    /// The turn this command ended, if any. Replaces a pair of `output` and
    /// `rejection_reason` options that could disagree with each other and with
    /// the phase they sat beside.
    pub completed: Option<TurnCompletion>,
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
    },
    ToolResultCommitted {
        turn_id: String,
        action_id: String,
        call_id: String,
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
