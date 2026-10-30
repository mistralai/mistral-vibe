use serde::de::IgnoredAny;
use serde::{Deserialize, Serialize};

use crate::core::features::compaction::{
    CompactionBudget, CompactionPhase, CompactionProjection, CompactionProjectionFit,
    CompactionTrigger, PendingCompaction, Prompts as CompactionPrompts,
};
use crate::core::hooks::{HookPoint, hook_action_id};
use crate::core::model_context::ModelContext;
use crate::core::state::{ActivePhase, ActiveTurn, TurnState};
use crate::core::step_protocol::ToolDefinition;
use crate::core::turn::{PendingLifecycleHook, TurnOutcome};
use crate::core::wire::content::{ContentBlock, validate_non_empty_content};
use crate::core::wire::message::StoredMessage;

use super::context::CheckpointStoredMessage;
use super::schema::{CheckpointCompletionCandidate, CheckpointProtocolError};
use super::tool::CheckpointToolBatch;
use super::validate_derived_hook_action_id;

/// What restoring a pending compaction needs from the restored session.
pub(super) struct CompactionRestore<'a> {
    pub generated_system: StoredMessage,
    pub tools: &'a [ToolDefinition],
    pub budget: CompactionBudget,
    pub prompts: &'a CompactionPrompts,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(transparent)]
pub(super) struct CheckpointTurnState(CheckpointTurnStateRepr);

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
enum CheckpointTurnStateRepr {
    Idle,
    Compacting {
        compaction_id: String,
        trigger: CheckpointCompactionTrigger,
        projection: CheckpointCompactionProjection,
    },
    Active {
        turn_id: String,
        iterations: u32,
        phase: CheckpointActivePhase,
        /// Retained for V1 compatibility; new checkpoints always write Empty.
        queued: CheckpointQueuedTurn,
        pending_steer: Vec<CheckpointStoredMessage>,
        model_input_ready: bool,
        #[serde(default, skip_serializing_if = "is_zero")]
        output_limit_retries: u32,
    },
    Terminal {
        turn_id: String,
        outcome: CheckpointTurnOutcome,
    },
}

impl CheckpointTurnState {
    pub(super) fn capture(turn: &TurnState) -> Result<Self, String> {
        match turn {
            TurnState::Idle => Ok(Self(CheckpointTurnStateRepr::Idle)),
            TurnState::Compacting { pending } => {
                let trigger = pending.between_turns_trigger().ok_or_else(|| {
                    "between-turn compaction contains an in-turn continuation".to_string()
                })?;
                validate_identity(pending.compaction_id(), "compaction ID")?;
                Ok(Self(CheckpointTurnStateRepr::Compacting {
                    compaction_id: pending.compaction_id().to_string(),
                    trigger: CheckpointCompactionTrigger::capture(trigger),
                    projection: CheckpointCompactionProjection::capture(pending.projection())?,
                }))
            }
            TurnState::Active(active) => {
                validate_identity(&active.turn_id, "active checkpoint turn ID")?;
                Ok(Self(CheckpointTurnStateRepr::Active {
                    turn_id: active.turn_id.clone(),
                    iterations: active.iterations,
                    phase: CheckpointActivePhase::capture(
                        &active.phase,
                        &active.turn_id,
                        active.iterations,
                    )?,
                    queued: CheckpointQueuedTurn::Empty,
                    pending_steer: capture_pending_steer(&active.pending_steer)?,
                    model_input_ready: active.model_input_ready,
                    output_limit_retries: active.output_limit_retries,
                }))
            }
            TurnState::Terminal { turn_id, outcome } => {
                validate_identity(turn_id, "terminal checkpoint turn ID")?;
                Ok(Self(CheckpointTurnStateRepr::Terminal {
                    turn_id: turn_id.clone(),
                    outcome: CheckpointTurnOutcome::capture(outcome),
                }))
            }
        }
    }

    pub(super) fn restore(
        self,
        context: &ModelContext,
        compaction: CompactionRestore<'_>,
    ) -> Result<TurnState, String> {
        match self.0 {
            CheckpointTurnStateRepr::Idle => Ok(TurnState::Idle),
            CheckpointTurnStateRepr::Compacting {
                compaction_id,
                trigger,
                projection,
            } => {
                validate_identity(&compaction_id, "compaction ID")?;
                Ok(TurnState::Compacting {
                    pending: PendingCompaction::between_turns(
                        compaction_id,
                        trigger.restore(),
                        projection.restore(compaction)?,
                    ),
                })
            }
            CheckpointTurnStateRepr::Active {
                turn_id,
                iterations,
                phase,
                queued,
                pending_steer,
                model_input_ready,
                output_limit_retries,
            } => {
                if !matches!(queued, CheckpointQueuedTurn::Empty) {
                    return Err("checkpoint contains pending queued requests; resume with the previous build before upgrading".to_string());
                }

                if turn_id.trim().is_empty() {
                    return Err("active checkpoint turn ID must not be empty".to_string());
                }
                let phase = phase.restore(&turn_id, iterations, context, compaction)?;
                Ok(TurnState::Active(ActiveTurn {
                    turn_id,
                    iterations,
                    phase,
                    pending_steer: restore_pending_steer(pending_steer)?,
                    model_input_ready,
                    output_limit_retries,
                }))
            }
            CheckpointTurnStateRepr::Terminal { turn_id, outcome } => {
                validate_identity(&turn_id, "terminal checkpoint turn ID")?;
                Ok(TurnState::Terminal {
                    turn_id,
                    outcome: outcome.restore(),
                })
            }
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
struct CheckpointCompactionProjection {
    attempt: u32,
    /// Absent for the in-context phase. Checkpoints captured before the
    /// phases existed name it `request`.
    #[serde(
        default,
        alias = "request",
        skip_serializing_if = "CheckpointCompactionPhase::is_in_context"
    )]
    phase: CheckpointCompactionPhase,
    /// Present only in the cut-off retry phase.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    cut_off_retry: Option<CheckpointCutOffRetry>,
    /// After a refusal as too long in this compaction, the estimate every
    /// later summary request stays under.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    refit_limit: Option<u64>,
    /// What the summary covers, without the generated system message. A
    /// checkpoint written before the summary scope ended at the last reply
    /// holds the fitted request instead, which restores the same way.
    messages: Vec<CheckpointStoredMessage>,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq)]
enum CheckpointCompactionPhase {
    #[default]
    #[serde(rename = "in_context")]
    InContext,
    #[serde(rename = "standalone")]
    Standalone,
    #[serde(rename = "cut_off_retry")]
    CutOffRetry,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
struct CheckpointCutOffRetry {
    attempt: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    partial: Option<String>,
}

impl CheckpointCompactionPhase {
    fn is_in_context(&self) -> bool {
        *self == Self::InContext
    }
}

fn capture_phase(
    phase: &CompactionPhase,
) -> (CheckpointCompactionPhase, Option<CheckpointCutOffRetry>) {
    match phase {
        CompactionPhase::InContext => (CheckpointCompactionPhase::InContext, None),
        CompactionPhase::Standalone => (CheckpointCompactionPhase::Standalone, None),
        CompactionPhase::CutOffRetry { attempt, partial } => (
            CheckpointCompactionPhase::CutOffRetry,
            Some(CheckpointCutOffRetry {
                attempt: *attempt,
                partial: partial.clone(),
            }),
        ),
    }
}

fn restore_phase(
    phase: CheckpointCompactionPhase,
    cut_off_retry: Option<CheckpointCutOffRetry>,
) -> Result<CompactionPhase, String> {
    match (phase, cut_off_retry) {
        (CheckpointCompactionPhase::InContext, None) => Ok(CompactionPhase::InContext),
        (CheckpointCompactionPhase::Standalone, None) => Ok(CompactionPhase::Standalone),
        (CheckpointCompactionPhase::CutOffRetry, Some(retry)) => {
            if retry
                .partial
                .as_ref()
                .is_some_and(|partial| partial.trim().is_empty())
            {
                return Err("cut-off retry partial summary must not be empty".to_string());
            }
            Ok(CompactionPhase::CutOffRetry {
                attempt: retry.attempt,
                partial: retry.partial,
            })
        }
        (CheckpointCompactionPhase::CutOffRetry, None) => {
            Err("cut-off retry phase is missing its attempt".to_string())
        }
        (_, Some(_)) => Err("only the cut-off retry phase records a cut-off retry".to_string()),
    }
}

impl CheckpointCompactionProjection {
    fn capture(projection: &CompactionProjection) -> Result<Self, String> {
        let messages = projection
            .messages()
            .iter()
            .filter(|message| !message.is_generated_system())
            .map(CheckpointStoredMessage::capture)
            .collect::<Result<Vec<_>, _>>()?;
        let (phase, cut_off_retry) = capture_phase(projection.phase());
        Ok(Self {
            attempt: projection.attempt(),
            phase,
            cut_off_retry,
            refit_limit: projection.refit_limit(),
            messages,
        })
    }

    fn restore(self, compaction: CompactionRestore<'_>) -> Result<CompactionProjection, String> {
        let phase = restore_phase(self.phase, self.cut_off_retry)?;
        let mut messages = vec![compaction.generated_system];
        messages.extend(
            self.messages
                .into_iter()
                .map(CheckpointStoredMessage::restore)
                .collect::<Result<Vec<_>, _>>()?,
        );
        match CompactionProjection::restore(
            messages,
            self.attempt,
            phase,
            self.refit_limit,
            compaction.tools,
            compaction.budget,
            compaction.prompts,
        )
        .map_err(|error| error.detail().to_string())?
        {
            CompactionProjectionFit::Fitted(projection) => Ok(projection),
            CompactionProjectionFit::TooLarge => {
                Err("token threshold cannot fit the smallest compaction request".to_string())
            }
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
enum CheckpointCompactionTrigger {
    #[serde(rename = "manual")]
    Manual,
    #[serde(rename = "automatic")]
    Automatic,
}

impl CheckpointCompactionTrigger {
    fn capture(trigger: CompactionTrigger) -> Self {
        match trigger {
            CompactionTrigger::Manual => Self::Manual,
            CompactionTrigger::Automatic => Self::Automatic,
        }
    }

    fn restore(self) -> CompactionTrigger {
        match self {
            Self::Manual => CompactionTrigger::Manual,
            Self::Automatic => CompactionTrigger::Automatic,
        }
    }
}

#[allow(clippy::enum_variant_names)]
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
enum CheckpointActivePhase {
    AwaitingCompletion {
        action_id: String,
    },
    AwaitingCompaction {
        compaction_id: String,
        projection: CheckpointCompactionProjection,
    },
    AwaitingToolBatch {
        batch: CheckpointToolBatch,
    },
    AwaitingHook {
        pending: CheckpointPendingLifecycleHook,
    },
}

impl CheckpointActivePhase {
    fn capture(phase: &ActivePhase, turn_id: &str, iterations: u32) -> Result<Self, String> {
        match phase {
            ActivePhase::AwaitingCompletion { action_id } => Ok(Self::AwaitingCompletion {
                action_id: action_id.clone(),
            }),
            ActivePhase::AwaitingCompaction { pending } => {
                let Some((pending_turn_id, pending_iterations)) = pending.in_turn_identity() else {
                    return Err(
                        "active compaction contains a between-turn continuation".to_string()
                    );
                };
                if pending_turn_id != turn_id || pending_iterations != iterations {
                    return Err(
                        "active compaction continuation does not match its active turn".to_string(),
                    );
                }
                validate_identity(pending.compaction_id(), "compaction ID")?;
                Ok(Self::AwaitingCompaction {
                    compaction_id: pending.compaction_id().to_string(),
                    projection: CheckpointCompactionProjection::capture(pending.projection())?,
                })
            }
            ActivePhase::AwaitingToolBatch { batch } => Ok(Self::AwaitingToolBatch {
                batch: CheckpointToolBatch::capture(batch)?,
            }),
            ActivePhase::AwaitingHook { pending } => Ok(Self::AwaitingHook {
                pending: CheckpointPendingLifecycleHook::capture(pending, turn_id)?,
            }),
        }
    }

    fn restore(
        self,
        turn_id: &str,
        iterations: u32,
        context: &ModelContext,
        compaction: CompactionRestore<'_>,
    ) -> Result<ActivePhase, String> {
        match self {
            Self::AwaitingCompletion { action_id } => {
                Ok(ActivePhase::AwaitingCompletion { action_id })
            }
            Self::AwaitingCompaction {
                compaction_id,
                projection,
            } => Ok(ActivePhase::AwaitingCompaction {
                pending: PendingCompaction::in_turn(
                    compaction_id,
                    turn_id.to_string(),
                    iterations,
                    projection.restore(compaction)?,
                ),
            }),
            Self::AwaitingToolBatch { batch } => Ok(ActivePhase::AwaitingToolBatch {
                batch: batch.restore(super::tool_batch_calls_from_context(context)?)?,
            }),
            Self::AwaitingHook { pending } => Ok(ActivePhase::AwaitingHook {
                pending: pending.restore(turn_id)?,
            }),
        }
    }
}

fn capture_pending_steer(
    messages: &[StoredMessage],
) -> Result<Vec<CheckpointStoredMessage>, String> {
    messages
        .iter()
        .map(|message| CheckpointStoredMessage::capture_visible_user(message, "pending steering"))
        .collect()
}

fn restore_pending_steer(
    messages: Vec<CheckpointStoredMessage>,
) -> Result<Vec<StoredMessage>, String> {
    messages
        .into_iter()
        .map(|message| message.restore_visible_user("pending steering"))
        .collect()
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
enum CheckpointQueuedTurn {
    Empty,
    /// Decoded so restore can explain how to recover these requests.
    Pending {
        turn_id: String,
        messages: Vec<CheckpointStoredMessage>,
    },
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
enum CheckpointTurnOutcome {
    Completed { output: Vec<ContentBlock> },
    Rejected { reason: String },
    Failed { error: CheckpointProtocolError },
    Interrupted { reason: Option<String> },
}

impl CheckpointTurnOutcome {
    fn capture(outcome: &TurnOutcome) -> Self {
        match outcome {
            TurnOutcome::Completed { output } => Self::Completed {
                output: output.clone(),
            },
            TurnOutcome::Rejected { reason } => Self::Rejected {
                reason: reason.clone(),
            },
            TurnOutcome::Failed { error } => Self::Failed {
                error: CheckpointProtocolError::capture(error),
            },
            TurnOutcome::Interrupted { reason } => Self::Interrupted {
                reason: reason.clone(),
            },
        }
    }

    fn restore(self) -> TurnOutcome {
        match self {
            Self::Completed { output } => TurnOutcome::Completed { output },
            Self::Rejected { reason } => TurnOutcome::Rejected { reason },
            Self::Failed { error } => TurnOutcome::Failed {
                error: error.restore(),
            },
            Self::Interrupted { reason } => TurnOutcome::Interrupted { reason },
        }
    }
}

#[allow(clippy::large_enum_variant)]
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "hook", rename_all = "snake_case", deny_unknown_fields)]
enum CheckpointPendingLifecycleHook {
    PreAgentTurn {
        hook_binding_ids: Vec<String>,
        user_content: Vec<ContentBlock>,
        /// Deprecated: still written by `mistralai-vibe-harness` 0.1.9. Accepted
        /// so those checkpoints restore; the value is ignored and never written.
        #[serde(default, skip_serializing)]
        previous_turn: IgnoredAny,
    },
    PreLlmCall {
        hook_binding_ids: Vec<String>,
        completion_action_id: String,
    },
    PostLlmCall {
        hook_binding_ids: Vec<String>,
        completion_action_id: String,
        candidate: CheckpointCompletionCandidate,
    },
    PostAgentTurn {
        hook_binding_ids: Vec<String>,
        completion_action_id: String,
        candidate: CheckpointCompletionCandidate,
    },
}

impl CheckpointPendingLifecycleHook {
    fn capture(pending: &PendingLifecycleHook, active_turn_id: &str) -> Result<Self, String> {
        match pending {
            PendingLifecycleHook::PreAgentTurn {
                action_id,
                hook_binding_ids,
                turn_id,
                user_content,
            } => {
                validate_non_empty_content(user_content, "pre-agent user content")
                    .map_err(|error| error.detail().to_string())?;
                if turn_id != active_turn_id {
                    return Err(format!(
                        "pre-agent hook turn ID {turn_id:?} does not match active turn ID {active_turn_id:?}"
                    ));
                }
                validate_derived_hook_action_id(
                    action_id,
                    active_turn_id,
                    HookPoint::PreAgentTurn,
                )?;
                Ok(Self::PreAgentTurn {
                    hook_binding_ids: hook_binding_ids.clone(),
                    user_content: user_content.clone(),
                    previous_turn: IgnoredAny,
                })
            }
            PendingLifecycleHook::PreLlmCall {
                action_id,
                hook_binding_ids,
                completion_action_id,
            } => {
                validate_derived_hook_action_id(
                    action_id,
                    completion_action_id,
                    HookPoint::PreLlmCall,
                )?;
                Ok(Self::PreLlmCall {
                    hook_binding_ids: hook_binding_ids.clone(),
                    completion_action_id: completion_action_id.clone(),
                })
            }
            PendingLifecycleHook::PostLlmCall {
                action_id,
                hook_binding_ids,
                completion_action_id,
                candidate,
            } => {
                validate_derived_hook_action_id(
                    action_id,
                    completion_action_id,
                    HookPoint::PostLlmCall,
                )?;
                Ok(Self::PostLlmCall {
                    hook_binding_ids: hook_binding_ids.clone(),
                    completion_action_id: completion_action_id.clone(),
                    candidate: CheckpointCompletionCandidate::capture(candidate)?,
                })
            }
            PendingLifecycleHook::PostAgentTurn {
                action_id,
                hook_binding_ids,
                completion_action_id,
                candidate,
            } => {
                validate_derived_hook_action_id(
                    action_id,
                    completion_action_id,
                    HookPoint::PostAgentTurn,
                )?;
                Ok(Self::PostAgentTurn {
                    hook_binding_ids: hook_binding_ids.clone(),
                    completion_action_id: completion_action_id.clone(),
                    candidate: CheckpointCompletionCandidate::capture(candidate)?,
                })
            }
        }
    }

    fn restore(self, active_turn_id: &str) -> Result<PendingLifecycleHook, String> {
        match self {
            Self::PreAgentTurn {
                hook_binding_ids,
                user_content,
                previous_turn: _,
            } => {
                validate_non_empty_content(&user_content, "pre-agent user content")
                    .map_err(|error| error.detail().to_string())?;
                Ok(PendingLifecycleHook::PreAgentTurn {
                    action_id: hook_action_id(active_turn_id, HookPoint::PreAgentTurn),
                    hook_binding_ids,
                    turn_id: active_turn_id.to_string(),
                    user_content,
                })
            }
            Self::PreLlmCall {
                hook_binding_ids,
                completion_action_id,
            } => Ok(PendingLifecycleHook::PreLlmCall {
                action_id: hook_action_id(&completion_action_id, HookPoint::PreLlmCall),
                hook_binding_ids,
                completion_action_id,
            }),
            Self::PostLlmCall {
                hook_binding_ids,
                completion_action_id,
                candidate,
            } => Ok(PendingLifecycleHook::PostLlmCall {
                action_id: hook_action_id(&completion_action_id, HookPoint::PostLlmCall),
                hook_binding_ids,
                completion_action_id,
                candidate: candidate.restore()?,
            }),
            Self::PostAgentTurn {
                hook_binding_ids,
                completion_action_id,
                candidate,
            } => Ok(PendingLifecycleHook::PostAgentTurn {
                action_id: hook_action_id(&completion_action_id, HookPoint::PostAgentTurn),
                hook_binding_ids,
                completion_action_id,
                candidate: candidate.restore()?,
            }),
        }
    }
}

fn validate_identity(id: &str, label: &str) -> Result<(), String> {
    if id.trim().is_empty() {
        return Err(format!("{label} must not be empty"));
    }
    Ok(())
}

fn is_zero(value: &u32) -> bool {
    *value == 0
}
