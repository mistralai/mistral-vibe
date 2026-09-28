use crate::core::checkpoint::Checkpoint;
use crate::core::config::HarnessConfig;
use crate::core::error::CoreError;
use crate::core::state::{HarnessState, StateInspection};
use crate::core::step_protocol::Action;
use crate::core::step_protocol::{
    HarnessApplyResult, HarnessCommand, HarnessCommandRejection, HarnessInput, Inspection,
    STEP_PROTOCOL_VERSION, SessionTransition, invalid_command,
};
use crate::core::wire::message::Message;
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

    pub(crate) fn create_with_history(
        config: HarnessConfig,
        initial_history: Vec<Message>,
    ) -> Result<Self, String> {
        let state = initial_state_with_history(config, initial_history)
            .map_err(|error| error.detail().to_string())?;
        Ok(Self::from_state(state))
    }

    /// Restores Core continuation state and resumes the Runtime's input sequence.
    ///
    /// The checkpoint carries no delivery state, so `resume_input_id` is the last
    /// input ID the Runtime durably accepted for this session. Zero starts a
    /// fresh sequence.
    pub(crate) fn restore(
        config: HarnessConfig,
        checkpoint: Checkpoint,
        resume_input_id: u64,
    ) -> Result<Self, String> {
        Ok(Self {
            state: checkpoint.restore(config)?,
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
                let turn = self.state.turn_view(None).map_err(|error| {
                    core_rejection(error, "completion_model_input_resync_requested")
                })?;
                let source = ModelInputSource {
                    context: &self.state.context,
                    compaction: self.state.pending_compaction().map(|pending| {
                        CompactionInputSource {
                            action_id: pending.action_id(),
                            messages: pending.projection().messages(),
                        }
                    }),
                    image_delivery: self.state.config.settings.context.image_delivery,
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
                    .turn_view(transition.completed.clone())
                    .map_err(|error| invalid_command(error.detail()))?;
                let pending_action_ids = self
                    .state
                    .pending_actions()
                    .into_iter()
                    .map(|action| action.action_id().to_string())
                    .collect();
                let source = ModelInputSource {
                    context: &self.state.context,
                    compaction: self.state.pending_compaction().map(|pending| {
                        CompactionInputSource {
                            action_id: pending.action_id(),
                            messages: pending.projection().messages(),
                        }
                    }),
                    image_delivery: self.state.config.settings.context.image_delivery,
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
            status,
            active_turn_id,
            last_turn_id,
            pending_actions,
            message_count,
        } = self.state.inspection();
        Inspection {
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
mod tests {
    use serde_json::json;

    use super::*;
    use crate::core::UserMessageMode;
    use crate::core::config::{ProvidedToolDefinition, ProvidedToolExposure, ToolGroupDefinition};
    use crate::core::step_protocol::{
        DeterminismContext, HarnessActionDirective, HarnessApplyResult, HarnessConfigurationChange,
        HarnessInput, HarnessNextAction, ModelMessageUpdate, ModelToolCatalogUpdate,
    };
    use crate::core::wire::content::ContentBlock;

    fn input(input_id: u64, command: HarnessCommand) -> HarnessInput {
        HarnessInput {
            protocol_version: STEP_PROTOCOL_VERSION,
            input_id,
            determinism: DeterminismContext {
                time_unix_ms: 1_700_000_000_000 + input_id,
                random_seed: input_id as u32,
            },
            command,
        }
    }

    ///
    /// *Prepare*: A session with model context has an exhausted tool-catalog revision.
    /// *Do*: Reconfigure its capabilities so Core must commit a changed tool catalog.
    /// *Assert*: The command is rejected without committing candidate state or consuming the input.
    ///
    #[test]
    fn tool_catalog_revision_overflow_rejects_without_committing_candidate() {
        // Prepare
        let mut session = HarnessSession::create(crate::core::testing::config()).unwrap();
        assert!(matches!(
            session.apply(input(
                1,
                HarnessCommand::ContextMessage {
                    content: vec![ContentBlock::text("background")],
                },
            )),
            HarnessApplyResult::Accepted { .. }
        ));
        session.state.tool_catalog.revision = u64::MAX;
        let previous_state = session.state.clone();
        let previous_inspection = session.inspect();
        let mut capabilities = session.state.config.capabilities.clone();
        capabilities.tool_groups.push(ToolGroupDefinition {
            name: "workspace".to_string(),
            description: "Workspace tools".to_string(),
            metadata: None,
            icon_url: None,
            tools: vec![ProvidedToolDefinition {
                name: "lookup".to_string(),
                description: "Lookup".to_string(),
                input_schema: json!({ "type": "object" }),
                output_schema: None,
                exposure: ProvidedToolExposure::Direct,
            }],
        });

        // Do
        let result = session.apply(input(
            2,
            HarnessCommand::Reconfigure {
                changes: vec![HarnessConfigurationChange::Capabilities {
                    value: capabilities,
                }],
            },
        ));

        // Assert
        let HarnessApplyResult::Rejected {
            rejection: HarnessCommandRejection::InvalidCommand { error },
        } = result
        else {
            panic!("expected an invalid-command rejection, got {result:?}")
        };
        assert_eq!(error.message, "model tool catalog revision overflowed");
        assert_eq!(session.state, previous_state);
        assert_eq!(session.inspect(), previous_inspection);
    }

    fn context_message(text: &str) -> HarnessCommand {
        HarnessCommand::ContextMessage {
            content: vec![ContentBlock::text(text)],
        }
    }

    fn checkpoint_after_two_inputs(config: HarnessConfig) -> Checkpoint {
        let mut session = HarnessSession::create(config).unwrap();
        for input_id in 1..=2 {
            assert!(matches!(
                session.apply(input(input_id, context_message("background"))),
                HarnessApplyResult::Accepted { .. }
            ));
        }
        session.checkpoint().unwrap()
    }

    ///
    /// *Prepare*: A session accepted two inputs and produced a checkpoint the Runtime stored beside its own input cursor.
    /// *Do*: Restore that checkpoint at the recorded cursor and send both a consumed ID and the next one.
    /// *Assert*: The restored session reports the cursor, rejects what the Runtime already consumed, and continues the sequence.
    ///
    #[test]
    fn restore_resumes_the_input_sequence_the_runtime_recorded() {
        // Prepare
        let config = crate::core::testing::config();
        let checkpoint = checkpoint_after_two_inputs(config.clone());

        // Do
        let mut restored = HarnessSession::restore(config, checkpoint, 2).unwrap();
        let consumed = restored.apply(input(2, context_message("already accepted")));
        let next = restored.apply(input(3, context_message("resumed")));

        // Assert
        assert!(matches!(
            consumed,
            HarnessApplyResult::Rejected {
                rejection: HarnessCommandRejection::StaleInput {
                    received_input_id: 2,
                    last_accepted_input_id: 2,
                },
            }
        ));
        assert!(matches!(next, HarnessApplyResult::Accepted { .. }));
        assert_eq!(restored.inspect().last_input_id, 3);
    }

    ///
    /// *Prepare*: A checkpoint is restored at a cursor a previous resident session reached.
    /// *Do*: Take the first turn of the resumed session.
    /// *Assert*: The completion carries a full model-input replacement, because the checkpoint restores no delivery caches.
    ///
    #[test]
    fn a_resumed_session_still_opens_with_a_full_model_input_replacement() {
        // Prepare
        let config = crate::core::testing::config();
        let checkpoint = checkpoint_after_two_inputs(config.clone());
        let mut restored = HarnessSession::restore(config, checkpoint, 2).unwrap();

        // Do
        let result = restored.apply(input(
            3,
            HarnessCommand::UserMessage {
                turn_id: "turn-resumed".to_string(),
                content: vec![ContentBlock::text("carry on")],
                mode: UserMessageMode::Queue,
            },
        ));

        // Assert
        let HarnessApplyResult::Accepted { transition } = result else {
            panic!("expected the resumed input to be accepted, got {result:?}")
        };
        let HarnessNextAction::Actions { directives } = &transition.next else {
            panic!("expected the turn to dispatch a completion")
        };
        let [
            HarnessActionDirective::Dispatch {
                action: Action::Completion { model_input, .. },
            },
        ] = directives.as_slice()
        else {
            panic!("expected a single dispatched completion, got {directives:?}")
        };
        assert!(matches!(
            model_input.messages,
            ModelMessageUpdate::Replace { .. }
        ));
        assert!(matches!(
            model_input.tool_catalog,
            ModelToolCatalogUpdate::Replace { .. }
        ));
    }

    ///
    /// *Prepare*: A checkpoint and a resumed cursor one past the largest ID the wire can carry.
    /// *Do*: Restore the checkpoint at that cursor.
    /// *Assert*: The restore fails rather than seating a session whose next ID cannot round-trip through JSON.
    ///
    #[test]
    fn restore_rejects_a_resumed_input_id_outside_the_json_safe_range() {
        // Prepare
        let config = crate::core::testing::config();
        let checkpoint = checkpoint_after_two_inputs(config.clone());

        // Do
        let result = HarnessSession::restore(config, checkpoint, 9_007_199_254_740_992);

        // Assert
        let Err(error) = result else {
            panic!("expected the restore to reject a cursor outside the JSON safe range")
        };
        assert_eq!(error, "resumed input ID must be a JSON safe integer");
    }

    ///
    /// *Prepare*: A session resumed at the largest input ID the wire can carry.
    /// *Do*: Send the input that would follow it.
    /// *Assert*: The sequence reports itself exhausted instead of advancing past a JSON safe integer.
    ///
    #[test]
    fn a_session_resumed_at_the_json_safe_limit_reports_an_exhausted_sequence() {
        // Prepare
        let config = crate::core::testing::config();
        let checkpoint = checkpoint_after_two_inputs(config.clone());
        let mut restored =
            HarnessSession::restore(config, checkpoint, 9_007_199_254_740_991).unwrap();
        let mut overflowing = input(1, context_message("one too many"));
        overflowing.input_id = 9_007_199_254_740_992;

        // Do
        let result = restored.apply(overflowing);

        // Assert
        let HarnessApplyResult::Rejected {
            rejection: HarnessCommandRejection::InvalidCommand { error },
        } = result
        else {
            panic!("expected an invalid-command rejection, got {result:?}")
        };
        assert_eq!(error.message, "input ID sequence is exhausted");
    }
}
