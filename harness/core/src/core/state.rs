use crate::core::capabilities::HookBindingIndex;
use crate::core::config::HarnessConfig;
use crate::core::error::CoreError;
use crate::core::features::compaction::PendingCompaction;
use crate::core::features::notifications::{Acceptance, Delivery, Notification, NotificationState};
use crate::core::hooks::HookPoint;
use crate::core::hooks::HookToolKey;
use crate::core::model_context::ModelContext;
use crate::core::prompt::build_system_prompt;
use crate::core::step_protocol::{
    Action, CompletionActionKind, FilesystemOperation, ModelInputUpdate, ModelMessageUpdate,
    ModelToolCatalogUpdate,
};
use crate::core::step_protocol::{
    CommandState, PendingActionInspection, PendingCompletionPurpose,
    PendingFilesystemOperationInspection, SessionStatus, Turn,
};
use crate::core::step_protocol::{Observation, Outcome, TurnCompletion};
use crate::core::tools::context::ToolContext;
use crate::core::tools::execution::ToolBatch;
use crate::core::tools::resolved::ResolvedTools;
use crate::core::turn::PendingLifecycleHook;
use crate::core::turn::TurnOutcome;
use crate::core::wire::message::{Message, StoredMessage};

/// Private Core state.
///
/// Commands are applied to a cloned candidate state. The candidate replaces the
/// live session only after the full transition succeeds, so rejected commands
/// cannot leave partial mutation behind.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct HarnessState {
    pub config: HarnessConfig,
    pub(crate) resolved_tools: ResolvedTools,
    pub hook_binding_index: HookBindingIndex,
    pub context: ModelContext,
    pub compaction_count: u64,
    /// The provider-reported context size (input + output tokens) of the
    /// most recent agent completion, when the provider reported usage.
    ///
    /// The serialized-bytes estimate under-counts for tokenizers that pack
    /// more tokens per byte than the fixed ratio assumes, so the automatic
    /// compaction trigger also consults this actual measurement. Cleared
    /// when compaction replaces the live context, since the reported size
    /// then predates the replacement; the next completion re-establishes it.
    /// A failed or interrupted compaction keeps it, so the over-budget
    /// signal survives to the next turn.
    pub last_reported_context_tokens: Option<u64>,
    pub turn: TurnState,
    /// The last turn to reach a terminal state.
    ///
    /// Session history rather than turn state: it survives a new turn starting
    /// and a context injection resetting the session to idle, which is why it
    /// does not live inside [`TurnState`].
    pub last_finished_turn_id: Option<String>,
    pub notifications: NotificationState,
    pub(crate) tool_catalog: ToolCatalogLedger,
}

#[derive(Clone, Debug, Default)]
pub(crate) struct ToolCatalogLedger {
    pub(crate) revision: u64,
    changed: bool,
}

// Model-input revisions are disposable transport bookkeeping. They do not
// participate in semantic state equality or checkpoint compatibility.
impl PartialEq for ToolCatalogLedger {
    fn eq(&self, _other: &Self) -> bool {
        true
    }
}

pub(crate) struct StateInspection {
    pub(crate) status: SessionStatus,
    pub(crate) active_turn_id: Option<String>,
    pub(crate) last_turn_id: Option<String>,
    pub(crate) pending_actions: Vec<PendingActionInspection>,
    pub(crate) message_count: usize,
}

impl HarnessState {
    pub(crate) fn tool_context(&self) -> Result<ToolContext<'_>, CoreError> {
        Ok(ToolContext::new(
            &self.resolved_tools,
            &self.hook_binding_index,
            self.require_active()?.turn_id.as_str(),
            self.context.message_count(),
        ))
    }

    pub(crate) fn receive_notification(
        &mut self,
        notification: Notification,
    ) -> Result<Outcome, CoreError> {
        let turn_id = self.active_turn_id().map(str::to_string);
        let delivery = match &self.turn {
            TurnState::Active(active) if active.model_input_ready => Delivery::Immediate,
            TurnState::Active(_) | TurnState::Compacting { .. } => Delivery::Deferred,
            TurnState::Idle | TurnState::Terminal { .. } => Delivery::Immediate,
        };
        let acceptance = self.notifications.accept(notification.clone(), delivery)?;
        if matches!(acceptance, Acceptance::Duplicate) {
            return Ok(Outcome::quiet());
        }
        let mut outcome = Outcome::quiet();
        outcome.observe(Observation::NotificationReceived {
            turn_id,
            notification,
        });
        Ok(outcome)
    }

    pub(crate) fn mark_model_input_ready(&mut self) -> Result<(), CoreError> {
        self.require_active_mut()?.mark_model_input_ready();
        self.notifications.mark_ready();
        Ok(())
    }

    pub(crate) fn has_pending_model_input(&self) -> bool {
        self.active()
            .is_some_and(|active| !active.pending_steer.is_empty())
            || self.notifications.has_pending()
    }

    /// Seeds the system prompt when a session takes its first message.
    pub(crate) fn ensure_system_prompt(&mut self) {
        if self.context.is_empty() {
            self.context.push(self.generated_system_message());
        }
    }

    pub(crate) fn generated_system_message(&self) -> StoredMessage {
        StoredMessage::generated_system(Message::system_text(build_system_prompt(
            &self.config,
            self.resolved_tools.tool_group_inventory_prompt_section(),
        )))
    }

    pub(crate) fn active(&self) -> Option<&ActiveTurn> {
        match &self.turn {
            TurnState::Active(active) => Some(active),
            TurnState::Idle | TurnState::Compacting { .. } | TurnState::Terminal { .. } => None,
        }
    }

    pub(crate) fn active_mut(&mut self) -> Option<&mut ActiveTurn> {
        match &mut self.turn {
            TurnState::Active(active) => Some(active),
            TurnState::Idle | TurnState::Compacting { .. } | TurnState::Terminal { .. } => None,
        }
    }

    /// The turn a command must correlate against, or an invariant error.
    ///
    /// Callers reach for this on paths that only run while a turn is active, so
    /// a `None` here is a Core bug rather than bad input.
    pub(crate) fn require_active(&self) -> Result<&ActiveTurn, CoreError> {
        self.active()
            .ok_or_else(|| CoreError::invariant("active phase is missing its turn identity"))
    }

    pub(crate) fn require_active_mut(&mut self) -> Result<&mut ActiveTurn, CoreError> {
        self.active_mut()
            .ok_or_else(|| CoreError::invariant("active phase is missing its turn identity"))
    }

    pub(crate) fn active_turn_id(&self) -> Option<&str> {
        self.active().map(|active| active.turn_id.as_str())
    }

    pub(crate) fn command_state(&self) -> CommandState {
        match self.turn {
            TurnState::Idle | TurnState::Terminal { .. } => CommandState::Idle,
            TurnState::Compacting { .. } => CommandState::Compacting,
            TurnState::Active(_) => CommandState::Running,
        }
    }

    pub(crate) fn pending_action_ids(&self) -> Vec<String> {
        self.pending_actions()
            .into_iter()
            .map(|action| action.action_id().to_string())
            .collect()
    }

    pub(crate) fn pending_actions(&self) -> Vec<Action> {
        match &self.turn {
            TurnState::Idle | TurnState::Terminal { .. } => Vec::new(),
            TurnState::Compacting { pending } => vec![completion_action(
                self,
                pending.action_id().to_string(),
                None,
                CompletionActionKind::Compaction {
                    compaction_id: pending.compaction_id().to_string(),
                    trigger: pending.trigger(),
                    attempt: pending.attempt(),
                },
                0,
            )],
            TurnState::Active(active) => match &active.phase {
                ActivePhase::AwaitingCompletion { action_id } => vec![completion_action(
                    self,
                    action_id.clone(),
                    Some(active.turn_id.clone()),
                    CompletionActionKind::Agent,
                    active.iterations,
                )],
                ActivePhase::AwaitingCompaction { pending } => vec![completion_action(
                    self,
                    pending.action_id().to_string(),
                    Some(active.turn_id.clone()),
                    CompletionActionKind::Compaction {
                        compaction_id: pending.compaction_id().to_string(),
                        trigger: pending.trigger(),
                        attempt: pending.attempt(),
                    },
                    active.iterations,
                )],
                ActivePhase::AwaitingHook { pending } => {
                    vec![pending.action(&active.turn_id)]
                }
                ActivePhase::AwaitingToolBatch { batch, .. } => {
                    batch.pending_actions(&active.turn_id)
                }
            },
        }
    }

    pub(crate) fn pending_compaction(&self) -> Option<&PendingCompaction> {
        match &self.turn {
            TurnState::Compacting { pending } => Some(pending),
            TurnState::Active(ActiveTurn {
                phase: ActivePhase::AwaitingCompaction { pending },
                ..
            }) => Some(pending),
            TurnState::Idle | TurnState::Active(_) | TurnState::Terminal { .. } => None,
        }
    }

    pub(crate) fn pending_tool_call_matches(&self, action_id: &str, call_id: &str) -> bool {
        self.pending_tool_call_id(action_id).as_deref() == Some(call_id)
    }

    pub(crate) fn pending_completion(&self, received_action_id: &str) -> Result<Action, CoreError> {
        let pending_actions = self.pending_actions();
        let pending_action_ids = pending_actions
            .iter()
            .map(|action| action.action_id().to_string())
            .collect::<Vec<_>>();
        if !pending_action_ids
            .iter()
            .any(|action_id| action_id == received_action_id)
        {
            return Err(CoreError::invalid_correlation(
                received_action_id,
                pending_action_ids,
            ));
        }
        pending_actions
            .into_iter()
            .find(|action| {
                action.action_id() == received_action_id
                    && matches!(action, Action::Completion { .. })
            })
            .ok_or_else(|| CoreError::invalid_state(self.command_state()))
    }

    pub(crate) fn pending_tool_call_id(&self, action_id: &str) -> Option<String> {
        self.pending_actions()
            .into_iter()
            .find_map(|action| match action {
                Action::RuntimeBuiltinTool {
                    effect_id,
                    operation_id,
                    ..
                }
                | Action::ProvidedTool {
                    effect_id,
                    operation_id,
                    ..
                } if effect_id == action_id => Some(operation_id),
                Action::Completion { .. }
                | Action::Hook { .. }
                | Action::RuntimeBuiltinTool { .. }
                | Action::ProvidedTool { .. }
                | Action::Filesystem { .. } => None,
            })
    }

    pub(crate) fn is_idempotent_late_interrupt(&self, expected_turn_id: &str) -> bool {
        self.active().is_none() && self.last_finished_turn_id.as_deref() == Some(expected_turn_id)
    }

    pub(crate) fn mark_tool_catalog_changed(&mut self) {
        self.tool_catalog.changed = true;
    }

    pub(crate) fn commit_model_input_changes(&mut self) -> Result<(), CoreError> {
        self.context.commit_revision()?;
        if self.tool_catalog.changed {
            self.tool_catalog.revision =
                self.tool_catalog.revision.checked_add(1).ok_or_else(|| {
                    CoreError::invalid_command("model tool catalog revision overflowed")
                })?;
            self.tool_catalog.changed = false;
        }
        Ok(())
    }

    pub(crate) fn inspection(&self) -> StateInspection {
        let status = match &self.turn {
            TurnState::Idle => SessionStatus::Idle,
            TurnState::Compacting { .. } => SessionStatus::Compacting,
            TurnState::Active(_) => SessionStatus::Running,
            TurnState::Terminal { outcome, .. } => match outcome {
                TurnOutcome::Failed { .. } => SessionStatus::Failed,
                TurnOutcome::Completed { .. }
                | TurnOutcome::Rejected { .. }
                | TurnOutcome::Interrupted { .. } => SessionStatus::Completed,
            },
        };
        let pending_actions = self
            .pending_actions()
            .into_iter()
            .map(|action| match action {
                Action::Completion {
                    effect_id, kind, ..
                } => PendingActionInspection::Completion {
                    purpose: match kind.purpose() {
                        crate::core::step_protocol::CompletionPurpose::Agent => {
                            PendingCompletionPurpose::Agent
                        }
                        crate::core::step_protocol::CompletionPurpose::Compaction => {
                            PendingCompletionPurpose::Compaction
                        }
                    },
                    action_id: effect_id,
                },
                Action::RuntimeBuiltinTool {
                    effect_id,
                    operation_id,
                    call,
                    ..
                } => PendingActionInspection::RuntimeBuiltinToolCall {
                    action_id: effect_id,
                    call_id: operation_id,
                    name: call.name,
                },
                Action::ProvidedTool {
                    effect_id,
                    operation_id,
                    call,
                    ..
                } => PendingActionInspection::ProvidedToolCall {
                    action_id: effect_id,
                    call_id: operation_id,
                    group_name: call.group_name,
                    tool_name: call.tool_name,
                },
                Action::Hook {
                    effect_id,
                    hook_binding_ids,
                    call,
                    ..
                } => PendingActionInspection::HookCall {
                    action_id: effect_id,
                    hook: call.point(),
                    hook_binding_ids,
                },
                Action::Filesystem {
                    effect_id,
                    operation: FilesystemOperation::Write { workspace_path, .. },
                    ..
                } => PendingActionInspection::Filesystem {
                    action_id: effect_id,
                    operation: PendingFilesystemOperationInspection::Write { workspace_path },
                },
            })
            .collect();
        StateInspection {
            status,
            active_turn_id: self.active_turn_id().map(str::to_string),
            last_turn_id: self.last_finished_turn_id.clone(),
            pending_actions,
            message_count: self.context.message_count(),
        }
    }

    pub(crate) fn turn_view(&self, completed: Option<TurnCompletion>) -> Result<Turn, CoreError> {
        if !self.pending_actions().is_empty() {
            return match &self.turn {
                TurnState::Active(active) => Ok(Turn::Running {
                    turn_id: active.turn_id.clone(),
                }),
                TurnState::Compacting { pending } => Ok(Turn::Compacting {
                    trigger: pending.trigger(),
                }),
                TurnState::Idle | TurnState::Terminal { .. } => Err(CoreError::invariant(
                    "session has pending actions but no pending turn state",
                )),
            };
        }
        if let Some(TurnCompletion { turn_id, outcome }) = completed {
            return Ok(terminal_turn(turn_id, outcome));
        }
        Ok(match &self.turn {
            TurnState::Idle => Turn::Idle,
            TurnState::Compacting { pending } => Turn::Compacting {
                trigger: pending.trigger(),
            },
            TurnState::Active(active) => Turn::Running {
                turn_id: active.turn_id.clone(),
            },
            TurnState::Terminal { turn_id, outcome } => {
                terminal_turn(turn_id.clone(), outcome.clone())
            }
        })
    }

    /// Moves the active turn to a terminal outcome.
    ///
    /// Steering accepted for the turn is a real user message that clients have
    /// already shown. A turn that ends before its next model call -- an
    /// interruption, a failure, or the iteration cap -- would otherwise drop it
    /// with the active turn, leaving the user looking at a message the model
    /// never received. Committing it here keeps model context consistent with
    /// the history the user sees, so the next turn's model call carries it.
    pub(crate) fn finish_turn(&mut self, outcome: TurnOutcome) -> Result<String, CoreError> {
        let turn_id = match std::mem::replace(&mut self.turn, TurnState::Idle) {
            TurnState::Active(mut active) => {
                self.context.extend(active.take_pending_steer());
                active.turn_id
            }
            restored => {
                self.turn = restored;
                return Err(CoreError::invariant(
                    "cannot finish a session without an active turn",
                ));
            }
        };
        self.last_finished_turn_id = Some(turn_id.clone());
        self.turn = TurnState::Terminal {
            turn_id: turn_id.clone(),
            outcome,
        };
        Ok(turn_id)
    }
}

fn terminal_turn(turn_id: String, outcome: TurnOutcome) -> Turn {
    match outcome {
        TurnOutcome::Completed { output } => Turn::Completed { turn_id, output },
        TurnOutcome::Rejected { .. } => Turn::Completed {
            turn_id,
            output: Vec::new(),
        },
        TurnOutcome::Failed { error } => Turn::Failed { turn_id, error },
        TurnOutcome::Interrupted { reason } => Turn::Interrupted { turn_id, reason },
    }
}

fn completion_action(
    state: &HarnessState,
    action_id: String,
    turn_id: Option<String>,
    kind: CompletionActionKind,
    iteration: u32,
) -> Action {
    Action::Completion {
        effect_id: action_id,
        turn_id,
        kind,
        iteration,
        max_iterations: state.config.settings.turn.max_iterations,
        model_input: ModelInputUpdate {
            messages: ModelMessageUpdate::Append {
                base_revision: 0,
                revision: 0,
                messages: Vec::new(),
            },
            tool_catalog: ModelToolCatalogUpdate::Keep { revision: 0 },
        },
    }
}

/// Where the session is in the turn lifecycle.
///
/// This replaces a `Phase` enum that sat beside five independent `Option`s and
/// three phase-dependent `Vec`s. States like "active but missing a turn id", or
/// "completed with both an output and a rejection reason", are no longer
/// representable.
#[derive(Clone, Debug, PartialEq)]
pub(crate) enum TurnState {
    Idle,
    Compacting {
        pending: PendingCompaction,
    },
    Active(ActiveTurn),
    Terminal {
        turn_id: String,
        outcome: TurnOutcome,
    },
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct ActiveTurn {
    pub turn_id: String,
    pub iterations: u32,
    pub phase: ActivePhase,
    /// Steering accepted for this turn but not yet included in a model Action.
    pub pending_steer: Vec<StoredMessage>,
    /// A model-valid assistant or tool-result boundary has been committed, so
    /// pending steering and notifications may be included in the next agent
    /// completion Action.
    pub model_input_ready: bool,
    /// User messages that arrived mid-turn and start the next turn once this
    /// one completes normally. Scoped to the active turn, so terminating a turn
    /// discards them rather than carrying a stale turn id into the next one.
    pub queued: QueuedTurn,
}

/// What the Runtime is currently waiting on for the active turn.
///
/// The shared `Awaiting` prefix is the point: every variant names something the
/// Core has handed out and is blocked on, which is what distinguishes an active
/// turn from a terminal one.
#[allow(clippy::enum_variant_names)]
#[derive(Clone, Debug, PartialEq)]
pub(crate) enum ActivePhase {
    AwaitingCompletion { action_id: String },
    AwaitingCompaction { pending: PendingCompaction },
    AwaitingToolBatch { batch: ToolBatch },
    AwaitingHook { pending: PendingLifecycleHook },
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) enum InterruptionResolution {
    Quiet,
    DiscardCompletion { action_id: String },
    CommitToolMessages { messages: Vec<Message> },
}

impl ActiveTurn {
    pub(crate) fn defer_steer(&mut self, message: StoredMessage) -> Result<(), CoreError> {
        if matches!(
            &self.phase,
            ActivePhase::AwaitingHook {
                pending: PendingLifecycleHook::PreAgentTurn { .. }
            }
        ) {
            return Err(CoreError::invalid_command(
                "cannot steer a turn before its pre-agent hook completes",
            ));
        }
        self.pending_steer.push(message);
        Ok(())
    }

    pub(crate) fn mark_model_input_ready(&mut self) {
        self.model_input_ready = true;
    }

    pub(crate) fn take_model_input_ready(&mut self) -> bool {
        std::mem::replace(&mut self.model_input_ready, false)
    }

    pub(crate) fn take_pending_steer(&mut self) -> Vec<StoredMessage> {
        std::mem::take(&mut self.pending_steer)
    }
}

impl ActivePhase {
    pub(crate) fn interrupt(
        &self,
        interruption_message: &str,
    ) -> Result<InterruptionResolution, CoreError> {
        match self {
            Self::AwaitingCompletion { action_id } => {
                Ok(InterruptionResolution::DiscardCompletion {
                    action_id: action_id.clone(),
                })
            }
            Self::AwaitingCompaction { .. } => Ok(InterruptionResolution::Quiet),
            Self::AwaitingToolBatch { batch, .. } => {
                Ok(InterruptionResolution::CommitToolMessages {
                    messages: batch.interrupted_messages(interruption_message)?,
                })
            }
            Self::AwaitingHook { pending } => match pending.interrupted_completion_action_id() {
                Some(action_id) => Ok(InterruptionResolution::DiscardCompletion {
                    action_id: action_id.to_string(),
                }),
                None => Ok(InterruptionResolution::Quiet),
            },
        }
    }
}

/// A turn's queued follow-up. One variant carries both the identity and the
/// messages, so there is no way to have one without the other.
#[derive(Clone, Debug, Default, PartialEq)]
pub(crate) enum QueuedTurn {
    #[default]
    Empty,
    Pending {
        turn_id: String,
        messages: Vec<StoredMessage>,
    },
}

impl QueuedTurn {
    /// Adds a message to the queued turn, or opens one.
    ///
    /// A queued turn holds exactly one turn identity: a second identity means
    /// the Runtime is queueing two turns at once, which the protocol does not
    /// allow.
    pub(crate) fn enqueue(
        &mut self,
        turn_id: String,
        message: StoredMessage,
    ) -> Result<(), CoreError> {
        match self {
            Self::Empty => {
                *self = Self::Pending {
                    turn_id,
                    messages: vec![message],
                };
                Ok(())
            }
            Self::Pending {
                turn_id: pending_turn_id,
                messages,
            } => {
                if *pending_turn_id != turn_id {
                    return Err(CoreError::invalid_command(format!(
                        "queued turn mismatch: expected {pending_turn_id:?}, got {turn_id:?}"
                    )));
                }
                messages.push(message);
                Ok(())
            }
        }
    }

    pub(crate) fn take(&mut self) -> Option<(String, Vec<StoredMessage>)> {
        match std::mem::take(self) {
            Self::Empty => None,
            Self::Pending { turn_id, messages } => Some((turn_id, messages)),
        }
    }
}

pub(crate) fn hook_binding_ids(
    state: &HarnessState,
    point: HookPoint,
    tool_key: Option<&HookToolKey>,
) -> Vec<String> {
    state.hook_binding_index.select(point, tool_key)
}
