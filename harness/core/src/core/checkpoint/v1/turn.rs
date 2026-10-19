use serde::{Deserialize, Serialize};

use crate::core::features::compaction::{
    CompactionBudget, CompactionProjection, CompactionProjectionFit, CompactionTrigger,
    PendingCompaction,
};
use crate::core::hooks::{HookPoint, hook_action_id};
use crate::core::model_context::ModelContext;
use crate::core::state::{ActivePhase, ActiveTurn, QueuedTurn, TurnState};
use crate::core::step_protocol::ToolDefinition;
use crate::core::turn::{PendingLifecycleHook, SuspendedTurn, TurnOutcome};
use crate::core::wire::content::{ContentBlock, validate_non_empty_content};
use crate::core::wire::message::StoredMessage;

use super::context::CheckpointStoredMessage;
use super::schema::{CheckpointCompletionCandidate, CheckpointProtocolError};
use super::tool::CheckpointToolBatch;
use super::validate_derived_hook_action_id;

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
        queued: CheckpointQueuedTurn,
        pending_steer: Vec<CheckpointStoredMessage>,
        model_input_ready: bool,
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
                    queued: CheckpointQueuedTurn::capture(&active.queued)?,
                    pending_steer: capture_pending_steer(&active.pending_steer)?,
                    model_input_ready: active.model_input_ready,
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
        generated_system: StoredMessage,
        tools: &[ToolDefinition],
        budget: CompactionBudget,
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
                        projection.restore(generated_system.clone(), tools, budget)?,
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
            } => {
                if turn_id.trim().is_empty() {
                    return Err("active checkpoint turn ID must not be empty".to_string());
                }
                let phase = phase.restore(
                    &turn_id,
                    iterations,
                    context,
                    generated_system,
                    tools,
                    budget,
                )?;
                Ok(TurnState::Active(ActiveTurn {
                    turn_id,
                    iterations,
                    phase,
                    pending_steer: restore_pending_steer(pending_steer)?,
                    model_input_ready,
                    queued: queued.restore()?,
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
    messages: Vec<CheckpointStoredMessage>,
}

impl CheckpointCompactionProjection {
    fn capture(projection: &CompactionProjection) -> Result<Self, String> {
        let messages = projection
            .messages()
            .iter()
            .filter(|message| !message.is_generated_system())
            .map(CheckpointStoredMessage::capture)
            .collect::<Result<Vec<_>, _>>()?;
        Ok(Self {
            attempt: projection.attempt(),
            messages,
        })
    }

    fn restore(
        self,
        generated_system: StoredMessage,
        tools: &[ToolDefinition],
        budget: CompactionBudget,
    ) -> Result<CompactionProjection, String> {
        let mut messages = vec![generated_system];
        messages.extend(
            self.messages
                .into_iter()
                .map(CheckpointStoredMessage::restore)
                .collect::<Result<Vec<_>, _>>()?,
        );
        let projection = CompactionProjection::restore(messages, self.attempt)
            .map_err(|error| error.detail().to_string())?;
        match projection
            .fit_to_budget(tools, budget)
            .map_err(|error| error.detail().to_string())?
        {
            CompactionProjectionFit::Fitted(projection) => Ok(projection),
            CompactionProjectionFit::TooLarge => Err(
                "token threshold cannot fit the generated system prompt, latest user message, tool catalog, and compaction prompt".to_string(),
            ),
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
        generated_system: StoredMessage,
        tools: &[ToolDefinition],
        budget: CompactionBudget,
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
                    projection.restore(generated_system, tools, budget)?,
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
    Pending {
        turn_id: String,
        messages: Vec<CheckpointStoredMessage>,
    },
}

impl CheckpointQueuedTurn {
    fn capture(queued: &QueuedTurn) -> Result<Self, String> {
        match queued {
            QueuedTurn::Empty => Ok(Self::Empty),
            QueuedTurn::Pending { turn_id, messages } => {
                validate_identity(turn_id, "queued checkpoint turn ID")?;
                if messages.is_empty() {
                    return Err("queued turn must contain at least one message".to_string());
                }
                Ok(Self::Pending {
                    turn_id: turn_id.clone(),
                    messages: messages
                        .iter()
                        .map(|message| {
                            CheckpointStoredMessage::capture_visible_user(message, "queued turn")
                        })
                        .collect::<Result<_, _>>()?,
                })
            }
        }
    }

    fn restore(self) -> Result<QueuedTurn, String> {
        match self {
            Self::Empty => Ok(QueuedTurn::Empty),
            Self::Pending { turn_id, messages } => {
                validate_identity(&turn_id, "queued checkpoint turn ID")?;
                if messages.is_empty() {
                    return Err("queued turn must contain at least one message".to_string());
                }
                Ok(QueuedTurn::Pending {
                    turn_id,
                    messages: messages
                        .into_iter()
                        .map(|message| message.restore_visible_user("queued turn"))
                        .collect::<Result<_, _>>()?,
                })
            }
        }
    }
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
        previous_turn: CheckpointInactiveTurnState,
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
                suspended,
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
                    previous_turn: CheckpointInactiveTurnState::capture(suspended)?,
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
                previous_turn,
            } => {
                validate_non_empty_content(&user_content, "pre-agent user content")
                    .map_err(|error| error.detail().to_string())?;
                Ok(PendingLifecycleHook::PreAgentTurn {
                    action_id: hook_action_id(active_turn_id, HookPoint::PreAgentTurn),
                    hook_binding_ids,
                    turn_id: active_turn_id.to_string(),
                    user_content,
                    suspended: previous_turn.restore()?,
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

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
enum CheckpointInactiveTurnState {
    Idle,
    Terminal {
        turn_id: String,
        outcome: CheckpointTurnOutcome,
    },
}

impl CheckpointInactiveTurnState {
    fn capture(turn: &SuspendedTurn) -> Result<Self, String> {
        match turn {
            SuspendedTurn::Idle => Ok(Self::Idle),
            SuspendedTurn::Terminal { turn_id, outcome } => {
                validate_identity(turn_id, "previous terminal checkpoint turn ID")?;
                Ok(Self::Terminal {
                    turn_id: turn_id.clone(),
                    outcome: CheckpointTurnOutcome::capture(outcome),
                })
            }
        }
    }

    fn restore(self) -> Result<SuspendedTurn, String> {
        match self {
            Self::Idle => Ok(SuspendedTurn::Idle),
            Self::Terminal { turn_id, outcome } => {
                validate_identity(&turn_id, "previous terminal checkpoint turn ID")?;
                Ok(SuspendedTurn::Terminal {
                    turn_id,
                    outcome: outcome.restore(),
                })
            }
        }
    }
}
