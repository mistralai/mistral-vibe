use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::core::features::programmatic_tool_calling::RUN_TYPESCRIPT_NAME;
use crate::core::features::programmatic_tool_calling::{
    PendingProgramRecord, ProgramCapture, ProgramExecution, ProgramFunctionRecord,
    ProgramOperationRecord,
};
use crate::core::hooks::HookPoint;
use crate::core::hooks::hook_action_id;
use crate::core::tools::external::{ExternalToolCall, ToolOrigin};
use crate::core::wire::content::ContentBlock;
use crate::core::wire::tool::ToolCall;

use super::schema::{CheckpointExternalTool, CheckpointProgramFunction, CheckpointToolResult};
use super::tool::{
    capture_external_tool_call, restore_external_tool_call, validate_external_tool_identity,
    validate_tool_hook_action_id,
};

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub(super) struct CheckpointProgramExecution {
    operations: Vec<CheckpointProgramOperation>,
    round: u32,
}

impl CheckpointProgramExecution {
    pub(super) fn capture(
        execution: &ProgramExecution,
        parent_call: &ToolCall,
    ) -> Result<Self, String> {
        let capture = execution.capture()?;
        validate_parent_call(&capture, parent_call)?;
        let operations = capture
            .operations()
            .iter()
            .map(CheckpointProgramOperation::capture)
            .collect::<Result<_, _>>()?;
        Ok(Self {
            operations,
            round: capture.round(),
        })
    }

    pub(super) fn restore(self, parent_call: &ToolCall) -> Result<ProgramExecution, String> {
        let source = parent_program_code(parent_call)?.to_string();
        let operations = self
            .operations
            .into_iter()
            .map(CheckpointProgramOperation::restore)
            .collect::<Result<Vec<_>, _>>()?;
        ProgramExecution::restore(source, operations, self.round)
    }
}

fn validate_parent_call(capture: &ProgramCapture, parent_call: &ToolCall) -> Result<(), String> {
    let code = parent_program_code(parent_call)?;
    if capture.source() != code {
        return Err(
            "program checkpoint source does not match its run_typescript model call".to_string(),
        );
    }
    Ok(())
}

fn parent_program_code(parent_call: &ToolCall) -> Result<&str, String> {
    if parent_call.name != RUN_TYPESCRIPT_NAME {
        return Err(format!(
            "program checkpoint model call must be named {RUN_TYPESCRIPT_NAME:?}"
        ));
    }
    parent_call
        .arguments
        .get("code")
        .and_then(Value::as_str)
        .ok_or_else(|| "program checkpoint model call must contain string code".to_string())
}

fn validate_program_function_target(
    function: &ProgramFunctionRecord,
    call: &ExternalToolCall,
) -> Result<(), String> {
    validate_external_tool_identity(&call.call)?;
    let Some(programmatic_name) = call.call.programmatic_name() else {
        return Err(format!(
            "program operation {:?} targets a direct-only built-in",
            call.call_id
        ));
    };
    if function.name != programmatic_name {
        return Err(format!(
            "program operation {:?} targets {:?}, but its pending external call targets {programmatic_name:?}",
            call.call_id, function.name
        ));
    }
    Ok(())
}

#[allow(clippy::large_enum_variant)]
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
enum CheckpointProgramOperation {
    Internal {
        id: String,
        outcome: CheckpointProgramOutcome,
    },
    External {
        id: String,
        function: CheckpointProgramFunction,
        execution: CheckpointProgramExternalExecution,
    },
}

impl CheckpointProgramOperation {
    fn capture(operation: &ProgramOperationRecord) -> Result<Self, String> {
        Ok(match operation {
            ProgramOperationRecord::InternalResolved { id, value } => Self::Internal {
                id: id.clone(),
                outcome: CheckpointProgramOutcome::Resolved {
                    value: value.clone(),
                },
            },
            ProgramOperationRecord::InternalRejected { id, error } => Self::Internal {
                id: id.clone(),
                outcome: CheckpointProgramOutcome::Rejected {
                    error: error.clone(),
                },
            },
            ProgramOperationRecord::ExternalPending {
                id,
                function,
                pending,
            } => Self::External {
                id: id.clone(),
                function: CheckpointProgramFunction::capture(function),
                execution: CheckpointProgramExternalExecution::capture(pending, id, function)?,
            },
            ProgramOperationRecord::ExternalResolved {
                id,
                function,
                value,
                content,
            } => Self::External {
                id: id.clone(),
                function: CheckpointProgramFunction::capture(function),
                execution: CheckpointProgramExternalExecution::Resolved {
                    value: value.clone(),
                    content: content.clone(),
                },
            },
            ProgramOperationRecord::ExternalRejected {
                id,
                function,
                error,
                content,
            } => Self::External {
                id: id.clone(),
                function: CheckpointProgramFunction::capture(function),
                execution: CheckpointProgramExternalExecution::Rejected {
                    error: error.clone(),
                    content: content.clone(),
                },
            },
        })
    }

    fn restore(self) -> Result<ProgramOperationRecord, String> {
        Ok(match self {
            Self::Internal { id, outcome } => match outcome {
                CheckpointProgramOutcome::Resolved { value } => {
                    ProgramOperationRecord::InternalResolved { id, value }
                }
                CheckpointProgramOutcome::Rejected { error } => {
                    ProgramOperationRecord::InternalRejected { id, error }
                }
            },
            Self::External {
                id,
                function,
                execution,
            } => execution.restore(id, function.restore())?,
        })
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
enum CheckpointProgramOutcome {
    Resolved { value: Value },
    Rejected { error: Value },
}

#[allow(clippy::large_enum_variant)]
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
enum CheckpointProgramExternalExecution {
    AwaitingPreHook {
        hook_binding_ids: Vec<String>,
        call: CheckpointExternalTool,
    },
    Pending {
        call: CheckpointExternalTool,
    },
    AwaitingPostHook {
        hook_binding_ids: Vec<String>,
        call: CheckpointExternalTool,
        result: CheckpointToolResult,
    },
    Resolved {
        value: Value,
        content: Vec<ContentBlock>,
    },
    Rejected {
        error: Value,
        content: Vec<ContentBlock>,
    },
}

impl CheckpointProgramExternalExecution {
    fn capture(
        pending: &PendingProgramRecord,
        operation_id: &str,
        function: &ProgramFunctionRecord,
    ) -> Result<Self, String> {
        Self::validate(pending, operation_id, function)?;
        Ok(match pending {
            PendingProgramRecord::AwaitingPreHook {
                call,
                hook_binding_ids,
                ..
            } => Self::AwaitingPreHook {
                hook_binding_ids: hook_binding_ids.clone(),
                call: CheckpointExternalTool::capture(&call.call),
            },
            PendingProgramRecord::Pending { call } => Self::Pending {
                call: CheckpointExternalTool::capture(&call.call),
            },
            PendingProgramRecord::AwaitingPostHook {
                call,
                hook_binding_ids,
                result,
                ..
            } => Self::AwaitingPostHook {
                hook_binding_ids: hook_binding_ids.clone(),
                call: CheckpointExternalTool::capture(&call.call),
                result: CheckpointToolResult::capture(result),
            },
        })
    }

    fn validate(
        pending: &PendingProgramRecord,
        operation_id: &str,
        function: &ProgramFunctionRecord,
    ) -> Result<(), String> {
        match pending {
            PendingProgramRecord::AwaitingPreHook {
                call,
                hook_action_id,
                ..
            } => {
                validate_program_function_target(function, call)?;
                capture_external_tool_call(call, operation_id, ToolOrigin::Programmatic)?;
                validate_tool_hook_action_id(
                    hook_action_id,
                    operation_id,
                    ToolOrigin::Programmatic,
                    HookPoint::PreToolCall,
                )?;
            }
            PendingProgramRecord::Pending { call } => {
                validate_program_function_target(function, call)?;
                capture_external_tool_call(call, operation_id, ToolOrigin::Programmatic)?;
            }
            PendingProgramRecord::AwaitingPostHook {
                call,
                hook_action_id,
                ..
            } => {
                validate_program_function_target(function, call)?;
                capture_external_tool_call(call, operation_id, ToolOrigin::Programmatic)?;
                validate_tool_hook_action_id(
                    hook_action_id,
                    operation_id,
                    ToolOrigin::Programmatic,
                    HookPoint::PostToolCall,
                )?;
            }
        }
        Ok(())
    }

    fn restore(
        self,
        id: String,
        function: ProgramFunctionRecord,
    ) -> Result<ProgramOperationRecord, String> {
        let invocation_name = function.name.clone();
        let operation = match self {
            Self::AwaitingPreHook {
                hook_binding_ids,
                call,
            } => {
                let call = restore_external_tool_call(
                    call,
                    &id,
                    ToolOrigin::Programmatic,
                    invocation_name.clone(),
                );
                let hook_action_id = hook_action_id(&call.action_id, HookPoint::PreToolCall);
                ProgramOperationRecord::ExternalPending {
                    id,
                    function,
                    pending: Box::new(PendingProgramRecord::AwaitingPreHook {
                        call,
                        hook_action_id,
                        hook_binding_ids,
                    }),
                }
            }
            Self::Pending { call } => ProgramOperationRecord::ExternalPending {
                pending: Box::new(PendingProgramRecord::Pending {
                    call: restore_external_tool_call(
                        call,
                        &id,
                        ToolOrigin::Programmatic,
                        invocation_name.clone(),
                    ),
                }),
                id,
                function,
            },
            Self::AwaitingPostHook {
                hook_binding_ids,
                call,
                result,
            } => {
                let call = restore_external_tool_call(
                    call,
                    &id,
                    ToolOrigin::Programmatic,
                    invocation_name,
                );
                let hook_action_id = hook_action_id(&call.action_id, HookPoint::PostToolCall);
                ProgramOperationRecord::ExternalPending {
                    id,
                    function,
                    pending: Box::new(PendingProgramRecord::AwaitingPostHook {
                        call,
                        hook_action_id,
                        hook_binding_ids,
                        result: result.restore(),
                    }),
                }
            }
            Self::Resolved { value, content } => ProgramOperationRecord::ExternalResolved {
                id,
                function,
                value,
                content,
            },
            Self::Rejected { error, content } => ProgramOperationRecord::ExternalRejected {
                id,
                function,
                error,
                content,
            },
        };
        if let ProgramOperationRecord::ExternalPending {
            id,
            function,
            pending,
        } = &operation
        {
            Self::validate(pending, id, function)?;
        }
        Ok(operation)
    }
}
