use std::collections::BTreeMap;

use crate::core::config::ImageDeliverySettings;
use crate::core::model_context::{ModelContext, ModelContextCursor};
use crate::core::model_input_projection::project_model_input_messages;
use crate::core::step_protocol::DeterminismContext;
use crate::core::step_protocol::Transition;
use crate::core::step_protocol::{
    Action, CompletionPurpose, ModelInputUpdate, ModelMessageUpdate, ModelToolCatalogUpdate,
    ToolDefinition,
};
use crate::core::step_protocol::{
    HarnessActionDirective, HarnessCommand, HarnessCommandRejection, HarnessInput,
    HarnessNextAction, STEP_PROTOCOL_VERSION, SessionTransition, Turn, invalid_command,
};
use crate::core::wire::message::StoredMessage;

const MAX_SAFE_JSON_INTEGER: u64 = 9_007_199_254_740_991;

#[derive(Clone)]
struct InputReceipt {
    input_id: u64,
    input_json: String,
    transition: SessionTransition,
}

#[derive(Clone, Default)]
pub(super) struct StepDelivery {
    last_input_id: u64,
    last_receipt: Option<InputReceipt>,
    model_context_cursor: Option<ModelContextCursor>,
    tool_catalog_revision: Option<u64>,
}

pub(super) enum PreparedInput {
    Replay(SessionTransition),
    Fresh {
        receipt: PendingReceipt,
        command: HarnessCommand,
        determinism: DeterminismContext,
    },
}

pub(super) struct PendingReceipt {
    input_id: u64,
    input_json: String,
}

#[derive(Clone, Copy)]
pub(super) struct ModelInputSource<'a> {
    pub(super) context: &'a ModelContext,
    pub(super) compaction: Option<CompactionInputSource<'a>>,
    pub(super) image_delivery: ImageDeliverySettings,
    pub(super) tools: &'a [ToolDefinition],
    pub(super) tool_catalog_revision: u64,
}

#[derive(Clone, Copy)]
pub(super) struct CompactionInputSource<'a> {
    pub(super) action_id: &'a str,
    pub(super) messages: &'a [StoredMessage],
}

impl StepDelivery {
    /// Resumes the accepted-input sequence a previous resident session reached.
    ///
    /// Only the cursor carries over. Receipts and model-input caches stay
    /// resident transport state that the checkpoint deliberately omits, so a
    /// resumed session replays nothing and still opens with a full model-input
    /// replacement. The Runtime supplies the cursor from its own durable
    /// records, which is where ADR 0002 puts accepted-command receipts.
    pub(super) fn resumed(last_input_id: u64) -> Result<Self, String> {
        if last_input_id > MAX_SAFE_JSON_INTEGER {
            return Err("resumed input ID must be a JSON safe integer".to_string());
        }
        Ok(Self {
            last_input_id,
            ..Self::default()
        })
    }

    pub(super) fn prepare(
        &self,
        mut input: HarnessInput,
        normalize: impl FnOnce(&mut HarnessCommand),
    ) -> Result<PreparedInput, HarnessCommandRejection> {
        if input.protocol_version != STEP_PROTOCOL_VERSION {
            return Err(invalid_command(format!(
                "unsupported harness input version {}",
                input.protocol_version
            )));
        }
        if input.determinism.time_unix_ms > MAX_SAFE_JSON_INTEGER {
            return Err(invalid_command(
                "determinism time_unix_ms must be a JSON safe integer",
            ));
        }

        normalize(&mut input.command);
        let input_json = serde_json::to_string(&(&input.determinism, &input.command))
            .map_err(|error| invalid_command(error.to_string()))?;
        if let Some(receipt) = &self.last_receipt
            && receipt.input_id == input.input_id
        {
            if receipt.input_json == input_json {
                return Ok(PreparedInput::Replay(receipt.transition.clone()));
            }
            return Err(HarnessCommandRejection::InputConflict {
                input_id: input.input_id,
            });
        }
        if input.input_id <= self.last_input_id {
            return Err(HarnessCommandRejection::StaleInput {
                received_input_id: input.input_id,
                last_accepted_input_id: self.last_input_id,
            });
        }
        // A resumed cursor no longer restarts at zero once per generation, so the
        // sequence now has a session lifetime. Bound it where the wire does: the
        // next ID must stay a JSON safe integer.
        let expected_input_id = self
            .last_input_id
            .checked_add(1)
            .filter(|next| *next <= MAX_SAFE_JSON_INTEGER)
            .ok_or_else(|| invalid_command("input ID sequence is exhausted"))?;
        if input.input_id != expected_input_id {
            return Err(HarnessCommandRejection::OutOfOrderInput {
                received_input_id: input.input_id,
                expected_input_id,
            });
        }

        Ok(PreparedInput::Fresh {
            receipt: PendingReceipt {
                input_id: input.input_id,
                input_json,
            },
            command: input.command,
            determinism: input.determinism,
        })
    }

    pub(super) fn finish(
        &mut self,
        receipt: PendingReceipt,
        mut transition: Transition,
        pending_action_ids: Vec<String>,
        turn: Turn,
        source: ModelInputSource<'_>,
    ) -> Result<SessionTransition, HarnessCommandRejection> {
        self.synchronize_model_input(&mut transition.effects, source)
            .map_err(invalid_command)?;
        let next = next_action(transition.effects, pending_action_ids).map_err(invalid_command)?;
        let transition = SessionTransition {
            protocol_version: STEP_PROTOCOL_VERSION,
            input_id: receipt.input_id,
            next,
            observations: transition.observations,
            turn,
        };
        Ok(self.commit(receipt, transition))
    }

    pub(super) fn finish_resync(
        &mut self,
        receipt: PendingReceipt,
        mut action: Action,
        turn: Turn,
        source: ModelInputSource<'_>,
    ) -> Result<SessionTransition, HarnessCommandRejection> {
        let Action::Completion {
            effect_id,
            kind,
            model_input,
            ..
        } = &mut action
        else {
            unreachable!("completion action pattern was matched")
        };
        let action_id = effect_id.clone();
        let purpose = kind.purpose();
        self.replace_model_input(&action_id, purpose, model_input, source)
            .map_err(invalid_command)?;
        let transition = SessionTransition {
            protocol_version: STEP_PROTOCOL_VERSION,
            input_id: receipt.input_id,
            next: HarnessNextAction::Actions {
                directives: vec![HarnessActionDirective::Refresh { action }],
            },
            observations: Vec::new(),
            turn,
        };
        Ok(self.commit(receipt, transition))
    }

    pub(super) fn last_input_id(&self) -> u64 {
        self.last_input_id
    }

    fn commit(
        &mut self,
        receipt: PendingReceipt,
        transition: SessionTransition,
    ) -> SessionTransition {
        self.last_input_id = receipt.input_id;
        self.last_receipt = Some(InputReceipt {
            input_id: receipt.input_id,
            input_json: receipt.input_json,
            transition: transition.clone(),
        });
        transition
    }

    fn synchronize_model_input(
        &mut self,
        actions: &mut [Action],
        source: ModelInputSource<'_>,
    ) -> Result<(), String> {
        let mut updated = false;
        let mut used_compaction_projection = false;
        for action in actions {
            let Action::Completion {
                effect_id,
                kind,
                model_input,
                ..
            } = action
            else {
                continue;
            };
            model_input.messages = match kind.purpose() {
                CompletionPurpose::Agent => source
                    .context
                    .message_update_since(self.model_context_cursor, source.image_delivery.agent),
                CompletionPurpose::Compaction => {
                    let projection = source.compaction.ok_or_else(|| {
                        "pending compaction action has no model-input projection".to_string()
                    })?;
                    if projection.action_id != effect_id {
                        return Err("pending compaction projection does not match its action ID"
                            .to_string());
                    }
                    used_compaction_projection = true;
                    ModelMessageUpdate::Replace {
                        revision: source.context.revision(),
                        messages: project_model_input_messages(
                            projection.messages,
                            source.image_delivery.compaction,
                        ),
                    }
                }
            };
            model_input.tool_catalog =
                if self.tool_catalog_revision == Some(source.tool_catalog_revision) {
                    ModelToolCatalogUpdate::Keep {
                        revision: source.tool_catalog_revision,
                    }
                } else {
                    ModelToolCatalogUpdate::Replace {
                        revision: source.tool_catalog_revision,
                        tools: source.tools.to_vec(),
                    }
                };
            updated = true;
        }
        if updated {
            self.model_context_cursor =
                (!used_compaction_projection).then(|| source.context.cursor());
            self.tool_catalog_revision = Some(source.tool_catalog_revision);
        }
        Ok(())
    }

    fn replace_model_input(
        &mut self,
        action_id: &str,
        purpose: CompletionPurpose,
        model_input: &mut ModelInputUpdate,
        source: ModelInputSource<'_>,
    ) -> Result<(), String> {
        let messages = match purpose {
            CompletionPurpose::Agent => source.context.replace_all(source.image_delivery.agent),
            CompletionPurpose::Compaction => {
                let projection = source.compaction.ok_or_else(|| {
                    "pending compaction action has no model-input projection".to_string()
                })?;
                if projection.action_id != action_id {
                    return Err(
                        "pending compaction projection does not match its action ID".to_string()
                    );
                }
                ModelMessageUpdate::Replace {
                    revision: source.context.revision(),
                    messages: project_model_input_messages(
                        projection.messages,
                        source.image_delivery.compaction,
                    ),
                }
            }
        };
        *model_input = ModelInputUpdate {
            messages,
            tool_catalog: ModelToolCatalogUpdate::Replace {
                revision: source.tool_catalog_revision,
                tools: source.tools.to_vec(),
            },
        };
        self.model_context_cursor =
            matches!(purpose, CompletionPurpose::Agent).then(|| source.context.cursor());
        self.tool_catalog_revision = Some(source.tool_catalog_revision);
        Ok(())
    }
}

fn next_action(
    actions: Vec<Action>,
    pending_action_ids: Vec<String>,
) -> Result<HarnessNextAction, String> {
    if pending_action_ids.is_empty() {
        if actions.is_empty() {
            return Ok(HarnessNextAction::None);
        }
        return Err("transition emitted actions while the Core has no pending action".to_string());
    }
    let mut new_actions = BTreeMap::new();
    for action in actions {
        let action_id = action.action_id().to_string();
        if new_actions.insert(action_id.clone(), action).is_some() {
            return Err(format!(
                "transition emitted duplicate action ID {action_id:?}"
            ));
        }
    }
    let mut directives = Vec::with_capacity(pending_action_ids.len());
    for action_id in pending_action_ids {
        directives.push(match new_actions.remove(&action_id) {
            Some(action) => HarnessActionDirective::Dispatch { action },
            None => HarnessActionDirective::Keep { action_id },
        });
    }
    if let Some((action_id, _)) = new_actions.into_iter().next() {
        return Err(format!(
            "transition emitted action {action_id:?} that is absent from Core state"
        ));
    }
    Ok(HarnessNextAction::Actions { directives })
}
