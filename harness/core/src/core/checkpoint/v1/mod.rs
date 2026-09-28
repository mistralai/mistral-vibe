use std::collections::HashSet;

use serde::{Deserialize, Serialize};

use crate::core::config::HarnessConfig;
use crate::core::features::compaction::CompactionBudget;
use crate::core::hooks::HookPoint;
use crate::core::hooks::hook_action_id;
use crate::core::model_context::ModelContext;
use crate::core::state::{ActivePhase, HarnessState, TurnState};
use crate::core::step_protocol::Action;
use crate::core::tools::execution::{ToolBatch, ToolExecution, ToolExecutionState};
use crate::core::turn::{PendingLifecycleHook, SuspendedTurn};
use crate::core::wire::message::tool_calls_from_parts;
use crate::core::wire::message::{Message, StoredMessageSource};
use crate::core::wire::tool::ToolCall;

use self::context::CheckpointContext;
use self::notification::CheckpointNotifications;
use self::turn::CheckpointTurnState;

mod context;
mod notification;
mod program;
mod schema;
mod tool;
mod turn;

pub(super) const CHECKPOINT_VERSION: u8 = 1;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub(super) struct CheckpointV1 {
    #[serde(rename = "checkpoint_version")]
    _checkpoint_version: u8,
    context: CheckpointContext,
    compaction_count: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    last_reported_context_tokens: Option<u64>,
    turn: CheckpointTurnState,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    last_finished_turn_id: Option<String>,
    #[serde(default)]
    notifications: CheckpointNotifications,
}

impl CheckpointV1 {
    pub(super) fn capture(state: &HarnessState) -> Result<Self, String> {
        validate_restored_state(state)?;
        Ok(Self {
            _checkpoint_version: CHECKPOINT_VERSION,
            context: CheckpointContext::capture(&state.context)?,
            compaction_count: state.compaction_count,
            last_reported_context_tokens: state.last_reported_context_tokens,
            turn: CheckpointTurnState::capture(&state.turn)?,
            last_finished_turn_id: state.last_finished_turn_id.clone(),
            notifications: CheckpointNotifications::capture(&state.notifications),
        })
    }

    pub(super) fn restore(self, config: HarnessConfig) -> Result<HarnessState, String> {
        let mut state =
            crate::core::initial_state(config).map_err(|error| error.detail().to_string())?;
        state.context = self.context.restore(
            &state.config,
            state.resolved_tools.tool_group_inventory_prompt_section(),
        )?;
        state.compaction_count = self.compaction_count;
        state.last_reported_context_tokens = self.last_reported_context_tokens;
        let generated_system = state.generated_system_message();
        state.turn = self.turn.restore(
            &state.context,
            generated_system,
            state.resolved_tools.top_level_tools(),
            CompactionBudget {
                token_threshold: state.config.settings.context.compaction.token_threshold(),
                image_delivery: state.config.settings.context.image_delivery.compaction,
            },
        )?;
        state.last_finished_turn_id = self.last_finished_turn_id;
        state.notifications = self.notifications.restore()?;
        validate_restored_state(&state)?;
        Ok(state)
    }
}

fn validate_restored_state(state: &HarnessState) -> Result<(), String> {
    if state
        .last_finished_turn_id
        .as_deref()
        .is_some_and(|id| id.trim().is_empty())
    {
        return Err("checkpoint last finished turn ID must not be empty".to_string());
    }
    if let Some(active) = state.active()
        && state.context.is_empty()
        && !matches!(
            active.phase,
            ActivePhase::AwaitingHook {
                pending: PendingLifecycleHook::PreAgentTurn { .. }
            }
        )
    {
        return Err("active checkpoint must contain model context".to_string());
    }
    if let TurnState::Terminal { turn_id, .. } = &state.turn
        && state.last_finished_turn_id.as_deref() != Some(turn_id)
    {
        return Err("terminal checkpoint last turn identity does not match".to_string());
    }
    if let Some(active) = state.active() {
        let mut pending_action_ids = HashSet::new();
        match &active.phase {
            ActivePhase::AwaitingCompletion { action_id } => {
                insert_pending_action_id(&mut pending_action_ids, action_id)?;
            }
            ActivePhase::AwaitingCompaction { pending } => {
                let Some((turn_id, iterations)) = pending.in_turn_identity() else {
                    return Err(
                        "active compaction contains a between-turn continuation".to_string()
                    );
                };
                if turn_id != active.turn_id || iterations != active.iterations {
                    return Err(
                        "active compaction continuation does not match its active turn".to_string(),
                    );
                }
                insert_pending_action_id(&mut pending_action_ids, pending.action_id())?;
            }
            ActivePhase::AwaitingHook { pending } => {
                validate_hook_binding_ids(pending.hook_binding_ids())?;
                insert_pending_action_id(&mut pending_action_ids, pending.action_id())?;
                if let PendingLifecycleHook::PreAgentTurn { suspended, .. } = pending
                    && let SuspendedTurn::Terminal { turn_id, .. } = suspended
                    && state.last_finished_turn_id.as_deref() != Some(turn_id)
                {
                    return Err(
                        "pre-agent previous turn does not match the last finished turn".to_string(),
                    );
                }
            }
            ActivePhase::AwaitingToolBatch { batch, .. } => {
                validate_tool_batch_context(state, batch)?;
                for execution in &batch.executions {
                    collect_tool_execution_action_ids(
                        execution,
                        &active.turn_id,
                        &mut pending_action_ids,
                    )?;
                }
                if pending_action_ids.is_empty() {
                    return Err(
                        "active tool batch checkpoint must contain pending work".to_string()
                    );
                }
            }
        }
    }
    Ok(())
}

fn validate_tool_batch_context(state: &HarnessState, batch: &ToolBatch) -> Result<(), String> {
    let calls = tool_batch_calls_from_context(&state.context)?;
    if calls.len() != batch.executions.len()
        || calls
            .iter()
            .zip(&batch.executions)
            .any(|(call, execution)| call != &execution.call)
    {
        return Err("active tool batch does not match its assistant tool-call message".to_string());
    }
    Ok(())
}

fn tool_batch_calls_from_context(context: &ModelContext) -> Result<Vec<ToolCall>, String> {
    let Some(stored) = context.messages().last() else {
        return Err("active tool batch checkpoint is missing its assistant message".to_string());
    };
    let Message::Assistant { content } = &stored.message else {
        return Err(
            "active tool batch checkpoint must follow its assistant tool-call message".to_string(),
        );
    };
    if stored.source != StoredMessageSource::History {
        return Err("active tool batch assistant message must be historical".to_string());
    }
    Ok(tool_calls_from_parts(content))
}

fn collect_tool_execution_action_ids(
    execution: &ToolExecution,
    turn_id: &str,
    ids: &mut HashSet<String>,
) -> Result<(), String> {
    match &execution.state {
        ToolExecutionState::DirectAwaitingPreHook {
            hook_action_id,
            hook_binding_ids,
            ..
        }
        | ToolExecutionState::DirectAwaitingPostHook {
            hook_action_id,
            hook_binding_ids,
            ..
        } => {
            validate_hook_binding_ids(hook_binding_ids)?;
            insert_pending_action_id(ids, hook_action_id)
        }
        ToolExecutionState::DirectPending { call } => {
            insert_pending_action_id(ids, &call.action_id)
        }
        ToolExecutionState::ProgramPending { execution } => {
            let actions = execution.pending_actions(turn_id);
            if actions.is_empty() {
                return Err(
                    "pending program checkpoint must contain at least one operation".to_string(),
                );
            }
            for action in actions {
                match action {
                    Action::Hook {
                        effect_id,
                        hook_binding_ids,
                        ..
                    } => {
                        validate_hook_binding_ids(&hook_binding_ids)?;
                        insert_pending_action_id(ids, &effect_id)?;
                    }
                    Action::RuntimeBuiltinTool { effect_id, .. }
                    | Action::ProvidedTool { effect_id, .. } => {
                        insert_pending_action_id(ids, &effect_id)?;
                    }
                    Action::Completion { .. } => {
                        return Err(
                            "pending program checkpoint cannot contain a completion action"
                                .to_string(),
                        );
                    }
                    Action::Filesystem { .. } => {
                        return Err(
                            "pending program checkpoint cannot contain a filesystem action"
                                .to_string(),
                        );
                    }
                }
            }
            Ok(())
        }
        ToolExecutionState::AwaitingLargeOutputWrite { action_id, .. } => {
            insert_pending_action_id(ids, action_id)
        }
        ToolExecutionState::Completed { .. } => Ok(()),
    }
}

fn insert_pending_action_id(ids: &mut HashSet<String>, id: &str) -> Result<(), String> {
    if id.trim().is_empty() {
        return Err("pending checkpoint action ID must not be empty".to_string());
    }
    if !ids.insert(id.to_string()) {
        return Err(format!("duplicate pending checkpoint action ID {id:?}"));
    }
    Ok(())
}

fn validate_hook_binding_ids(ids: &[String]) -> Result<(), String> {
    if ids.is_empty() {
        return Err("pending checkpoint hook must contain at least one binding ID".to_string());
    }
    let mut unique = HashSet::new();
    for id in ids {
        if id.trim().is_empty() || !unique.insert(id) {
            return Err(
                "pending checkpoint hook binding IDs must be non-empty and unique".to_string(),
            );
        }
    }
    Ok(())
}

pub(super) fn validate_derived_hook_action_id(
    action_id: &str,
    subject_action_id: &str,
    point: HookPoint,
) -> Result<(), String> {
    let expected = hook_action_id(subject_action_id, point);
    if action_id != expected {
        return Err(format!(
            "checkpoint hook action ID {action_id:?} does not match derived action ID {expected:?}"
        ));
    }
    Ok(())
}
