use std::collections::HashSet;

use serde::{Deserialize, Serialize};

use crate::core::action_id;
use crate::core::features::large_output::{CharacterLimits, PendingWrite};
use crate::core::hooks::HookPoint;
use crate::core::hooks::hook_action_id;
use crate::core::tools::execution::{
    LargeOutputSource, ToolBatch, ToolExecution, ToolExecutionState,
};
use crate::core::tools::external::{
    ExternalTool, ExternalToolCall, ToolOrigin, effect_id_for_operation,
};
use crate::core::wire::content::ContentBlock;
use crate::core::wire::message::{Message, ToolOutcome};
use crate::core::wire::tool::ToolCall;

use super::program::CheckpointProgramExecution;
use super::schema::{CheckpointExternalTool, CheckpointToolResult};
use super::validate_derived_hook_action_id;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub(super) struct CheckpointToolBatch {
    executions: Vec<CheckpointToolExecutionState>,
}

impl CheckpointToolBatch {
    pub(super) fn capture(batch: &ToolBatch) -> Result<Self, String> {
        validate_tool_batch(&batch.executions)?;
        Ok(Self {
            executions: batch
                .executions
                .iter()
                .map(|execution| {
                    CheckpointToolExecutionState::capture(&execution.state, &execution.call)
                })
                .collect::<Result<_, _>>()?,
        })
    }

    pub(super) fn restore(self, model_calls: Vec<ToolCall>) -> Result<ToolBatch, String> {
        if self.executions.is_empty() {
            return Err("tool batch must contain at least one execution".to_string());
        }
        if self.executions.len() != model_calls.len() {
            return Err(
                "active tool batch does not match its assistant tool-call message".to_string(),
            );
        }
        let executions = self
            .executions
            .into_iter()
            .zip(model_calls)
            .map(|(state, call)| {
                let state = state.restore(&call)?;
                Ok::<_, String>(ToolExecution { call, state })
            })
            .collect::<Result<Vec<_>, _>>()?;
        validate_tool_batch(&executions)?;
        Ok(ToolBatch { executions })
    }
}

#[allow(clippy::large_enum_variant)]
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
enum CheckpointToolExecutionState {
    DirectAwaitingPreHook {
        hook_binding_ids: Vec<String>,
        call: CheckpointExternalTool,
    },
    DirectPending {
        call: CheckpointExternalTool,
    },
    DirectAwaitingPostHook {
        hook_binding_ids: Vec<String>,
        call: CheckpointExternalTool,
        result: CheckpointToolResult,
    },
    ProgramPending {
        execution: CheckpointProgramExecution,
    },
    AwaitingLargeOutputWrite {
        source: CheckpointLargeOutputSource,
        pending: CheckpointPendingLargeOutputWrite,
    },
    Completed {
        result: CheckpointCompletedToolResult,
    },
}

impl CheckpointToolExecutionState {
    fn capture(state: &ToolExecutionState, model_call: &ToolCall) -> Result<Self, String> {
        let model_call_id = &model_call.id;
        match state {
            ToolExecutionState::DirectAwaitingPreHook {
                hook_action_id,
                hook_binding_ids,
                call,
            } => {
                validate_tool_hook_action_id(
                    hook_action_id,
                    model_call_id,
                    ToolOrigin::TopLevel,
                    HookPoint::PreToolCall,
                )?;
                Ok(Self::DirectAwaitingPreHook {
                    hook_binding_ids: hook_binding_ids.clone(),
                    call: capture_external_tool_call(call, model_call_id, ToolOrigin::TopLevel)?,
                })
            }
            ToolExecutionState::DirectPending { call } => Ok(Self::DirectPending {
                call: capture_external_tool_call(call, model_call_id, ToolOrigin::TopLevel)?,
            }),
            ToolExecutionState::DirectAwaitingPostHook {
                hook_action_id,
                hook_binding_ids,
                call,
                result,
            } => {
                validate_tool_hook_action_id(
                    hook_action_id,
                    model_call_id,
                    ToolOrigin::TopLevel,
                    HookPoint::PostToolCall,
                )?;
                Ok(Self::DirectAwaitingPostHook {
                    hook_binding_ids: hook_binding_ids.clone(),
                    call: capture_external_tool_call(call, model_call_id, ToolOrigin::TopLevel)?,
                    result: CheckpointToolResult::capture(result),
                })
            }
            ToolExecutionState::ProgramPending { execution } => Ok(Self::ProgramPending {
                execution: CheckpointProgramExecution::capture(execution, model_call)?,
            }),
            ToolExecutionState::AwaitingLargeOutputWrite {
                action_id: pending_action_id,
                source,
                pending,
            } => {
                let origin = match source {
                    LargeOutputSource::Direct { .. } => "direct",
                    LargeOutputSource::RunTypescript => "run_typescript",
                };
                let expected_action_id = action_id::filesystem("write", origin, model_call_id);
                if pending_action_id != &expected_action_id {
                    return Err(format!(
                        "checkpoint filesystem write action ID {pending_action_id:?} does not match derived action ID {expected_action_id:?}"
                    ));
                }
                Ok(Self::AwaitingLargeOutputWrite {
                    source: CheckpointLargeOutputSource::capture(source, model_call_id)?,
                    pending: CheckpointPendingLargeOutputWrite::capture(pending)?,
                })
            }
            ToolExecutionState::Completed { message } => Ok(Self::Completed {
                result: CheckpointCompletedToolResult::capture(message, model_call)?,
            }),
        }
    }

    fn restore(self, model_call: &ToolCall) -> Result<ToolExecutionState, String> {
        let model_call_id = &model_call.id;
        match self {
            Self::DirectAwaitingPreHook {
                hook_binding_ids,
                call,
            } => Ok(ToolExecutionState::DirectAwaitingPreHook {
                hook_action_id: hook_action_id(
                    &effect_id_for_operation(ToolOrigin::TopLevel, model_call_id),
                    HookPoint::PreToolCall,
                ),
                hook_binding_ids,
                call: restore_external_tool_call(
                    call,
                    model_call_id,
                    ToolOrigin::TopLevel,
                    model_call.name.clone(),
                ),
            }),
            Self::DirectPending { call } => Ok(ToolExecutionState::DirectPending {
                call: restore_external_tool_call(
                    call,
                    model_call_id,
                    ToolOrigin::TopLevel,
                    model_call.name.clone(),
                ),
            }),
            Self::DirectAwaitingPostHook {
                hook_binding_ids,
                call,
                result,
            } => Ok(ToolExecutionState::DirectAwaitingPostHook {
                hook_action_id: hook_action_id(
                    &effect_id_for_operation(ToolOrigin::TopLevel, model_call_id),
                    HookPoint::PostToolCall,
                ),
                hook_binding_ids,
                call: restore_external_tool_call(
                    call,
                    model_call_id,
                    ToolOrigin::TopLevel,
                    model_call.name.clone(),
                ),
                result: result.restore(),
            }),
            Self::ProgramPending { execution } => Ok(ToolExecutionState::ProgramPending {
                execution: execution.restore(model_call)?,
            }),
            Self::AwaitingLargeOutputWrite { source, pending } => {
                let source = source.restore(model_call_id, model_call.name.clone());
                let origin = match source {
                    LargeOutputSource::Direct { .. } => "direct",
                    LargeOutputSource::RunTypescript => "run_typescript",
                };
                Ok(ToolExecutionState::AwaitingLargeOutputWrite {
                    action_id: action_id::filesystem("write", origin, model_call_id),
                    source,
                    pending: pending.restore(),
                })
            }
            Self::Completed { result } => Ok(ToolExecutionState::Completed {
                message: result.restore(model_call),
            }),
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
enum CheckpointLargeOutputSource {
    Direct { call: CheckpointExternalTool },
    RunTypescript,
}

impl CheckpointLargeOutputSource {
    fn capture(source: &LargeOutputSource, model_call_id: &str) -> Result<Self, String> {
        match source {
            LargeOutputSource::Direct { call } => Ok(Self::Direct {
                call: capture_external_tool_call(call, model_call_id, ToolOrigin::TopLevel)?,
            }),
            LargeOutputSource::RunTypescript => Ok(Self::RunTypescript),
        }
    }

    fn restore(self, model_call_id: &str, invocation_name: String) -> LargeOutputSource {
        match self {
            Self::Direct { call } => LargeOutputSource::Direct {
                call: restore_external_tool_call(
                    call,
                    model_call_id,
                    ToolOrigin::TopLevel,
                    invocation_name,
                ),
            },
            Self::RunTypescript => LargeOutputSource::RunTypescript,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
struct CheckpointPendingLargeOutputWrite {
    relative_path: String,
    serialized: String,
    original_result: CheckpointToolResult,
    tool_name: String,
    max_output_characters: usize,
    model_visible_output_characters: usize,
    type_definition: String,
}

impl CheckpointPendingLargeOutputWrite {
    fn capture(pending: &PendingWrite) -> Result<Self, String> {
        let limits = pending.limits();
        if pending.relative_path().trim().is_empty()
            || pending.serialized().is_empty()
            || pending.tool_name().trim().is_empty()
            || pending.type_definition().trim().is_empty()
        {
            return Err("pending large-output write contains an empty required field".to_string());
        }
        Ok(Self {
            relative_path: pending.relative_path().to_string(),
            serialized: pending.serialized().to_string(),
            original_result: CheckpointToolResult::capture(pending.original_result()),
            tool_name: pending.tool_name().to_string(),
            max_output_characters: limits.max_output,
            model_visible_output_characters: limits.model_visible_output,
            type_definition: pending.type_definition().to_string(),
        })
    }

    fn restore(self) -> PendingWrite {
        PendingWrite::restore(
            self.relative_path,
            self.serialized,
            self.original_result.restore(),
            self.tool_name,
            CharacterLimits {
                max_output: self.max_output_characters,
                model_visible_output: self.model_visible_output_characters,
            },
            self.type_definition,
        )
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
enum CheckpointCompletedToolResult {
    Success { content: Vec<ContentBlock> },
    Failure { content: Vec<ContentBlock> },
}

impl CheckpointCompletedToolResult {
    fn capture(message: &Message, model_call: &ToolCall) -> Result<Self, String> {
        let Message::Tool {
            tool_call_id,
            name,
            outcome,
            content,
            meta,
        } = message
        else {
            return Err(
                "completed checkpoint tool execution must contain a tool message".to_string(),
            );
        };
        if tool_call_id != &model_call.id || name != &model_call.name {
            return Err(
                "completed checkpoint tool message does not match its model call".to_string(),
            );
        }
        if meta.is_some() {
            return Err("completed checkpoint tool message cannot contain metadata".to_string());
        }
        Ok(match outcome {
            ToolOutcome::Success => Self::Success {
                content: content.clone(),
            },
            ToolOutcome::Failure => Self::Failure {
                content: content.clone(),
            },
        })
    }

    fn restore(self, model_call: &ToolCall) -> Message {
        let (outcome, content) = match self {
            Self::Success { content } => (ToolOutcome::Success, content),
            Self::Failure { content } => (ToolOutcome::Failure, content),
        };
        Message::tool(
            model_call.id.clone(),
            model_call.name.clone(),
            outcome,
            content,
            None,
        )
    }
}

pub(super) fn capture_external_tool_call(
    call: &ExternalToolCall,
    operation_id: &str,
    expected_origin: ToolOrigin,
) -> Result<CheckpointExternalTool, String> {
    if call.origin != expected_origin {
        return Err(format!(
            "tool call {:?} has an invalid checkpoint origin",
            call.call_id
        ));
    }
    if call.call_id != operation_id {
        return Err(format!(
            "checkpoint tool call ID {:?} does not match parent operation ID {operation_id:?}",
            call.call_id
        ));
    }
    let expected_action_id = effect_id_for_operation(expected_origin, operation_id);
    if call.action_id != expected_action_id {
        return Err(format!(
            "checkpoint tool action ID {:?} does not match derived action ID {expected_action_id:?}",
            call.action_id
        ));
    }
    Ok(CheckpointExternalTool::capture(&call.call))
}

pub(super) fn restore_external_tool_call(
    call: CheckpointExternalTool,
    operation_id: &str,
    origin: ToolOrigin,
    invocation_name: String,
) -> ExternalToolCall {
    ExternalToolCall {
        action_id: effect_id_for_operation(origin, operation_id),
        call_id: operation_id.to_string(),
        origin,
        call: call.restore(invocation_name),
    }
}

pub(super) fn validate_tool_hook_action_id(
    action_id: &str,
    operation_id: &str,
    origin: ToolOrigin,
    point: HookPoint,
) -> Result<(), String> {
    validate_derived_hook_action_id(
        action_id,
        &effect_id_for_operation(origin, operation_id),
        point,
    )
}

fn validate_tool_batch(executions: &[ToolExecution]) -> Result<(), String> {
    if executions.is_empty() {
        return Err("tool batch must contain at least one execution".to_string());
    }
    let mut call_ids = HashSet::new();
    for execution in executions {
        validate_tool_execution(execution)?;
        if !call_ids.insert(execution.call.id.as_str()) {
            return Err(format!(
                "duplicate tool call ID {:?} in checkpoint",
                execution.call.id
            ));
        }
    }
    Ok(())
}

fn validate_tool_execution(execution: &ToolExecution) -> Result<(), String> {
    let model_call = &execution.call;
    if model_call.id.trim().is_empty() || model_call.name.trim().is_empty() {
        return Err("checkpoint tool call identity must not be empty".to_string());
    }
    if model_call
        .argument_error
        .as_deref()
        .is_some_and(|error| error.trim().is_empty())
    {
        return Err("checkpoint tool argument error must not be empty".to_string());
    }
    if model_call.argument_error.is_some()
        && !matches!(execution.state, ToolExecutionState::Completed { .. })
    {
        return Err(
            "checkpoint tool call with invalid arguments must already be completed".to_string(),
        );
    }
    match &execution.state {
        ToolExecutionState::DirectAwaitingPreHook { call, .. } => {
            validate_direct_tool_call(model_call, call)?;
            return Ok(());
        }
        ToolExecutionState::DirectPending { call } => {
            validate_direct_tool_call(model_call, call)?;
            return Ok(());
        }
        ToolExecutionState::DirectAwaitingPostHook { call, .. } => {
            validate_direct_tool_call(model_call, call)?;
            return Ok(());
        }
        ToolExecutionState::ProgramPending { .. } => return Ok(()),
        ToolExecutionState::AwaitingLargeOutputWrite { source, .. } => {
            if let LargeOutputSource::Direct { call } = source {
                validate_direct_tool_call(model_call, call)?;
            }
            return Ok(());
        }
        ToolExecutionState::Completed { .. } => {}
    }
    let ToolExecutionState::Completed { message } = &execution.state else {
        unreachable!();
    };
    message
        .validate()
        .map_err(|error| error.detail().to_string())?;
    let Message::Tool {
        tool_call_id,
        name,
        outcome,
        ..
    } = message
    else {
        return Err("completed checkpoint tool execution must contain a tool message".to_string());
    };
    if tool_call_id != &model_call.id || name != &model_call.name {
        return Err("completed checkpoint tool message does not match its model call".to_string());
    }
    if model_call.argument_error.is_some() && outcome != &ToolOutcome::Failure {
        return Err(
            "checkpoint tool call with invalid arguments must contain a failure message"
                .to_string(),
        );
    }
    Ok(())
}

fn validate_direct_tool_call(
    model_call: &ToolCall,
    external_call: &ExternalToolCall,
) -> Result<(), String> {
    validate_external_tool_identity(&external_call.call)?;
    let Some(direct_name) = external_call.call.direct_model_name() else {
        return Err(format!(
            "checkpoint direct tool call {:?} targets a programmatic-only built-in",
            model_call.id
        ));
    };
    if model_call.name != direct_name {
        return Err(format!(
            "checkpoint direct tool call {:?} names {:?}, but its external call targets {direct_name:?}",
            model_call.id, model_call.name
        ));
    }
    Ok(())
}

pub(super) fn validate_external_tool_identity(tool: &ExternalTool) -> Result<(), String> {
    if let ExternalTool::Provided {
        group_name,
        tool_name,
        ..
    } = tool
        && (group_name.trim().is_empty() || tool_name.trim().is_empty())
    {
        return Err("checkpoint provided-tool identity must not be empty".to_string());
    }
    Ok(())
}
