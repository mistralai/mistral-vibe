use crate::core::checkpoint::Checkpoint;
use crate::core::config::HarnessConfig;
use crate::core::conversation::ConversationSeed;
use crate::core::error::CoreError;
use crate::core::state::{ActivePhase, ActiveTurn, HarnessState, StateInspection, TurnState};
use crate::core::step_protocol::Action;
use crate::core::step_protocol::{
    HarnessApplyResult, HarnessCommand, HarnessCommandRejection, HarnessInput, Inspection,
    STEP_PROTOCOL_VERSION, SessionTransition, invalid_command,
};
use crate::core::wire::message::{Message, StoredMessage};
use crate::core::{advance_in_place, initial_state_with_history};

mod delivery;

use delivery::{CompactionInputSource, ModelInputSource, PreparedInput, StepDelivery};

#[derive(Clone)]
pub(crate) struct HarnessSession {
    state: HarnessState,
    delivery: StepDelivery,
}

impl HarnessSession {
    #[cfg(test)]
    pub(crate) fn create(config: HarnessConfig) -> Result<Self, String> {
        Self::create_with_history(config, Vec::new())
    }

    #[cfg(any(test, feature = "benchmark"))]
    pub(crate) fn create_with_history(
        config: HarnessConfig,
        initial_history: Vec<Message>,
    ) -> Result<Self, String> {
        Self::create_with_conversation(config, initial_history, None)
    }

    pub(crate) fn create_with_conversation(
        config: HarnessConfig,
        initial_history: Vec<Message>,
        conversation: Option<ConversationSeed>,
    ) -> Result<Self, String> {
        if conversation.is_some() && !initial_history.is_empty() {
            return Err("initial history and conversation seed cannot both be supplied".into());
        }
        let imported = conversation.map(ConversationSeed::import).transpose()?;
        let initial_history = imported.as_ref().map_or(initial_history, |messages| {
            messages.iter().map(|item| item.message.clone()).collect()
        });
        let mut state = initial_state_with_history(config, initial_history)
            .map_err(|error| error.detail().to_string())?;
        if let Some(imported) = imported {
            let mut messages = state.context.messages().to_vec();
            messages.retain(StoredMessage::is_generated_system);
            messages.extend(imported);
            state.context = crate::core::model_context::ModelContext::new(messages);
        }
        Ok(Self::from_state(state))
    }

    pub(crate) fn export_conversation(
        &self,
        context: crate::core::SpawnContext,
    ) -> Result<ConversationSeed, String> {
        let finished_results = match &self.state.turn {
            TurnState::Active(ActiveTurn {
                phase: ActivePhase::AwaitingToolBatch { batch },
                ..
            }) => batch.completed_messages(),
            _ => Vec::new(),
        };
        crate::core::conversation::export(
            self.state.context.messages(),
            context,
            &finished_results,
            &self
                .state
                .config
                .settings
                .prompts
                .agent_messages
                .inherited_pending_call,
        )
    }

    /// Restores like [`Self::restore_moved_from`], without a move.
    #[cfg(test)]
    pub(crate) fn restore(
        config: HarnessConfig,
        checkpoint: Checkpoint,
        resume_input_id: u64,
    ) -> Result<Self, String> {
        Self::restore_moved_from(config, checkpoint, resume_input_id, None)
    }

    /// Restores Core continuation state and resumes the Runtime's input sequence.
    ///
    /// The checkpoint carries no delivery state, so `resume_input_id` is the last
    /// input ID the Runtime durably accepted for this session. Zero starts a
    /// fresh sequence.
    ///
    /// A Runtime that restores a checkpoint under another configuration than the
    /// one it was taken under passes that one as `moved_from`. The Session then
    /// moves by the rule a reconfiguration applies, before it fits any pending
    /// work: new model facts reset the estimate correction, and are refused when
    /// the next Turn could not carry the context under them.
    pub(crate) fn restore_moved_from(
        config: HarnessConfig,
        checkpoint: Checkpoint,
        resume_input_id: u64,
        moved_from: Option<&HarnessConfig>,
    ) -> Result<Self, String> {
        Ok(Self {
            state: checkpoint.restore_moved_from(
                config,
                moved_from.map(|previous| &previous.settings.context),
            )?,
            delivery: StepDelivery::resumed(resume_input_id)?,
        })
    }

    fn from_state(state: HarnessState) -> Self {
        Self {
            state,
            delivery: StepDelivery::default(),
        }
    }

    pub(crate) fn apply(&mut self, input: HarnessInput) -> HarnessApplyResult {
        let mut candidate = self.clone();
        match candidate.apply_accepted(input) {
            Ok(transition) => {
                *self = candidate;
                HarnessApplyResult::Accepted {
                    transition: Box::new(transition),
                }
            }
            Err(rejection) => HarnessApplyResult::Rejected { rejection },
        }
    }

    fn apply_accepted(
        &mut self,
        input: HarnessInput,
    ) -> Result<SessionTransition, HarnessCommandRejection> {
        let state = &self.state;
        let prepared = self.delivery.prepare(input, |command| {
            crate::core::router::normalize_command(state, command);
        })?;
        let (receipt, command, determinism) = match prepared {
            PreparedInput::Replay(transition) => return Ok(transition),
            PreparedInput::Fresh {
                receipt,
                command,
                determinism,
            } => (receipt, command, determinism),
        };
        let command_type = command.command_type();

        let result = match command {
            HarnessCommand::CompletionModelInputResyncRequested { action_id } => {
                let action = self.current_completion_action(&action_id)?;
                let turn = self.state.turn_view().map_err(|error| {
                    core_rejection(error, "completion_model_input_resync_requested")
                })?;
                let settings = &self.state.config.settings;
                let source = ModelInputSource {
                    context: &self.state.context,
                    compaction: self.state.pending_compaction().map(|pending| {
                        CompactionInputSource {
                            action_id: pending.action_id(),
                            messages: pending.projection().model_messages(),
                        }
                    }),
                    image_delivery: settings.context.effective_image_delivery(),
                    tools: self.state.resolved_tools.top_level_tools(),
                    tool_catalog_revision: self.state.tool_catalog.revision,
                };
                return self.delivery.finish_resync(receipt, action, turn, source);
            }
            command => advance_in_place(&mut self.state, command, determinism),
        };
        match result {
            Ok(transition) => {
                let turn = self
                    .state
                    .turn_view()
                    .map_err(|error| invalid_command(error.detail()))?;
                let pending_action_ids = self
                    .state
                    .pending_actions()
                    .into_iter()
                    .map(|action| action.action_id().to_string())
                    .collect();
                let settings = &self.state.config.settings;
                let source = ModelInputSource {
                    context: &self.state.context,
                    compaction: self.state.pending_compaction().map(|pending| {
                        CompactionInputSource {
                            action_id: pending.action_id(),
                            messages: pending.projection().model_messages(),
                        }
                    }),
                    image_delivery: settings.context.effective_image_delivery(),
                    tools: self.state.resolved_tools.top_level_tools(),
                    tool_catalog_revision: self.state.tool_catalog.revision,
                };
                self.delivery
                    .finish(receipt, transition, pending_action_ids, turn, source)
            }
            Err(error) => Err(core_rejection(error, command_type)),
        }
    }

    fn current_completion_action(
        &self,
        received_action_id: &str,
    ) -> Result<Action, HarnessCommandRejection> {
        self.state
            .pending_completion(received_action_id)
            .map_err(|error| core_rejection(error, "completion_model_input_resync_requested"))
    }

    pub(crate) fn inspect(&self) -> Inspection {
        let StateInspection {
            has_pending_model_input,
            status,
            active_turn_id,
            last_turn_id,
            pending_actions,
            message_count,
        } = self.state.inspection();
        Inspection {
            has_pending_model_input,
            protocol_version: STEP_PROTOCOL_VERSION,
            status,
            active_turn_id,
            last_turn_id,
            pending_actions,
            message_count,
            context_revision: self.state.context.revision(),
            tool_catalog_revision: self.state.tool_catalog.revision,
            last_input_id: self.delivery.last_input_id(),
        }
    }

    pub(crate) fn checkpoint(&self) -> Result<Checkpoint, String> {
        Checkpoint::capture(&self.state)
    }
}

/// Renders a Core failure as a Session Protocol rejection.
///
/// Core owns state and action-correlation decisions. This interface adds the
/// wire command name and maps all other Core failures to `invalid_command`.
fn core_rejection(error: CoreError, command_type: &'static str) -> HarnessCommandRejection {
    match error {
        CoreError::QueueFull => HarnessCommandRejection::QueueFull,
        CoreError::InvalidState { state } => HarnessCommandRejection::InvalidState {
            command_type: command_type.to_string(),
            state,
        },
        CoreError::InvalidCorrelation {
            received_action_id,
            pending_action_ids,
        } => HarnessCommandRejection::InvalidCorrelation {
            received_action_id,
            pending_action_ids,
        },
        CoreError::InvalidCommand { detail }
        | CoreError::InvalidConfiguration { detail, .. }
        | CoreError::Invariant { detail } => invalid_command(detail),
    }
}

#[cfg(test)]
mod tests;
