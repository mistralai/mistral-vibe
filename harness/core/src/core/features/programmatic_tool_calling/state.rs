use std::collections::HashSet;

use serde_json::Value;

use crate::core::action_id;
use crate::core::error::CoreError;
use crate::core::features::permissions::GrantKey;
use crate::core::features::programmatic_tool_calling::ProgrammaticName;
use crate::core::features::programmatic_tool_calling::execution::serialized_len;
use crate::core::features::programmatic_tool_calling::model::{
    PartialEvaluation, ToolFunction, ToolKind, ToolState,
};
use crate::core::features::programmatic_tool_calling::time_budget::{
    ProgramClock, is_shortened_sleep, shortened_sleep_result, sleep_seconds,
};
use crate::core::hooks::{HookPoint, hook_action_id};
use crate::core::step_protocol::Action;
use crate::core::tools::execution::external_call::{
    ApprovalName, ExternalCallStage, HookSide, PendingExternalCall,
};
use crate::core::tools::external::{
    ExternalTool, ExternalToolCall, ToolOrigin, effect_id_for_operation,
};
use crate::core::wire::content::ContentBlock;
use crate::core::wire::tool::{StructuredContent, ToolResult};

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct PendingProgramOperation {
    pub(crate) external: PendingExternalCall,
    /// For a sent sleep: its length after pre-tool hooks, before Core fit it
    /// to the time budget. `None` for other calls, and for sleeps restored
    /// from checkpoints that did not record it.
    pub(crate) requested_sleep_seconds: Option<f64>,
}

/// How far an unfinished program operation got before its program stopped.
pub(super) enum UnfinishedOperation {
    /// Waiting for a pre-tool-call hook or an approval: the tool never ran.
    NotStarted,
    /// Sent to the Runtime: the tool may have had effects.
    Running,
    /// The tool ran; only its post-tool-call hook was left.
    Finished {
        /// The tool returned a failure.
        failed: bool,
    },
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct ProgramExecution {
    partial_evaluation: PartialEvaluation,
    pending_operations: Vec<PendingProgramOperation>,
    round: u32,
    clock: ProgramClock,
}

#[derive(Debug)]
pub(super) struct ProgramContinuation {
    pub(super) partial_evaluation: PartialEvaluation,
    pub(super) round: u32,
    pub(super) clock: ProgramClock,
}

impl ProgramContinuation {
    pub(super) fn after_round(
        partial_evaluation: PartialEvaluation,
        round: u32,
        clock: ProgramClock,
    ) -> Result<Self, CoreError> {
        let round = round
            .checked_add(1)
            .ok_or_else(|| CoreError::invariant("program replay round overflowed"))?;
        Ok(Self {
            partial_evaluation,
            round,
            clock,
        })
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
        pending: Box<PendingProgramOperation>,
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

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct ProgramCapture {
    source: String,
    operations: Vec<ProgramOperationRecord>,
    round: u32,
    clock: ProgramClock,
}

impl ProgramCapture {
    pub(crate) fn clock(&self) -> ProgramClock {
        self.clock
    }

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
    pub(super) fn awaiting_pre_hook(call: ExternalToolCall, hook_binding_ids: Vec<String>) -> Self {
        Self {
            external: PendingExternalCall::awaiting_pre_hook(call, hook_binding_ids),
            requested_sleep_seconds: None,
        }
    }

    /// An operation sent as `call`, which Core fit to the time budget from
    /// `requested`.
    pub(super) fn pending(requested: &ExternalToolCall, call: ExternalToolCall) -> Self {
        Self {
            external: PendingExternalCall::pending(call),
            requested_sleep_seconds: sleep_seconds(requested),
        }
    }

    pub(super) fn awaiting_approval(
        call: ExternalToolCall,
        grant_key: GrantKey,
        programmatic_name: ProgrammaticName,
    ) -> Self {
        Self {
            external: PendingExternalCall::awaiting_approval(
                call,
                grant_key,
                ApprovalName::Programmatic(programmatic_name),
            ),
            requested_sleep_seconds: None,
        }
    }
}

pub(super) fn resolve_tool_state(
    partial_evaluation: &mut PartialEvaluation,
    operation_id: &str,
    result: ToolResult,
) -> Result<(), CoreError> {
    let operation = partial_evaluation
        .tool_state
        .iter_mut()
        .find(|operation| matches!(operation, ToolState::Pending { id, .. } if id == operation_id))
        .ok_or_else(|| {
            CoreError::invariant(format!("pending operation {operation_id:?} was not found"))
        })?;
    let ToolState::Pending { kind, id, function } = operation.clone() else {
        unreachable!();
    };
    *operation = settled_tool_state(kind, id, function, result);
    Ok(())
}

fn settled_tool_state(
    kind: ToolKind,
    id: String,
    function: ToolFunction,
    result: ToolResult,
) -> ToolState {
    match result {
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
    }
}

impl ProgramExecution {
    pub(super) fn new(
        partial_evaluation: PartialEvaluation,
        pending_operations: Vec<PendingProgramOperation>,
        round: u32,
        clock: ProgramClock,
        now_unix_ms: u64,
    ) -> Self {
        let mut execution = Self {
            partial_evaluation,
            pending_operations,
            round,
            clock,
        };
        execution.sync_clock(now_unix_ms);
        execution
    }

    /// Pauses the budget clock while an operation waits for an approval or a
    /// hook, and runs it otherwise.
    pub(super) fn sync_clock(&mut self, now_unix_ms: u64) {
        let waiting = self
            .pending_operations
            .iter()
            .any(|operation| !matches!(operation.external.stage, ExternalCallStage::Pending));
        self.clock.set_paused(waiting, now_unix_ms);
    }

    /// Turns a sleep that the time budget cut short into its catchable budget
    /// failure.
    pub(super) fn settle_sleep_result(
        &mut self,
        action_id: &str,
        result: ToolResult,
        budget_ms: u64,
        now_unix_ms: u64,
    ) -> ToolResult {
        let Some(operation) = self
            .pending_operations
            .iter()
            .find(|operation| operation.external.awaits_tool(action_id))
        else {
            return result;
        };
        let call = &operation.external.call;
        // Checkpoints that did not record the requested length fall back to
        // the length the program asked for.
        let requested_seconds = operation.requested_sleep_seconds.or_else(|| {
            self.partial_evaluation
                .tool_state
                .iter()
                .find_map(|operation| match operation {
                    ToolState::Pending { id, function, .. } if *id == call.call_id => {
                        function.arguments.get("seconds").and_then(Value::as_f64)
                    }
                    _ => None,
                })
        });
        let shortened = requested_seconds
            .is_some_and(|requested_seconds| is_shortened_sleep(call, requested_seconds));
        // A sleep cut short by the budget rejects even if the Runtime timer
        // fired a little early, so the program never mistakes it for a full sleep.
        if shortened && matches!(result, ToolResult::Success { .. }) {
            return shortened_sleep_result(budget_ms, self.clock.used_at(now_unix_ms));
        }
        result
    }

    pub(super) fn clock(&self) -> ProgramClock {
        self.clock
    }

    pub(super) fn round(&self) -> u32 {
        self.round
    }

    pub(super) fn partial_evaluation(&self) -> &PartialEvaluation {
        &self.partial_evaluation
    }

    pub(super) fn tool_state(&self) -> &[ToolState] {
        &self.partial_evaluation.tool_state
    }

    /// How far the unfinished operation `operation_id` got. Operations
    /// without a pending stage never started.
    pub(super) fn unfinished_operation(&self, operation_id: &str) -> UnfinishedOperation {
        match self
            .pending_operations
            .iter()
            .find(|operation| operation.external.call.call_id == operation_id)
            .map(|operation| &operation.external.stage)
        {
            Some(ExternalCallStage::Pending) => UnfinishedOperation::Running,
            Some(ExternalCallStage::AwaitingPostHook { result, .. }) => {
                UnfinishedOperation::Finished {
                    failed: matches!(result, ToolResult::Failure { .. }),
                }
            }
            Some(
                ExternalCallStage::AwaitingPreHook { .. }
                | ExternalCallStage::AwaitingApproval { .. },
            )
            | None => UnfinishedOperation::NotStarted,
        }
    }

    pub(crate) fn pending_actions(&self, turn_id: &str) -> Vec<Action> {
        self.pending_operations
            .iter()
            .map(|operation| operation.external.action(turn_id))
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
            .find(|operation| operation.external.awaits_tool(action_id))
            .map(|operation| &operation.external.call)
    }

    pub(super) fn pending_hook(&self, action_id: &str) -> Option<(HookSide, &ExternalToolCall)> {
        self.pending_operations.iter().find_map(|operation| {
            operation
                .external
                .awaited_hook(action_id)
                .map(|side| (side, &operation.external.call))
        })
    }

    /// Bytes held for replay once `call` holds `result`: whole while it awaits a
    /// post-tool hook, otherwise only what replay keeps of it.
    pub(super) fn held_result_bytes(
        &self,
        call: &ExternalToolCall,
        result: &ToolResult,
        awaiting_post_hook: bool,
    ) -> usize {
        let mut incoming = serialized_len(result);
        let mut held = 0;
        for operation in &self.partial_evaluation.tool_state {
            match operation {
                ToolState::Pending { kind, id, function }
                    if *id == call.call_id && !awaiting_post_hook =>
                {
                    incoming = serialized_len(&settled_tool_state(
                        kind.clone(),
                        id.clone(),
                        function.clone(),
                        result.clone(),
                    ));
                }
                ToolState::Pending { .. } => {}
                settled => held += serialized_len(settled),
            }
        }
        for operation in &self.pending_operations {
            if let ExternalCallStage::AwaitingPostHook { result, .. } = &operation.external.stage
                && operation.external.call.action_id != call.action_id
            {
                held += serialized_len(result);
            }
        }
        held + incoming
    }

    /// Sends the operation as `sent_call`, which Core fit to the time budget
    /// from the post-hook call `effective_call`.
    pub(super) fn continue_after_pre_hook(
        &mut self,
        hook_action_id: &str,
        effective_call: &ExternalToolCall,
        sent_call: ExternalToolCall,
        turn_id: &str,
    ) -> Result<Action, CoreError> {
        let operation = self.awaiting_pre_hook_operation_mut(hook_action_id)?;
        operation.requested_sleep_seconds = sleep_seconds(effective_call);
        Ok(operation.external.send(sent_call, turn_id))
    }

    pub(super) fn await_approval_after_pre_hook(
        &mut self,
        hook_action_id: &str,
        effective_call: ExternalToolCall,
        grant_key: GrantKey,
        programmatic_name: ProgrammaticName,
        turn_id: &str,
    ) -> Result<Action, CoreError> {
        let operation = self.awaiting_pre_hook_operation_mut(hook_action_id)?;
        Ok(operation.external.await_approval(
            effective_call,
            grant_key,
            ApprovalName::Programmatic(programmatic_name),
            turn_id,
        ))
    }

    fn awaiting_pre_hook_operation_mut(
        &mut self,
        hook_action_id: &str,
    ) -> Result<&mut PendingProgramOperation, CoreError> {
        self.pending_operations
            .iter_mut()
            .find(|operation| {
                operation.external.awaited_hook(hook_action_id) == Some(HookSide::Pre)
            })
            .ok_or_else(|| CoreError::invariant("program pre-hook operation was not found"))
    }

    pub(crate) fn pending_approval(&self, approval_action_id: &str) -> Option<&ExternalToolCall> {
        let operation_index = self.pending_approval_index(approval_action_id)?;
        Some(&self.pending_operations[operation_index].external.call)
    }

    /// Starts an approved operation, fitting a wait to the remaining time
    /// budget. Returns the result instead when the operation cannot start: a
    /// sleep after the budget is used up.
    pub(crate) fn approve_pending_approval(
        &mut self,
        approval_action_id: &str,
        budget_ms: u64,
        now_unix_ms: u64,
        turn_id: &str,
    ) -> Option<Result<Action, ToolResult>> {
        let operation_index = self.pending_approval_index(approval_action_id)?;
        let operation = &mut self.pending_operations[operation_index];
        let sent_call = match self
            .clock
            .fit(&operation.external.call, budget_ms, now_unix_ms)
        {
            Ok(call) => call,
            Err(result) => return Some(Err(*result)),
        };
        operation.requested_sleep_seconds = sleep_seconds(&operation.external.call);
        let action = operation.external.send(sent_call, turn_id);
        self.sync_clock(now_unix_ms);
        Some(Ok(action))
    }

    fn pending_approval_index(&self, approval_action_id: &str) -> Option<usize> {
        self.pending_operations
            .iter()
            .position(|operation| operation.external.awaits_approval(approval_action_id))
    }

    pub(super) fn await_post_hook(
        &mut self,
        action_id: &str,
        hook_binding_ids: Vec<String>,
        result: ToolResult,
        turn_id: &str,
    ) -> Result<Action, CoreError> {
        let operation = self
            .pending_operations
            .iter_mut()
            .find(|operation| operation.external.awaits_tool(action_id))
            .ok_or_else(|| CoreError::invariant("program tool operation was not found"))?;
        Ok(operation
            .external
            .await_post_hook(hook_binding_ids, result, turn_id))
    }

    /// Settles the operation whose current stage waits for `action_id`, and
    /// returns the next round once no operation is left.
    pub(super) fn resolve_pending(
        &mut self,
        action_id: &str,
        result: ToolResult,
    ) -> Result<Option<ProgramContinuation>, CoreError> {
        let operation_index = self
            .pending_operations
            .iter()
            .position(|operation| operation.external.action_id() == action_id)
            .ok_or_else(|| CoreError::invariant("pending program operation was not found"))?;
        let operation = self.pending_operations.remove(operation_index);
        resolve_tool_state(
            &mut self.partial_evaluation,
            &operation.external.call.call_id,
            result,
        )?;
        if !self.pending_operations.is_empty() {
            return Ok(None);
        }
        Ok(Some(ProgramContinuation::after_round(
            self.partial_evaluation.clone(),
            self.round,
            self.clock,
        )?))
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
                    if pending.external.call.call_id != *id {
                        return Err(format!(
                            "pending program operation {id:?} does not match continuation {:?}",
                            pending.external.call.call_id
                        ));
                    }
                    let function = ProgramFunctionRecord::from(function);
                    validate_pending_program_operation(id, &function, pending)?;
                    ProgramOperationRecord::ExternalPending {
                        id: id.clone(),
                        function,
                        pending: Box::new(pending.clone()),
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
            clock: self.clock,
        })
    }

    pub(crate) fn restore(
        source: String,
        operations: Vec<ProgramOperationRecord>,
        round: u32,
        clock: ProgramClock,
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
                    validate_pending_program_operation(&id, &function, &pending)?;
                    tool_state.push(ToolState::Pending {
                        kind: ToolKind::External,
                        id,
                        function: function.into(),
                    });
                    pending_operations.push(*pending);
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
        let mut clock = clock;
        let waiting = pending_operations
            .iter()
            .any(|operation| !matches!(operation.external.stage, ExternalCallStage::Pending));
        if waiting && clock.paused_since_unix_ms.is_none() {
            // Checkpoints written before pauses were recorded do not say when
            // the wait started; pause from the program's start so the
            // approval or hook wait does not count.
            clock.paused_since_unix_ms = clock.started_at_unix_ms;
        }
        Ok(Self {
            partial_evaluation: PartialEvaluation {
                code: source,
                input: replay_input(),
                tool_state,
            },
            pending_operations,
            round,
            clock,
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

fn validate_pending_program_operation(
    id: &str,
    function: &ProgramFunctionRecord,
    pending: &PendingProgramOperation,
) -> Result<(), String> {
    let call = &pending.external.call;
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
    let Some(programmatic_name) = call.call.programmatic_runtime_name() else {
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
    match &pending.external.stage {
        ExternalCallStage::AwaitingPreHook {
            hook_action_id,
            hook_binding_ids,
        } => {
            validate_pending_hook(
                call,
                hook_action_id,
                hook_binding_ids,
                HookPoint::PreToolCall,
            )?;
        }
        ExternalCallStage::AwaitingApproval {
            approval_action_id, ..
        } => {
            let expected_action_id = action_id::approval(&call.action_id);
            if approval_action_id != &expected_action_id {
                return Err(format!(
                    "program approval action ID {approval_action_id:?} does not match derived action ID {expected_action_id:?}"
                ));
            }
        }
        ExternalCallStage::Pending => {}
        ExternalCallStage::AwaitingPostHook {
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
        .map(|block| match block {
            ContentBlock::Text(content) => Some(content.text.as_str()),
            _ => None,
        })
        .collect::<Option<String>>()
        .unwrap_or_default();
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
                ProgramClock::default(),
            )
            .unwrap_err()
        };

        assert!(
            restore(
                ProgramFunctionRecord {
                    name: "write_file".to_string(),
                    ..function.clone()
                },
                PendingProgramOperation::pending(&call, call.clone()),
            )
            .contains("targets")
        );

        let mut invalid_action = call.clone();
        invalid_action.action_id = "tool:programmatic:wrong".to_string();
        assert!(
            restore(
                function.clone(),
                PendingProgramOperation::pending(&invalid_action, invalid_action.clone()),
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
                ProgramClock::default(),
            )
            .unwrap_err()
            .contains("at least one operation")
        );

        let mut max_round = ProgramExecution::restore(
            "async function main() {}".to_string(),
            vec![ProgramOperationRecord::ExternalPending {
                id: operation_id.to_string(),
                function: function.clone(),
                pending: Box::new(PendingProgramOperation::pending(&call, call.clone())),
            }],
            u32::MAX,
            ProgramClock::default(),
        )
        .unwrap();
        let overflow = max_round
            .resolve_pending(
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

    /// A checkpoint written before sleeps recorded their requested length
    /// compares the sent sleep with the length the program asked for.
    #[test]
    fn restored_sleep_without_a_requested_length_compares_with_the_program_arguments() {
        let operation_id = "operation-1";
        let sent = ExternalToolCall {
            action_id: effect_id_for_operation(ToolOrigin::Programmatic, operation_id),
            call_id: operation_id.to_string(),
            origin: ToolOrigin::Programmatic,
            call: ExternalTool::RuntimeBuiltin {
                name: RuntimeBuiltinToolName::SelfSleep,
                invocation_name: "sleep".to_string(),
                arguments: json!({"seconds": 5.0}),
            },
        };
        let settle = |requested_sleep_seconds| {
            let mut execution = ProgramExecution::restore(
                "async function main() {}".to_string(),
                vec![ProgramOperationRecord::ExternalPending {
                    id: operation_id.to_string(),
                    function: ProgramFunctionRecord {
                        name: "sleep".to_string(),
                        arguments: json!({"seconds": 30}),
                    },
                    pending: Box::new(PendingProgramOperation {
                        external: PendingExternalCall::pending(sent.clone()),
                        requested_sleep_seconds,
                    }),
                }],
                0,
                ProgramClock::started_at(0),
            )
            .unwrap();
            execution.settle_sleep_result(
                &sent.action_id,
                ToolResult::Success {
                    content: Vec::new(),
                    structured_content: StructuredContent::Absent,
                    meta: None,
                },
                60_000,
                60_000,
            )
        };

        assert!(matches!(settle(None), ToolResult::Failure { .. }));
        assert!(matches!(settle(Some(5.0)), ToolResult::Success { .. }));
    }

    /// A checkpoint written before pauses were recorded has a start time but
    /// no pause; restoring it while an operation waits for a hook pauses the
    /// clock, so the wait does not count toward the time budget.
    #[test]
    fn restore_without_a_recorded_pause_pauses_a_waiting_program() {
        let operation_id = "operation-1";
        let arguments = json!({ "path": "notes.txt" });
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
        let restore = |pending| {
            ProgramExecution::restore(
                "async function main() {}".to_string(),
                vec![ProgramOperationRecord::ExternalPending {
                    id: operation_id.to_string(),
                    function: ProgramFunctionRecord {
                        name: "read_file".to_string(),
                        arguments: arguments.clone(),
                    },
                    pending: Box::new(pending),
                }],
                0,
                ProgramClock::started_at(1_000),
            )
            .unwrap()
            .clock()
        };

        let waiting = restore(PendingProgramOperation::awaiting_pre_hook(
            call.clone(),
            vec!["pre-tool".to_string()],
        ));
        assert_eq!(waiting.used_at(120_000), 0);

        let running = restore(PendingProgramOperation::pending(&call, call.clone()));
        assert_eq!(running.used_at(120_000), 119_000);
    }
}
