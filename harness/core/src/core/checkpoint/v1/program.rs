use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::core::action_id;
use crate::core::features::permissions::GrantKey;
use crate::core::features::programmatic_tool_calling::RUN_TYPESCRIPT_NAME;
use crate::core::features::programmatic_tool_calling::{
    PendingProgramOperation, ProgramCapture, ProgramClock, ProgramExecution, ProgramFunctionRecord,
    ProgramOperationRecord,
};
use crate::core::hooks::HookPoint;
use crate::core::hooks::hook_action_id;
use crate::core::tools::execution::external_call::{
    ApprovalName, ExternalCallStage, PendingExternalCall,
};
use crate::core::tools::external::{ExternalToolCall, ToolOrigin};
use crate::core::wire::content::ContentBlock;
use crate::core::wire::tool::ToolCall;

use super::schema::{
    CheckpointExternalTool, CheckpointProgramFunction, CheckpointProgrammaticName,
    CheckpointToolResult,
};
use super::tool::{
    capture_external_tool_call, restore_external_tool_call, validate_external_tool_identity,
    validate_tool_hook_action_id,
};

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub(super) struct CheckpointProgramExecution {
    operations: Vec<CheckpointProgramOperation>,
    round: u32,
    // Earlier V1 checkpoints start the time budget on the next program round.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    started_at_unix_ms: Option<u64>,
    /// Time-budget pauses that finished: approval and hook waits.
    #[serde(default, skip_serializing_if = "is_zero")]
    paused_ms: u64,
    /// Start of the current pause while an operation waits for an approval or a hook.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    paused_since_unix_ms: Option<u64>,
}

fn is_zero(value: &u64) -> bool {
    *value == 0
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
            started_at_unix_ms: capture.clock().started_at_unix_ms,
            paused_ms: capture.clock().paused_ms,
            paused_since_unix_ms: capture.clock().paused_since_unix_ms,
        })
    }

    pub(super) fn restore(self, parent_call: &ToolCall) -> Result<ProgramExecution, String> {
        let source = parent_program_code(parent_call)?.to_string();
        let operations = self
            .operations
            .into_iter()
            .map(CheckpointProgramOperation::restore)
            .collect::<Result<Vec<_>, _>>()?;
        ProgramExecution::restore(
            source,
            operations,
            self.round,
            ProgramClock {
                started_at_unix_ms: self.started_at_unix_ms,
                paused_ms: self.paused_ms,
                paused_since_unix_ms: self.paused_since_unix_ms,
            },
        )
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
    let Some(programmatic_name) = call.call.programmatic_runtime_name() else {
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
    AwaitingApproval {
        grant_key: String,
        programmatic_name: CheckpointProgrammaticName,
        call: CheckpointExternalTool,
    },
    Pending {
        call: CheckpointExternalTool,
        /// For a sleep: its length after pre-tool hooks, before Core fit it to
        /// the time budget. Earlier V1 checkpoints do not record it.
        #[serde(default, skip_serializing_if = "Option::is_none")]
        requested_sleep_seconds: Option<f64>,
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
        pending: &PendingProgramOperation,
        operation_id: &str,
        function: &ProgramFunctionRecord,
    ) -> Result<Self, String> {
        Self::validate(pending, operation_id, function)?;
        let call = CheckpointExternalTool::capture(&pending.external.call.call);
        Ok(match &pending.external.stage {
            ExternalCallStage::AwaitingPreHook {
                hook_binding_ids, ..
            } => Self::AwaitingPreHook {
                hook_binding_ids: hook_binding_ids.clone(),
                call,
            },
            ExternalCallStage::AwaitingApproval {
                grant_key, name, ..
            } => {
                let ApprovalName::Programmatic(programmatic_name) = name else {
                    return Err("program approval must use a programmatic name".to_string());
                };
                Self::AwaitingApproval {
                    grant_key: grant_key.as_str().to_owned(),
                    programmatic_name: CheckpointProgrammaticName::capture(programmatic_name),
                    call,
                }
            }
            ExternalCallStage::Pending => Self::Pending {
                call,
                requested_sleep_seconds: pending.requested_sleep_seconds,
            },
            ExternalCallStage::AwaitingPostHook {
                hook_binding_ids,
                result,
                ..
            } => Self::AwaitingPostHook {
                hook_binding_ids: hook_binding_ids.clone(),
                call,
                result: CheckpointToolResult::capture(result),
            },
        })
    }

    fn validate(
        pending: &PendingProgramOperation,
        operation_id: &str,
        function: &ProgramFunctionRecord,
    ) -> Result<(), String> {
        let call = &pending.external.call;
        validate_program_function_target(function, call)?;
        capture_external_tool_call(call, operation_id, ToolOrigin::Programmatic)?;
        match &pending.external.stage {
            ExternalCallStage::AwaitingPreHook { hook_action_id, .. } => {
                validate_tool_hook_action_id(
                    hook_action_id,
                    operation_id,
                    ToolOrigin::Programmatic,
                    HookPoint::PreToolCall,
                )
            }
            ExternalCallStage::AwaitingApproval {
                approval_action_id, ..
            } => {
                let expected_action_id = action_id::approval(&call.action_id);
                if approval_action_id != &expected_action_id {
                    return Err(format!(
                        "checkpoint approval action ID {approval_action_id:?} does not match derived action ID {expected_action_id:?}"
                    ));
                }
                Ok(())
            }
            ExternalCallStage::Pending => Ok(()),
            ExternalCallStage::AwaitingPostHook { hook_action_id, .. } => {
                validate_tool_hook_action_id(
                    hook_action_id,
                    operation_id,
                    ToolOrigin::Programmatic,
                    HookPoint::PostToolCall,
                )
            }
        }
    }

    fn restore(
        self,
        id: String,
        function: ProgramFunctionRecord,
    ) -> Result<ProgramOperationRecord, String> {
        let restore_call = |call| {
            restore_external_tool_call(call, &id, ToolOrigin::Programmatic, function.name.clone())
        };
        let (external, requested_sleep_seconds) = match self {
            Self::AwaitingPreHook {
                hook_binding_ids,
                call,
            } => (
                PendingExternalCall::awaiting_pre_hook(restore_call(call), hook_binding_ids),
                None,
            ),
            Self::AwaitingApproval {
                grant_key,
                programmatic_name,
                call,
            } => (
                PendingExternalCall::awaiting_approval(
                    restore_call(call),
                    GrantKey::try_from(grant_key)?,
                    ApprovalName::Programmatic(programmatic_name.restore()),
                ),
                None,
            ),
            Self::Pending {
                call,
                requested_sleep_seconds,
            } => (
                PendingExternalCall::pending(restore_call(call)),
                requested_sleep_seconds,
            ),
            Self::AwaitingPostHook {
                hook_binding_ids,
                call,
                result,
            } => {
                let call = restore_call(call);
                let stage = ExternalCallStage::AwaitingPostHook {
                    hook_action_id: hook_action_id(&call.action_id, HookPoint::PostToolCall),
                    hook_binding_ids,
                    result: result.restore(),
                };
                (PendingExternalCall { call, stage }, None)
            }
            Self::Resolved { value, content } => {
                return Ok(ProgramOperationRecord::ExternalResolved {
                    id,
                    function,
                    value,
                    content,
                });
            }
            Self::Rejected { error, content } => {
                return Ok(ProgramOperationRecord::ExternalRejected {
                    id,
                    function,
                    error,
                    content,
                });
            }
        };
        let pending = PendingProgramOperation {
            external,
            requested_sleep_seconds,
        };
        Self::validate(&pending, &id, &function)?;
        Ok(ProgramOperationRecord::ExternalPending {
            id,
            function,
            pending: Box::new(pending),
        })
    }
}
