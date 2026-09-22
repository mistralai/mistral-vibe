use std::collections::HashSet;

use serde_json::Value;

use crate::core::error::CoreError;
use crate::core::features::programmatic_tool_calling::model::{
    PartialEvaluation, ToolFunction, ToolKind, ToolState,
};
use crate::core::hooks::{HookCall, HookPoint, hook_action_id};
use crate::core::step_protocol::Action;
use crate::core::tools::external::{
    ExternalTool, ExternalToolCall, ToolOrigin, effect_id_for_operation,
};
use crate::core::wire::content::ContentBlock;
use crate::core::wire::tool::{StructuredContent, ToolResult};

#[derive(Clone, Debug, PartialEq)]
pub(super) struct PendingProgramOperation {
    call: ExternalToolCall,
    stage: ProgramOperationStage,
}

#[allow(clippy::large_enum_variant)]
#[derive(Clone, Debug, PartialEq)]
enum ProgramOperationStage {
    AwaitingPreHook {
        hook_action_id: String,
        hook_binding_ids: Vec<String>,
    },
    Pending,
    AwaitingPostHook {
        hook_action_id: String,
        hook_binding_ids: Vec<String>,
        result: ToolResult,
    },
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct ProgramExecution {
    partial_evaluation: PartialEvaluation,
    pending_operations: Vec<PendingProgramOperation>,
    round: u32,
}

#[derive(Debug)]
pub(super) struct ProgramContinuation {
    pub(super) partial_evaluation: PartialEvaluation,
    pub(super) round: u32,
}

pub(super) enum PendingProgramHook<'a> {
    Pre(&'a ExternalToolCall),
    Post(&'a ExternalToolCall),
}

impl PendingProgramHook<'_> {
    pub(super) fn call(&self) -> &ExternalToolCall {
        match self {
            Self::Pre(call) | Self::Post(call) => call,
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct ProgramFunctionRecord {
    pub(crate) name: String,
    pub(crate) arguments: Value,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) enum ProgramOperationRecord {
    InternalResolved {
        id: String,
        value: Value,
    },
    InternalRejected {
        id: String,
        error: Value,
    },
    ExternalPending {
        id: String,
        function: ProgramFunctionRecord,
        pending: Box<PendingProgramRecord>,
    },
    ExternalResolved {
        id: String,
        function: ProgramFunctionRecord,
        value: Value,
        content: Vec<ContentBlock>,
    },
    ExternalRejected {
        id: String,
        function: ProgramFunctionRecord,
        error: Value,
        content: Vec<ContentBlock>,
    },
}

#[allow(clippy::large_enum_variant)]
#[derive(Clone, Debug, PartialEq)]
pub(crate) enum PendingProgramRecord {
    AwaitingPreHook {
        call: ExternalToolCall,
        hook_action_id: String,
        hook_binding_ids: Vec<String>,
    },
    Pending {
        call: ExternalToolCall,
    },
    AwaitingPostHook {
        call: ExternalToolCall,
        hook_action_id: String,
        hook_binding_ids: Vec<String>,
        result: ToolResult,
    },
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct ProgramCapture {
    source: String,
    operations: Vec<ProgramOperationRecord>,
    round: u32,
}

impl ProgramCapture {
    pub(crate) fn source(&self) -> &str {
        &self.source
    }

    pub(crate) fn operations(&self) -> &[ProgramOperationRecord] {
        &self.operations
    }

    pub(crate) fn round(&self) -> u32 {
        self.round
    }
}

impl PendingProgramOperation {
    pub(super) fn awaiting_pre_hook(
        call: ExternalToolCall,
        hook_action_id: String,
        hook_binding_ids: Vec<String>,
    ) -> Self {
        Self {
            call,
            stage: ProgramOperationStage::AwaitingPreHook {
                hook_action_id,
                hook_binding_ids,
            },
        }
    }

    pub(super) fn pending(call: ExternalToolCall) -> Self {
        Self {
            call,
            stage: ProgramOperationStage::Pending,
        }
    }
}

impl ProgramExecution {
    pub(super) fn new(
        partial_evaluation: PartialEvaluation,
        pending_operations: Vec<PendingProgramOperation>,
        round: u32,
    ) -> Self {
        Self {
            partial_evaluation,
            pending_operations,
            round,
        }
    }

    pub(crate) fn pending_actions(&self, turn_id: &str) -> Vec<Action> {
        self.pending_operations
            .iter()
            .map(|operation| match &operation.stage {
                ProgramOperationStage::AwaitingPreHook {
                    hook_action_id,
                    hook_binding_ids,
                } => Action::Hook {
                    effect_id: hook_action_id.clone(),
                    turn_id: turn_id.to_string(),
                    hook_binding_ids: hook_binding_ids.clone(),
                    call: HookCall::PreToolCall {
                        tool_call: (&operation.call).into(),
                    },
                },
                ProgramOperationStage::Pending => Action::external_tool(&operation.call, turn_id),
                ProgramOperationStage::AwaitingPostHook {
                    hook_action_id,
                    hook_binding_ids,
                    result,
                } => Action::Hook {
                    effect_id: hook_action_id.clone(),
                    turn_id: turn_id.to_string(),
                    hook_binding_ids: hook_binding_ids.clone(),
                    call: HookCall::PostToolCall {
                        tool_call: (&operation.call).into(),
                        tool_result: result.clone(),
                    },
                },
            })
            .collect()
    }

    pub(crate) fn handles_tool_action(&self, action_id: &str) -> bool {
        self.pending_operation(action_id).is_some()
    }

    pub(crate) fn handles_hook_action(&self, action_id: &str) -> bool {
        self.pending_hook(action_id).is_some()
    }

    pub(super) fn pending_operation(&self, action_id: &str) -> Option<&ExternalToolCall> {
        self.pending_operations
            .iter()
            .find(|operation| {
                operation.call.action_id == action_id
                    && matches!(operation.stage, ProgramOperationStage::Pending)
            })
            .map(|operation| &operation.call)
    }

    pub(super) fn pending_hook(&self, action_id: &str) -> Option<PendingProgramHook<'_>> {
        self.pending_operations
            .iter()
            .find_map(|operation| match &operation.stage {
                ProgramOperationStage::AwaitingPreHook { hook_action_id, .. }
                    if hook_action_id == action_id =>
                {
                    Some(PendingProgramHook::Pre(&operation.call))
                }
                ProgramOperationStage::AwaitingPostHook { hook_action_id, .. }
                    if hook_action_id == action_id =>
                {
                    Some(PendingProgramHook::Post(&operation.call))
                }
                _ => None,
            })
    }

    pub(super) fn continue_after_pre_hook(
        &mut self,
        hook_action_id: &str,
        effective_call: ExternalToolCall,
    ) -> Result<(), CoreError> {
        let operation = self
            .pending_operations
            .iter_mut()
            .find(|operation| {
                matches!(
                    &operation.stage,
                    ProgramOperationStage::AwaitingPreHook {
                        hook_action_id: pending_hook_action_id,
                        ..
                    } if pending_hook_action_id == hook_action_id
                )
            })
            .ok_or_else(|| CoreError::invariant("program pre-hook operation was not found"))?;
        operation.call = effective_call;
        operation.stage = ProgramOperationStage::Pending;
        Ok(())
    }

    pub(super) fn await_post_hook(
        &mut self,
        action_id: &str,
        hook_action_id: String,
        hook_binding_ids: Vec<String>,
        result: ToolResult,
    ) -> Result<(), CoreError> {
        let operation = self
            .pending_operations
            .iter_mut()
            .find(|operation| {
                operation.call.action_id == action_id
                    && matches!(operation.stage, ProgramOperationStage::Pending)
            })
            .ok_or_else(|| CoreError::invariant("program tool operation was not found"))?;
        operation.stage = ProgramOperationStage::AwaitingPostHook {
            hook_action_id,
            hook_binding_ids,
            result,
        };
        Ok(())
    }

    fn take_pending_operation(
        &mut self,
        action_id: &str,
    ) -> Result<PendingProgramOperation, CoreError> {
        let operation_index = self
            .pending_operations
            .iter()
            .position(|operation| {
                operation.call.action_id == action_id
                    && matches!(operation.stage, ProgramOperationStage::Pending)
            })
            .ok_or_else(|| CoreError::invariant("pending program operation was not found"))?;
        Ok(self.pending_operations.remove(operation_index))
    }

    fn take_hook_operation(
        &mut self,
        hook_action_id: &str,
    ) -> Result<PendingProgramOperation, CoreError> {
        let operation_index = self
            .pending_operations
            .iter()
            .position(|operation| match &operation.stage {
                ProgramOperationStage::AwaitingPreHook {
                    hook_action_id: pending_hook_action_id,
                    ..
                }
                | ProgramOperationStage::AwaitingPostHook {
                    hook_action_id: pending_hook_action_id,
                    ..
                } => pending_hook_action_id == hook_action_id,
                ProgramOperationStage::Pending => false,
            })
            .ok_or_else(|| CoreError::invariant("pending program hook was not found"))?;
        Ok(self.pending_operations.remove(operation_index))
    }

    pub(super) fn resolve_pending_tool(
        &mut self,
        action_id: &str,
        result: ToolResult,
    ) -> Result<Option<ProgramContinuation>, CoreError> {
        let operation = self.take_pending_operation(action_id)?;
        self.resolve_operation(&operation.call.call_id, result)?;
        self.continuation()
    }

    pub(super) fn resolve_pending_hook(
        &mut self,
        hook_action_id: &str,
        result: ToolResult,
    ) -> Result<Option<ProgramContinuation>, CoreError> {
        let operation = self.take_hook_operation(hook_action_id)?;
        self.resolve_operation(&operation.call.call_id, result)?;
        self.continuation()
    }

    fn resolve_operation(
        &mut self,
        operation_id: &str,
        result: ToolResult,
    ) -> Result<(), CoreError> {
        let operation = self
            .partial_evaluation
            .tool_state
            .iter_mut()
            .find(
                |operation| matches!(operation, ToolState::Pending { id, .. } if id == operation_id),
            )
            .ok_or_else(|| {
                CoreError::invariant(format!("pending operation {operation_id:?} was not found"))
            })?;
        let ToolState::Pending { kind, id, function } = operation.clone() else {
            unreachable!();
        };
        *operation = match result {
            ToolResult::Failure {
                content,
                structured_content,
                meta: _,
                error,
            } => {
                let mut rejection = serde_json::json!({
                    "name": error.code,
                    "message": error.message,
                    "details": error.details,
                });
                if let Some(value) = structured_content
                    .into_value()
                    .filter(|value| !value.is_null())
                {
                    rejection
                        .as_object_mut()
                        .expect("tool rejection is an object")
                        .insert("structuredContent".to_string(), value);
                }
                ToolState::Rejected {
                    kind,
                    id,
                    function,
                    error: rejection,
                    content: retained_program_content(content),
                }
            }
            ToolResult::Success {
                content,
                structured_content,
                ..
            } => {
                let result = programmatic_tool_result_value(&structured_content, &content);
                ToolState::Resolved {
                    kind,
                    id,
                    function,
                    result,
                    content: retained_program_content(content),
                }
            }
        };
        Ok(())
    }

    fn continuation(&self) -> Result<Option<ProgramContinuation>, CoreError> {
        if !self.pending_operations.is_empty() {
            return Ok(None);
        }
        let round = self
            .round
            .checked_add(1)
            .ok_or_else(|| CoreError::invariant("program replay round overflowed"))?;
        Ok(Some(ProgramContinuation {
            partial_evaluation: self.partial_evaluation.clone(),
            round,
        }))
    }

    #[cfg(test)]
    pub(crate) fn test_swap_pending_operations(&mut self, left: usize, right: usize) {
        self.pending_operations.swap(left, right);
    }

    #[cfg(test)]
    pub(crate) fn test_replace_source(&mut self, source: impl Into<String>) {
        self.partial_evaluation.code = source.into();
    }

    #[cfg(test)]
    pub(crate) fn test_replace_input(&mut self, input: Value) {
        self.partial_evaluation.input = input;
    }

    pub(crate) fn capture(&self) -> Result<ProgramCapture, String> {
        if self.pending_operations.is_empty() {
            return Err(
                "pending program execution must contain at least one operation".to_string(),
            );
        }
        if self.partial_evaluation.input != replay_input() {
            return Err("program replay input is not canonical".to_string());
        }
        let mut pending_operations = self.pending_operations.iter();
        let mut operation_ids = HashSet::new();
        let mut operations = Vec::with_capacity(self.partial_evaluation.tool_state.len());
        for operation in &self.partial_evaluation.tool_state {
            let record = match operation {
                ToolState::Pending { kind, id, function } => {
                    validate_unique_operation_id(&mut operation_ids, id)?;
                    if *kind != ToolKind::External {
                        return Err("internal program operation cannot be pending".to_string());
                    }
                    validate_external_function(function)?;
                    let pending = pending_operations.next().ok_or_else(|| {
                        format!("pending program operation {id:?} has no continuation")
                    })?;
                    if pending.call.call_id != *id {
                        return Err(format!(
                            "pending program operation {id:?} does not match continuation {:?}",
                            pending.call.call_id
                        ));
                    }
                    let function = ProgramFunctionRecord::from(function);
                    let pending = PendingProgramRecord::from(pending);
                    validate_pending_program_record(id, &function, &pending)?;
                    ProgramOperationRecord::ExternalPending {
                        id: id.clone(),
                        function,
                        pending: Box::new(pending),
                    }
                }
                ToolState::Resolved {
                    kind,
                    id,
                    function,
                    result,
                    content,
                } => {
                    validate_unique_operation_id(&mut operation_ids, id)?;
                    match kind {
                        ToolKind::Internal => {
                            validate_internal_operation(id, function, content)?;
                            ProgramOperationRecord::InternalResolved {
                                id: id.clone(),
                                value: result.clone(),
                            }
                        }
                        ToolKind::External => {
                            validate_external_function(function)?;
                            ProgramOperationRecord::ExternalResolved {
                                id: id.clone(),
                                function: function.into(),
                                value: result.clone(),
                                content: content.clone(),
                            }
                        }
                    }
                }
                ToolState::Rejected {
                    kind,
                    id,
                    function,
                    error,
                    content,
                } => {
                    validate_unique_operation_id(&mut operation_ids, id)?;
                    match kind {
                        ToolKind::Internal => {
                            validate_internal_operation(id, function, content)?;
                            ProgramOperationRecord::InternalRejected {
                                id: id.clone(),
                                error: error.clone(),
                            }
                        }
                        ToolKind::External => {
                            validate_external_function(function)?;
                            ProgramOperationRecord::ExternalRejected {
                                id: id.clone(),
                                function: function.into(),
                                error: error.clone(),
                                content: content.clone(),
                            }
                        }
                    }
                }
            };
            operations.push(record);
        }
        if pending_operations.next().is_some() {
            return Err(
                "program checkpoint has continuations absent from operation history".to_string(),
            );
        }
        Ok(ProgramCapture {
            source: self.partial_evaluation.code.clone(),
            operations,
            round: self.round,
        })
    }

    pub(crate) fn restore(
        source: String,
        operations: Vec<ProgramOperationRecord>,
        round: u32,
    ) -> Result<Self, String> {
        let mut operation_ids = HashSet::new();
        let mut tool_state = Vec::with_capacity(operations.len());
        let mut pending_operations = Vec::new();
        for operation in operations {
            match operation {
                ProgramOperationRecord::InternalResolved { id, value } => {
                    validate_unique_operation_id(&mut operation_ids, &id)?;
                    tool_state.push(ToolState::Resolved {
                        kind: ToolKind::Internal,
                        id,
                        function: internal_step_function(),
                        result: value,
                        content: Vec::new(),
                    });
                }
                ProgramOperationRecord::InternalRejected { id, error } => {
                    validate_unique_operation_id(&mut operation_ids, &id)?;
                    tool_state.push(ToolState::Rejected {
                        kind: ToolKind::Internal,
                        id,
                        function: internal_step_function(),
                        error,
                        content: Vec::new(),
                    });
                }
                ProgramOperationRecord::ExternalPending {
                    id,
                    function,
                    pending,
                } => {
                    validate_unique_operation_id(&mut operation_ids, &id)?;
                    validate_external_function_record(&function)?;
                    let pending = *pending;
                    validate_pending_program_record(&id, &function, &pending)?;
                    let pending = PendingProgramOperation::try_from((id.as_str(), pending))?;
                    tool_state.push(ToolState::Pending {
                        kind: ToolKind::External,
                        id,
                        function: function.into(),
                    });
                    pending_operations.push(pending);
                }
                ProgramOperationRecord::ExternalResolved {
                    id,
                    function,
                    value,
                    content,
                } => {
                    validate_unique_operation_id(&mut operation_ids, &id)?;
                    validate_external_function_record(&function)?;
                    tool_state.push(ToolState::Resolved {
                        kind: ToolKind::External,
                        id,
                        function: function.into(),
                        result: value,
                        content,
                    });
                }
                ProgramOperationRecord::ExternalRejected {
                    id,
                    function,
                    error,
                    content,
                } => {
                    validate_unique_operation_id(&mut operation_ids, &id)?;
                    validate_external_function_record(&function)?;
                    tool_state.push(ToolState::Rejected {
                        kind: ToolKind::External,
                        id,
                        function: function.into(),
                        error,
                        content,
                    });
                }
            }
        }
        if pending_operations.is_empty() {
            return Err(
                "pending program execution must contain at least one operation".to_string(),
            );
        }
        Ok(Self {
            partial_evaluation: PartialEvaluation {
                code: source,
                input: replay_input(),
                tool_state,
            },
            pending_operations,
            round,
        })
    }
}

const INTERNAL_STEP_FUNCTION: &str = "__internal__.step";

fn replay_input() -> Value {
    Value::Object(serde_json::Map::new())
}

fn internal_step_function() -> ToolFunction {
    ToolFunction {
        name: INTERNAL_STEP_FUNCTION.to_string(),
        arguments: Value::Object(serde_json::Map::new()),
    }
}

fn validate_unique_operation_id(ids: &mut HashSet<String>, id: &str) -> Result<(), String> {
    if id.trim().is_empty() {
        return Err("program operation ID must not be empty".to_string());
    }
    if !ids.insert(id.to_string()) {
        return Err(format!("duplicate program operation ID {id:?}"));
    }
    Ok(())
}

fn validate_internal_operation(
    id: &str,
    function: &ToolFunction,
    content: &[ContentBlock],
) -> Result<(), String> {
    if function.name != INTERNAL_STEP_FUNCTION
        || !function
            .arguments
            .as_object()
            .is_some_and(serde_json::Map::is_empty)
    {
        return Err("internal program operations must be empty-argument step records".to_string());
    }
    if !content.is_empty() {
        return Err(format!(
            "internal program operation {id:?} cannot contain rich content"
        ));
    }
    Ok(())
}

fn validate_external_function(function: &ToolFunction) -> Result<(), String> {
    validate_external_function_record(&ProgramFunctionRecord::from(function))
}

fn validate_external_function_record(function: &ProgramFunctionRecord) -> Result<(), String> {
    if function.name.trim().is_empty() || function.name == INTERNAL_STEP_FUNCTION {
        return Err("external program function name is invalid".to_string());
    }
    Ok(())
}

fn validate_pending_call(id: &str, call: &ExternalToolCall) -> Result<(), String> {
    if call.call_id != id {
        return Err(format!(
            "pending program operation {id:?} does not match continuation {:?}",
            call.call_id
        ));
    }
    if call.origin != ToolOrigin::Programmatic {
        return Err(format!(
            "pending program operation {id:?} must use programmatic origin"
        ));
    }
    let expected_action_id = effect_id_for_operation(ToolOrigin::Programmatic, id);
    if call.action_id != expected_action_id {
        return Err(format!(
            "program tool action ID {:?} does not match derived action ID {expected_action_id:?}",
            call.action_id
        ));
    }
    Ok(())
}

fn validate_pending_program_record(
    id: &str,
    function: &ProgramFunctionRecord,
    pending: &PendingProgramRecord,
) -> Result<(), String> {
    let call = match pending {
        PendingProgramRecord::AwaitingPreHook { call, .. }
        | PendingProgramRecord::Pending { call }
        | PendingProgramRecord::AwaitingPostHook { call, .. } => call,
    };
    validate_pending_call(id, call)?;
    if let ExternalTool::Provided {
        group_name,
        tool_name,
        ..
    } = &call.call
        && (group_name.trim().is_empty() || tool_name.trim().is_empty())
    {
        return Err("program provided-tool identity must not be empty".to_string());
    }
    let Some(programmatic_name) = call.call.programmatic_name() else {
        return Err(format!(
            "program operation {id:?} targets a direct-only built-in"
        ));
    };
    if function.name != programmatic_name {
        return Err(format!(
            "program operation {id:?} targets {:?}, but its pending external call targets {programmatic_name:?}",
            function.name
        ));
    }
    match pending {
        PendingProgramRecord::AwaitingPreHook {
            hook_action_id,
            hook_binding_ids,
            ..
        } => {
            validate_pending_hook(
                call,
                hook_action_id,
                hook_binding_ids,
                HookPoint::PreToolCall,
            )?;
        }
        PendingProgramRecord::Pending { .. } => {}
        PendingProgramRecord::AwaitingPostHook {
            hook_action_id,
            hook_binding_ids,
            ..
        } => {
            validate_pending_hook(
                call,
                hook_action_id,
                hook_binding_ids,
                HookPoint::PostToolCall,
            )?;
        }
    }
    Ok(())
}

fn validate_pending_hook(
    call: &ExternalToolCall,
    action_id: &str,
    binding_ids: &[String],
    point: HookPoint,
) -> Result<(), String> {
    let expected = hook_action_id(&call.action_id, point);
    if action_id != expected {
        return Err(format!(
            "program hook action ID {action_id:?} does not match derived action ID {expected:?}"
        ));
    }
    if binding_ids.is_empty() {
        return Err("pending program hook must contain at least one binding ID".to_string());
    }
    let mut unique = HashSet::new();
    if binding_ids
        .iter()
        .any(|id| id.trim().is_empty() || !unique.insert(id))
    {
        return Err("pending program hook binding IDs must be non-empty and unique".to_string());
    }
    Ok(())
}

fn programmatic_tool_result_value(
    structured_content: &StructuredContent,
    content: &[ContentBlock],
) -> Value {
    if let Some(value) = structured_content
        .as_value()
        .filter(|value| !value.is_null())
    {
        return value.clone();
    }
    let text = content
        .iter()
        .filter_map(|block| match block {
            ContentBlock::Text(content) => Some(content.text.as_str()),
            _ => None,
        })
        .collect::<String>();
    if !text.is_empty() {
        return serde_json::from_str(&text).unwrap_or(Value::String(text));
    }
    serde_json::to_value(content).unwrap_or(Value::Null)
}

fn retained_program_content(content: Vec<ContentBlock>) -> Vec<ContentBlock> {
    content
        .into_iter()
        .filter(|block| !matches!(block, ContentBlock::Text(_)))
        .collect()
}

impl From<&ToolFunction> for ProgramFunctionRecord {
    fn from(function: &ToolFunction) -> Self {
        Self {
            name: function.name.clone(),
            arguments: function.arguments.clone(),
        }
    }
}

impl From<ProgramFunctionRecord> for ToolFunction {
    fn from(function: ProgramFunctionRecord) -> Self {
        Self {
            name: function.name,
            arguments: function.arguments,
        }
    }
}

impl From<&PendingProgramOperation> for PendingProgramRecord {
    fn from(operation: &PendingProgramOperation) -> Self {
        match &operation.stage {
            ProgramOperationStage::AwaitingPreHook {
                hook_action_id,
                hook_binding_ids,
            } => Self::AwaitingPreHook {
                call: operation.call.clone(),
                hook_action_id: hook_action_id.clone(),
                hook_binding_ids: hook_binding_ids.clone(),
            },
            ProgramOperationStage::Pending => Self::Pending {
                call: operation.call.clone(),
            },
            ProgramOperationStage::AwaitingPostHook {
                hook_action_id,
                hook_binding_ids,
                result,
            } => Self::AwaitingPostHook {
                call: operation.call.clone(),
                hook_action_id: hook_action_id.clone(),
                hook_binding_ids: hook_binding_ids.clone(),
                result: result.clone(),
            },
        }
    }
}

impl TryFrom<(&str, PendingProgramRecord)> for PendingProgramOperation {
    type Error = String;

    fn try_from((id, pending): (&str, PendingProgramRecord)) -> Result<Self, Self::Error> {
        let operation = match pending {
            PendingProgramRecord::AwaitingPreHook {
                call,
                hook_action_id,
                hook_binding_ids,
            } => Self {
                call,
                stage: ProgramOperationStage::AwaitingPreHook {
                    hook_action_id,
                    hook_binding_ids,
                },
            },
            PendingProgramRecord::Pending { call } => Self {
                call,
                stage: ProgramOperationStage::Pending,
            },
            PendingProgramRecord::AwaitingPostHook {
                call,
                hook_action_id,
                hook_binding_ids,
                result,
            } => Self {
                call,
                stage: ProgramOperationStage::AwaitingPostHook {
                    hook_action_id,
                    hook_binding_ids,
                    result,
                },
            },
        };
        validate_pending_call(id, &operation.call)?;
        Ok(operation)
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;
    use crate::core::tools::external::RuntimeBuiltinToolName;

    #[test]
    fn replay_content_omits_text_already_projected_into_the_sandbox_value() {
        let image: ContentBlock = serde_json::from_value(json!({
            "type": "image",
            "data": "aW1hZ2U=",
            "mimeType": "image/png",
        }))
        .expect("image content block decodes");

        assert_eq!(
            retained_program_content(vec![ContentBlock::text("duplicate"), image.clone()]),
            vec![image],
        );
    }
    #[test]
    fn programmatic_result_prefers_structured_content_then_parses_text_fallbacks() {
        assert_eq!(
            programmatic_tool_result_value(
                &StructuredContent::present(json!({"answer": 42})),
                &[ContentBlock::text("ignored")],
            ),
            json!({"answer": 42})
        );
        assert_eq!(
            programmatic_tool_result_value(
                &StructuredContent::Absent,
                &[ContentBlock::text(r#"{"answer":42}"#)],
            ),
            json!({"answer": 42})
        );
        assert_eq!(
            programmatic_tool_result_value(
                &StructuredContent::present(Value::Null),
                &[ContentBlock::text("plain text")],
            ),
            Value::String("plain text".to_string())
        );
    }

    #[test]
    fn restore_rejects_semantically_inconsistent_pending_records() {
        let operation_id = "operation-1";
        let arguments = json!({ "path": "original.txt" });
        let call = ExternalToolCall {
            action_id: effect_id_for_operation(ToolOrigin::Programmatic, operation_id),
            call_id: operation_id.to_string(),
            origin: ToolOrigin::Programmatic,
            call: ExternalTool::RuntimeBuiltin {
                name: RuntimeBuiltinToolName::FileSystemReadFile,
                invocation_name: "read_file".to_string(),
                arguments: arguments.clone(),
            },
        };
        let function = ProgramFunctionRecord {
            name: "read_file".to_string(),
            arguments,
        };
        let restore = |function, pending| {
            ProgramExecution::restore(
                "async function main() {}".to_string(),
                vec![ProgramOperationRecord::ExternalPending {
                    id: operation_id.to_string(),
                    function,
                    pending: Box::new(pending),
                }],
                0,
            )
            .unwrap_err()
        };

        assert!(
            restore(
                ProgramFunctionRecord {
                    name: "write_file".to_string(),
                    ..function.clone()
                },
                PendingProgramRecord::Pending { call: call.clone() },
            )
            .contains("targets")
        );

        let mut invalid_action = call.clone();
        invalid_action.action_id = "tool:programmatic:wrong".to_string();
        assert!(
            restore(
                function.clone(),
                PendingProgramRecord::Pending {
                    call: invalid_action,
                },
            )
            .contains("derived action ID")
        );

        assert!(
            ProgramExecution::restore(
                "async function main() {}".to_string(),
                vec![ProgramOperationRecord::InternalResolved {
                    id: "step-1".to_string(),
                    value: Value::Null,
                }],
                0,
            )
            .unwrap_err()
            .contains("at least one operation")
        );

        let mut max_round = ProgramExecution::restore(
            "async function main() {}".to_string(),
            vec![ProgramOperationRecord::ExternalPending {
                id: operation_id.to_string(),
                function: function.clone(),
                pending: Box::new(PendingProgramRecord::Pending { call: call.clone() }),
            }],
            u32::MAX,
        )
        .unwrap();
        let overflow = max_round
            .resolve_pending_tool(
                &call.action_id,
                ToolResult::Success {
                    content: Vec::new(),
                    structured_content: StructuredContent::Absent,
                    meta: None,
                },
            )
            .unwrap_err();
        assert!(overflow.detail().contains("round overflowed"));
    }
}
