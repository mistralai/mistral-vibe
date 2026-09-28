use serde_json::Value;

use crate::core::error::CoreError;
use crate::core::state::HarnessState;
use crate::core::step_protocol::Action;
use crate::core::step_protocol::Outcome;
use crate::core::step_protocol::configuration_update;
use crate::core::step_protocol::{DeterminismContext, HarnessCommand};
use crate::core::turn::{ReportedToolResult, TurnEvent, dispatch};
use crate::core::wire::tool::{ProtocolError, StructuredContent, ToolResult};

/// Classifies one Step Protocol command and delegates turn lifecycle events.
///
/// `HarnessSession` handles input ordering and transport-only resynchronization.
/// This router retains generic action correlation and reconfiguration admission;
/// the turn coordinator owns state-dependent lifecycle legality and ordering.
pub(crate) fn reduce(
    state: &mut HarnessState,
    command: HarnessCommand,
    determinism: DeterminismContext,
) -> Result<Outcome, CoreError> {
    validate_correlation(state, &command)?;

    match command {
        HarnessCommand::UserMessage {
            turn_id,
            content,
            mode,
        } => dispatch(
            state,
            TurnEvent::UserMessage {
                turn_id,
                content,
                mode,
            },
            determinism,
        ),
        HarnessCommand::ContextMessage { content } => {
            dispatch(state, TurnEvent::ContextMessage { content }, determinism)
        }
        HarnessCommand::Notification { notification } => state.receive_notification(notification),
        HarnessCommand::Compact { extra_instructions } => dispatch(
            state,
            TurnEvent::ManualCompaction { extra_instructions },
            determinism,
        ),
        HarnessCommand::Reconfigure { changes } if state.pending_action_ids().is_empty() => {
            let update = configuration_update(changes).map_err(CoreError::invalid_command)?;
            crate::core::reconfigure_in_place(state, update)?;
            Ok(Outcome::quiet())
        }
        HarnessCommand::Reconfigure { .. } => Err(invalid_state(state)),
        HarnessCommand::CompletionModelInputResyncRequested { .. } => Err(CoreError::invariant(
            "model-input resynchronization must be handled by HarnessSession",
        )),
        HarnessCommand::Interrupt {
            expected_turn_id,
            reason,
        } => dispatch(
            state,
            TurnEvent::Interrupt {
                expected_turn_id,
                reason,
            },
            determinism,
        ),
        HarnessCommand::CompletionSucceeded { action_id, result } => dispatch(
            state,
            TurnEvent::Completion {
                action_id,
                result: Ok(result),
            },
            determinism,
        ),
        HarnessCommand::CompletionFailed { action_id, error } => dispatch(
            state,
            TurnEvent::Completion {
                action_id,
                result: Err(error),
            },
            determinism,
        ),
        HarnessCommand::HookCompleted { action_id, result } => dispatch(
            state,
            TurnEvent::Hook {
                action_id,
                result: Ok(result),
            },
            determinism,
        ),
        HarnessCommand::HookFailed { action_id, error } => dispatch(
            state,
            TurnEvent::Hook {
                action_id,
                result: Err(error),
            },
            determinism,
        ),
        HarnessCommand::ToolSucceeded {
            action_id,
            call_id,
            result,
        } => dispatch(
            state,
            TurnEvent::Tool {
                action_id,
                call_id,
                report: ReportedToolResult::Succeeded(result),
            },
            determinism,
        ),
        HarnessCommand::ToolFailed {
            action_id,
            call_id,
            result,
        } => dispatch(
            state,
            TurnEvent::Tool {
                action_id,
                call_id,
                report: ReportedToolResult::Failed(result),
            },
            determinism,
        ),
        HarnessCommand::FilesystemSucceeded { action_id, result } => dispatch(
            state,
            TurnEvent::Filesystem {
                action_id,
                result: Ok(result),
            },
            determinism,
        ),
        HarnessCommand::FilesystemFailed { action_id, error } => dispatch(
            state,
            TurnEvent::Filesystem {
                action_id,
                result: Err(error),
            },
            determinism,
        ),
        HarnessCommand::FailTurn {
            expected_turn_id,
            action_id,
            error,
        } => dispatch(
            state,
            TurnEvent::FailTurn {
                expected_turn_id,
                action_id,
                error,
            },
            determinism,
        ),
    }
}

/// Normalizes Core-owned runtime tool output before Step duplicate detection.
///
/// The normalized command is what the receipt stores, preserving the existing
/// rule that replaying an invalid successful result is equivalent to replaying
/// the Core-generated failure for that result.
pub(crate) fn normalize_command(state: &HarnessState, command: &mut HarnessCommand) {
    let HarnessCommand::ToolSucceeded {
        action_id,
        call_id,
        result,
    } = command
    else {
        return;
    };
    let Some(name) = state
        .pending_actions()
        .into_iter()
        .find_map(|action| match action {
            Action::RuntimeBuiltinTool {
                effect_id, call, ..
            } if effect_id == *action_id => Some(call.name),
            Action::Completion { .. }
            | Action::RuntimeBuiltinTool { .. }
            | Action::ProvidedTool { .. }
            | Action::Hook { .. }
            | Action::Filesystem { .. } => None,
        })
    else {
        return;
    };
    let Some(value) = result.structured_content() else {
        return;
    };
    if value.is_null() {
        return;
    }
    let Err(error) = state
        .resolved_tools
        .validate_runtime_builtin_output(name, value)
    else {
        return;
    };
    *command = HarnessCommand::ToolFailed {
        action_id: action_id.clone(),
        call_id: call_id.clone(),
        result: ToolResult::Failure {
            content: Vec::new(),
            structured_content: StructuredContent::Absent,
            meta: None,
            error: ProtocolError {
                code: "runtime_builtin_output_schema_mismatch".to_string(),
                message: format!(
                    "Runtime built-in {} returned structured content that does not satisfy its Core-owned output schema: {error}",
                    name.as_str()
                ),
                retryable: false,
                details: Value::Null,
            },
        },
    };
}

fn validate_correlation(state: &HarnessState, command: &HarnessCommand) -> Result<(), CoreError> {
    let Some(received_action_id) = command.correlated_action_id() else {
        return Ok(());
    };
    let pending_action_ids = state.pending_action_ids();
    if pending_action_ids
        .iter()
        .any(|action_id| action_id == received_action_id)
    {
        return Ok(());
    }
    Err(CoreError::invalid_correlation(
        received_action_id,
        pending_action_ids,
    ))
}

fn invalid_state(state: &HarnessState) -> CoreError {
    CoreError::invalid_state(state.command_state())
}
