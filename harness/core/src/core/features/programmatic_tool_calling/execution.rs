use std::ops::ControlFlow;

use crate::core::error::CoreError;
use serde_json::Value;
use serde_json::json;

use crate::core::features::permissions::GrantKey;
use crate::core::features::programmatic_tool_calling::code_mode::{
    self, CodeResult, EvaluationOutcome, EvaluationRequest, ProgramLogs,
};
use crate::core::features::programmatic_tool_calling::model::PartialEvaluation;
use crate::core::features::programmatic_tool_calling::model::ToolKind;
use crate::core::features::programmatic_tool_calling::model::ToolState;
use crate::core::features::programmatic_tool_calling::state::{
    PendingProgramOperation, ProgramContinuation, ProgramExecution, UnfinishedOperation,
    resolve_tool_state,
};
use crate::core::features::programmatic_tool_calling::time_budget::{
    BUDGET_EXCEEDED, ProgramClock, budget_exceeded_error,
};
use crate::core::features::programmatic_tool_calling::{
    CompletedProgramResult, ProgramContext, ProgramOutcome, ProgrammaticName, TypeScriptTool,
};
use crate::core::hooks::{
    HookResult, PreToolCallOutput, hook_failure_tool_result, skipped_tool_result,
};
use crate::core::step_protocol::Action;
use crate::core::step_protocol::DeterminismContext;
use crate::core::tools::execution::external_call::{Admission, HookSide};
use crate::core::tools::external::{
    ExternalTool, ExternalToolCall, ToolOrigin, effect_id_for_operation,
};
use crate::core::tools::result::model_visible_content_block;
use crate::core::wire::content::ContentBlock;
use crate::core::wire::content::text_content;
use crate::core::wire::tool::{
    ProtocolError, StructuredContent, ToolCall, ToolResult, echoed_tool_name,
};

pub(super) fn start_program_execution(
    context: ProgramContext<'_>,
    call: &ToolCall,
    determinism: DeterminismContext,
    accepted_results: &mut Vec<AcceptedProgramResult>,
) -> Result<(ProgramOutcome, Vec<Action>), CoreError> {
    let code = call
        .arguments
        .get("code")
        .and_then(Value::as_str)
        .ok_or_else(|| CoreError::invalid_command("run_typescript code must be a string"))?;
    drive_program_execution(
        context,
        call,
        ProgramContinuation {
            partial_evaluation: PartialEvaluation {
                code: code.to_string(),
                input: json!({}),
                tool_state: Vec::new(),
            },
            round: 0,
            clock: ProgramClock::started_at(determinism.time_unix_ms),
        },
        determinism,
        accepted_results,
    )
}

enum ProgramToolResultDisposition {
    Commit {
        call: ExternalToolCall,
    },
    AwaitPostHook {
        call: ExternalToolCall,
        hook_binding_ids: Vec<String>,
    },
}

struct ClassifiedProgramInput {
    accepted_result: Option<AcceptedProgramResult>,
    transition: ProgramInputTransition,
}

impl ClassifiedProgramInput {
    fn resume_after_hook(action_id: String, call: &ExternalToolCall, result: ToolResult) -> Self {
        Self {
            accepted_result: Some(accepted_program_result(call.clone(), result.clone())),
            transition: ProgramInputTransition::Resume { action_id, result },
        }
    }
}

enum ProgramInputTransition {
    /// Settles the operation that waits for `action_id` and resumes the program.
    Resume {
        action_id: String,
        result: ToolResult,
    },
    AwaitPostHook {
        action_id: String,
        hook_binding_ids: Vec<String>,
        result: ToolResult,
    },
    AwaitApprovalAfterPreHook {
        action_id: String,
        effective_call: ExternalToolCall,
        grant_key: GrantKey,
        programmatic_name: ProgrammaticName,
    },
    ContinueAfterPreHook {
        action_id: String,
        /// The call after pre-tool hooks.
        effective_call: ExternalToolCall,
        /// `effective_call` fit to the time budget: the call Core sends.
        sent_call: ExternalToolCall,
    },
}

pub(crate) enum ProgramInput {
    ToolResult {
        action_id: String,
        result: ToolResult,
    },
    ApprovalResult {
        action_id: String,
        result: ToolResult,
    },
    HookCompleted {
        action_id: String,
        result: HookResult,
    },
    HookFailed {
        action_id: String,
        error: ProtocolError,
    },
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct AcceptedProgramResult {
    pub(crate) action_id: String,
    pub(crate) operation_id: String,
    pub(crate) call: ExternalTool,
    pub(crate) result: ToolResult,
}

pub(crate) enum ProgramAdvance {
    Pending {
        actions: Vec<Action>,
    },
    Completed {
        completed: Box<CompletedProgramResult>,
    },
}

pub(crate) struct ProgramTransition {
    pub(crate) advance: ProgramAdvance,
    pub(crate) accepted_results: Vec<AcceptedProgramResult>,
}

fn drive_program_execution(
    context: ProgramContext<'_>,
    call: &ToolCall,
    mut continuation: ProgramContinuation,
    determinism: DeterminismContext,
    accepted_results: &mut Vec<AcceptedProgramResult>,
) -> Result<(ProgramOutcome, Vec<Action>), CoreError> {
    // One Step can settle many permission-denied replay rounds.
    // Keep replay iterative so each round does not grow the call stack.
    loop {
        match evaluate_program_round(context, call, continuation, determinism, accepted_results)? {
            ControlFlow::Break(outcome) => return Ok(outcome),
            ControlFlow::Continue(next) => continuation = next,
        }
    }
}

fn evaluate_program_round(
    context: ProgramContext<'_>,
    call: &ToolCall,
    continuation: ProgramContinuation,
    determinism: DeterminismContext,
    accepted_results: &mut Vec<AcceptedProgramResult>,
) -> Result<ControlFlow<(ProgramOutcome, Vec<Action>), ProgramContinuation>, CoreError> {
    let ProgramContinuation {
        partial_evaluation,
        round,
        mut clock,
    } = continuation;
    let now_unix_ms = determinism.time_unix_ms;
    let budget_ms = context.settings().max_elapsed_ms;
    clock.start_round(now_unix_ms);
    let used_ms = clock.used_at(now_unix_ms);
    let budget_used = used_ms >= budget_ms;
    let execution_id = typescript_execution_id(context.message_count(), &call.id, round);
    match code_mode::evaluate(EvaluationRequest {
        partial_evaluation: &partial_evaluation,
        tools: context.descriptors(),
        execution_id: &execution_id,
        round,
        determinism,
        max_program_effects: context.settings().max_effects,
        max_program_operations: context.settings().max_operations,
        budget_error: budget_used.then(|| budget_exceeded_error(budget_ms, used_ms)),
    })
    .map_err(CoreError::invariant)?
    {
        EvaluationOutcome::PartialEvaluation {
            mut partial_evaluation,
            logs,
        } => {
            if budget_used {
                // The program reached its next tool calls after the budget
                // ran out: it stops before them, so steering gets in.
                let result = failed_program_tool_result(
                    context.descriptors(),
                    &logs,
                    &partial_evaluation.tool_state,
                    budget_exceeded_error(budget_ms, used_ms),
                )?;
                return Ok(ControlFlow::Break((
                    ProgramOutcome::Completed(Box::new(CompletedProgramResult { result })),
                    Vec::new(),
                )));
            }
            let mut pending_operations = Vec::new();
            // Operations Core settles without starting them: permission
            // denials and sleeps after the time budget is used up.
            let mut unstarted = Vec::new();
            for (operation_id, function) in
                partial_evaluation
                    .tool_state
                    .iter()
                    .filter_map(|operation| match operation {
                        ToolState::Pending { id, function, .. } => Some((id, function)),
                        _ => None,
                    })
            {
                let resolved = context
                    .resolve_operation(&function.name, function.arguments.clone())
                    .ok_or_else(|| {
                        CoreError::invalid_command(format!(
                            "unknown programmatic tool {:?}",
                            echoed_tool_name(&function.name)
                        ))
                    })?;
                let operation_call = ExternalToolCall {
                    action_id: effect_id_for_operation(ToolOrigin::Programmatic, operation_id),
                    call_id: operation_id.clone(),
                    origin: ToolOrigin::Programmatic,
                    call: resolved.tool,
                };
                let hook_binding_ids = resolved.pre_hook_binding_ids;
                let operation = if !hook_binding_ids.is_empty() {
                    PendingProgramOperation::awaiting_pre_hook(operation_call, hook_binding_ids)
                } else {
                    match context.admit(&operation_call) {
                        Admission::Run => {
                            match clock.fit(&operation_call, budget_ms, now_unix_ms) {
                                Ok(call) => PendingProgramOperation::pending(&operation_call, call),
                                Err(result) => {
                                    unstarted
                                        .push(accepted_program_result(operation_call, *result));
                                    continue;
                                }
                            }
                        }
                        Admission::Deny(result) => {
                            unstarted.push(accepted_program_result(operation_call, result));
                            continue;
                        }
                        Admission::Ask(grant_key) => {
                            let programmatic_name =
                                context.resolve_programmatic_name(&operation_call)?;
                            PendingProgramOperation::awaiting_approval(
                                operation_call,
                                grant_key,
                                programmatic_name,
                            )
                        }
                    }
                };
                pending_operations.push(operation);
            }
            if pending_operations.is_empty() && unstarted.is_empty() {
                return Err(CoreError::invalid_command(
                    "TypeScript partial evaluation contains no pending effects",
                ));
            }

            for settled in &unstarted {
                resolve_tool_state(
                    &mut partial_evaluation,
                    &settled.operation_id,
                    settled.result.clone(),
                )?;
            }
            accepted_results.extend(unstarted);
            if pending_operations.is_empty() {
                let continuation =
                    ProgramContinuation::after_round(partial_evaluation, round, clock)?;
                return Ok(ControlFlow::Continue(continuation));
            }

            let execution = ProgramExecution::new(
                partial_evaluation,
                pending_operations,
                round,
                clock,
                now_unix_ms,
            );
            let pending_actions = execution.pending_actions(context.turn_id());
            Ok(ControlFlow::Break((
                ProgramOutcome::Pending(execution),
                pending_actions,
            )))
        }
        EvaluationOutcome::CodeResult {
            stdout,
            stderr,
            tool_state,
            log_positions,
            result,
        } => {
            let result = completed_program_tool_result(
                context.descriptors(),
                &ProgramLogs {
                    stdout,
                    stderr,
                    log_positions,
                },
                &tool_state,
                result,
            )?;
            Ok(ControlFlow::Break((
                ProgramOutcome::Completed(Box::new(CompletedProgramResult { result })),
                Vec::new(),
            )))
        }
        EvaluationOutcome::Error { error } => {
            let result = failed_program_tool_result(
                context.descriptors(),
                &ProgramLogs::default(),
                &partial_evaluation.tool_state,
                error,
            )?;
            Ok(ControlFlow::Break((
                ProgramOutcome::Completed(Box::new(CompletedProgramResult { result })),
                Vec::new(),
            )))
        }
    }
}

fn completed_program_tool_result(
    descriptors: &[TypeScriptTool],
    logs: &ProgramLogs,
    tool_state: &[ToolState],
    result: CodeResult,
) -> Result<ToolResult, CoreError> {
    match result {
        CodeResult::Success { value } => {
            if let Some(blocks) = returned_content_blocks(&value) {
                let mut content = program_logs(logs);
                content.extend(blocks);
                return Ok(ToolResult::Success {
                    content,
                    structured_content: StructuredContent::Absent,
                    meta: None,
                });
            }
            let mut content = text_content(
                serde_json::to_string(&value)
                    .map_err(|error| CoreError::invariant(error.to_string()))?,
            );
            content.extend(program_logs(logs));
            content.extend(retained_program_content(descriptors, tool_state));
            Ok(ToolResult::Success {
                content,
                structured_content: StructuredContent::present(value),
                meta: None,
            })
        }
        CodeResult::Error { error } => {
            failed_program_tool_result(descriptors, logs, tool_state, error)
        }
    }
}

fn failed_program_tool_result(
    descriptors: &[TypeScriptTool],
    logs: &ProgramLogs,
    tool_state: &[ToolState],
    error: Value,
) -> Result<ToolResult, CoreError> {
    let code = error
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or("ExecutionError")
        .to_string();
    let message = error
        .get("message")
        .and_then(Value::as_str)
        .map(str::to_string)
        .unwrap_or_else(|| error.to_string());
    let mut content = text_content(format!(
        "run_typescript failed: {}",
        serde_json::to_string(&error).map_err(|error| CoreError::invariant(error.to_string()))?
    ));
    if let Some(summary) = program_call_summary(
        tool_state,
        &logs.log_positions,
        |runtime_name| program_display_name(descriptors, runtime_name),
        |_| CallStatus::NotStarted,
    ) {
        content.extend(text_content(summary));
    }
    content.extend(program_logs(logs));
    content.extend(retained_program_content(descriptors, tool_state));
    Ok(ToolResult::Failure {
        content,
        structured_content: StructuredContent::Absent,
        meta: None,
        error: ProtocolError {
            code,
            message,
            retryable: false,
            details: error.get("details").cloned().unwrap_or(Value::Null),
        },
    })
}

fn returned_content_blocks(value: &Value) -> Option<Vec<ContentBlock>> {
    if value.as_array().is_none_or(Vec::is_empty) {
        return None;
    }
    serde_json::from_value(value.clone()).ok()
}

fn program_logs(logs: &ProgramLogs) -> Vec<ContentBlock> {
    let mut content = Vec::new();
    if let Some(stdout) = logs.stdout.as_deref().filter(|value| !value.is_empty()) {
        content.extend(text_content(format!("stdout:\n{stdout}")));
    }
    if let Some(stderr) = logs.stderr.as_deref().filter(|value| !value.is_empty()) {
        content.extend(text_content(format!("stderr:\n{stderr}")));
    }
    content
}

fn retained_program_content(
    descriptors: &[TypeScriptTool],
    tool_state: &[ToolState],
) -> Vec<ContentBlock> {
    retained_program_content_with_name(tool_state, |runtime_name| {
        program_display_name(descriptors, runtime_name)
    })
}

fn program_display_name(descriptors: &[TypeScriptTool], runtime_name: &str) -> String {
    descriptors
        .iter()
        .find(|descriptor| descriptor.name == runtime_name)
        .map(|descriptor| descriptor.programmatic_name.display_name())
        .unwrap_or_else(|| format!("tools.{runtime_name}"))
}

/// Largest stopped-program summary, in bytes. The summary names operations and
/// never copies their results, so a program that made many large tool calls
/// still returns a small failure.
const MAX_SUMMARY_BYTES: usize = 4 * 1024;
/// Most entries a summary lists. A longer list keeps its first and last
/// entries and replaces the middle with one line.
const MAX_SUMMARY_ENTRIES: usize = 24;
const SUMMARY_HEAD_ENTRIES: usize = 8;
const MAX_SUMMARY_NAME_CHARS: usize = 80;
const CONSOLE_LOG: &str = "console.log";

/// Error codes of operations Core settled without starting them.
/// A sleep the time budget refused or cut short has no effects, so it counts
/// as not started too.
const UNSTARTED_ERROR_CODES: [&str; 4] = [
    "permission_denied",
    "approval_failed",
    "tool_skipped",
    BUDGET_EXCEEDED,
];

#[derive(Clone, Copy, PartialEq)]
enum CallStatus {
    Done,
    Failed,
    Running,
    NotStarted,
}

impl CallStatus {
    fn label(self) -> &'static str {
        match self {
            Self::Done => "done",
            Self::Failed => "failed, check effects",
            Self::Running => "still running, check effects",
            Self::NotStarted => "never started",
        }
    }

    fn short_label(self) -> &'static str {
        match self {
            Self::Failed => "failed",
            Self::Running => "still running",
            status => status.label(),
        }
    }
}

/// Consecutive calls with the same name and status. `console.log` entries
/// have no status and count log lines instead of tool calls.
struct SummaryEntry {
    name: String,
    status: Option<CallStatus>,
    first: usize,
    last: usize,
}

impl SummaryEntry {
    fn line(&self) -> String {
        let name = truncate_chars(&self.name, MAX_SUMMARY_NAME_CHARS);
        let range = index_range(self.first, self.last);
        match self.status {
            Some(status) => format!("{name} {range}: {}", status.label()),
            None => format!("{name} {range}"),
        }
    }
}

fn index_range(first: usize, last: usize) -> String {
    if first == last {
        first.to_string()
    } else {
        format!("{first}-{last}")
    }
}

/// The content an interrupted program adds after its Interrupted error: the
/// list of its calls and what it printed before it stopped.
pub(crate) fn interrupted_program_content(
    context: ProgramContext<'_>,
    execution: &ProgramExecution,
    call_id: &str,
    determinism: DeterminismContext,
) -> Vec<ContentBlock> {
    let logs = printed_output(context, execution, call_id, determinism);
    let descriptors = context.descriptors();
    let mut content = Vec::new();
    if let Some(summary) = program_call_summary(
        execution.tool_state(),
        &logs.log_positions,
        |runtime_name| program_display_name(descriptors, runtime_name),
        |operation_id| match execution.unfinished_operation(operation_id) {
            UnfinishedOperation::NotStarted => CallStatus::NotStarted,
            UnfinishedOperation::Running => CallStatus::Running,
            UnfinishedOperation::Finished { failed: false } => CallStatus::Done,
            UnfinishedOperation::Finished { failed: true } => CallStatus::Failed,
        },
    ) {
        content.extend(text_content(summary));
    }
    content.extend(program_logs(&logs));
    content
}

/// What a suspended program printed so far. Core does not keep it, so it
/// replays the recorded operations, which reproduces the same output. A
/// replay that does not suspend again yields nothing.
fn printed_output(
    context: ProgramContext<'_>,
    execution: &ProgramExecution,
    call_id: &str,
    determinism: DeterminismContext,
) -> ProgramLogs {
    let execution_id = typescript_execution_id(context.message_count(), call_id, execution.round());
    match code_mode::evaluate(EvaluationRequest {
        partial_evaluation: execution.partial_evaluation(),
        tools: context.descriptors(),
        execution_id: &execution_id,
        round: execution.round(),
        determinism,
        max_program_effects: context.settings().max_effects,
        max_program_operations: context.settings().max_operations,
        budget_error: None,
    }) {
        Ok(EvaluationOutcome::PartialEvaluation { logs, .. }) => logs,
        _ => ProgramLogs::default(),
    }
}

/// A short list of a stopped program's calls, so the model can continue from
/// where the program stopped instead of running it again. It lists tool calls
/// and `console.log` lines in program order, by name and index, never with
/// their results, and merges consecutive entries with the same name and
/// status. `log_positions` places each log line among `tool_state`;
/// `pending_status` places the operations that had not settled yet. `None`
/// when the program made no tool calls.
fn program_call_summary(
    tool_state: &[ToolState],
    log_positions: &[usize],
    display_name: impl Fn(&str) -> String,
    pending_status: impl Fn(&str) -> CallStatus,
) -> Option<String> {
    let mut entries: Vec<SummaryEntry> = Vec::new();
    let mut push = |name: String, status: Option<CallStatus>, index: usize| {
        if let Some(last) = entries.last_mut()
            && last.name == name
            && last.status == status
        {
            last.last = index;
            return;
        }
        entries.push(SummaryEntry {
            name,
            status,
            first: index,
            last: index,
        });
    };
    let mut call_index = 0;
    let mut log_index = 0;
    let mut logs = log_positions.iter().peekable();
    for (position, state) in tool_state.iter().enumerate() {
        while logs.next_if(|logged_at| **logged_at <= position).is_some() {
            log_index += 1;
            push(CONSOLE_LOG.to_string(), None, log_index);
        }
        let (kind, function, status) = match state {
            ToolState::Resolved { kind, function, .. } => (kind, function, CallStatus::Done),
            ToolState::Rejected {
                kind,
                function,
                error,
                ..
            } => (kind, function, rejected_status(error)),
            ToolState::Pending {
                kind, id, function, ..
            } => (kind, function, pending_status(id)),
        };
        if !matches!(kind, ToolKind::External) {
            continue;
        }
        call_index += 1;
        push(display_name(&function.name), Some(status), call_index);
    }
    for _ in logs {
        log_index += 1;
        push(CONSOLE_LOG.to_string(), None, log_index);
    }
    if call_index == 0 {
        return None;
    }

    let mut lines = vec!["Calls, in order:".to_string()];
    if entries.len() <= MAX_SUMMARY_ENTRIES {
        lines.extend(entries.iter().map(SummaryEntry::line));
    } else {
        let tail_start = entries.len() - (MAX_SUMMARY_ENTRIES - SUMMARY_HEAD_ENTRIES - 1);
        lines.extend(
            entries[..SUMMARY_HEAD_ENTRIES]
                .iter()
                .map(SummaryEntry::line),
        );
        lines.push(omitted_entries_line(
            &entries[SUMMARY_HEAD_ENTRIES..tail_start],
        ));
        lines.extend(entries[tail_start..].iter().map(SummaryEntry::line));
    }
    if entries
        .iter()
        .any(|entry| entry.status == Some(CallStatus::Done))
    {
        lines.push("Do not repeat done calls; continue with the rest.".to_string());
    }
    Some(truncate_bytes(lines.join("\n"), MAX_SUMMARY_BYTES))
}

/// One line for the entries a long summary leaves out: how many, which call
/// and log indexes they cover, and how many calls have each status.
fn omitted_entries_line(entries: &[SummaryEntry]) -> String {
    let calls = entries
        .iter()
        .filter(|entry| entry.status.is_some())
        .collect::<Vec<_>>();
    let logs = entries
        .iter()
        .filter(|entry| entry.status.is_none())
        .collect::<Vec<_>>();
    let mut parts = Vec::new();
    if let (Some(first), Some(last)) = (calls.first(), calls.last()) {
        let counts = [
            CallStatus::Done,
            CallStatus::Failed,
            CallStatus::Running,
            CallStatus::NotStarted,
        ]
        .into_iter()
        .filter_map(|status| {
            let count = calls
                .iter()
                .filter(|entry| entry.status == Some(status))
                .map(|entry| entry.last - entry.first + 1)
                .sum::<usize>();
            (count > 0).then(|| format!("{count} {}", status.short_label()))
        })
        .collect::<Vec<_>>()
        .join(", ");
        parts.push(format!(
            "calls {}: {counts}",
            index_range(first.first, last.last)
        ));
    }
    if let (Some(first), Some(last)) = (logs.first(), logs.last()) {
        parts.push(format!(
            "{CONSOLE_LOG} {}",
            index_range(first.first, last.last)
        ));
    }
    format!("+{} more entries ({})", entries.len(), parts.join("; "))
}

fn rejected_status(error: &Value) -> CallStatus {
    let name = error.get("name").and_then(Value::as_str);
    let invalid_arguments =
        error.get("type").and_then(Value::as_str) == Some("jsonSchemaArgumentValidationError");
    if invalid_arguments || name.is_some_and(|name| UNSTARTED_ERROR_CODES.contains(&name)) {
        return CallStatus::NotStarted;
    }
    CallStatus::Failed
}

fn truncate_chars(text: &str, max_chars: usize) -> String {
    match text.char_indices().nth(max_chars) {
        Some((end, _)) => format!("{}…", &text[..end]),
        None => text.to_string(),
    }
}

fn truncate_bytes(mut text: String, max_bytes: usize) -> String {
    if text.len() <= max_bytes {
        return text;
    }
    let mut end = max_bytes - '…'.len_utf8();
    while !text.is_char_boundary(end) {
        end -= 1;
    }
    text.truncate(end);
    text.push('…');
    text
}

fn retained_program_content_with_name(
    tool_state: &[ToolState],
    display_name: impl Fn(&str) -> String,
) -> Vec<ContentBlock> {
    let mut result = Vec::new();
    let mut call_ordinal = 0;
    for state in tool_state {
        let (kind, function, content) = match state {
            ToolState::Resolved {
                kind,
                function,
                content,
                ..
            }
            | ToolState::Rejected {
                kind,
                function,
                content,
                ..
            } => (kind, function, content),
            ToolState::Pending { .. } => continue,
        };
        if !matches!(kind, ToolKind::External) {
            continue;
        }
        call_ordinal += 1;
        let blocks = content
            .iter()
            .filter(|block| !matches!(block, ContentBlock::Text(_)))
            .filter(|block| model_visible_content_block((*block).clone()).is_some())
            .cloned()
            .collect::<Vec<_>>();
        if blocks.is_empty() {
            continue;
        }
        result.extend(text_content(format!(
            "Additional content from `{}` (call {call_ordinal}):",
            display_name(&function.name)
        )));
        result.extend(blocks);
    }
    result
}

impl ProgramExecution {
    fn within_result_limits(
        &self,
        call: &ExternalToolCall,
        result: ToolResult,
        awaiting_post_hook: bool,
    ) -> ToolResult {
        let result = within_nested_result_limit(result);
        if self.held_result_bytes(call, &result, awaiting_post_hook) <= code_mode::MAX_SOURCE_BYTES
        {
            return result;
        }
        let limit_mib = code_mode::MAX_SOURCE_BYTES / (1024 * 1024);
        result_too_large(
            "program_results_too_large",
            format!(
                "The tool ran, but run_typescript cannot hold its result: together with the results this program already holds it exceeds the {limit_mib} MiB limit for one program. Return only the fields needed from each result, make fewer tool calls in each run_typescript block, or split the work across multiple run_typescript calls."
            ),
            code_mode::MAX_SOURCE_BYTES,
        )
    }

    fn tool_result_disposition(
        &self,
        context: ProgramContext<'_>,
        action_id: &str,
    ) -> Option<ProgramToolResultDisposition> {
        let call = self.pending_operation(action_id)?.clone();
        let hook_binding_ids = context.post_hook_binding_ids(&call);
        Some(if hook_binding_ids.is_empty() {
            ProgramToolResultDisposition::Commit { call }
        } else {
            ProgramToolResultDisposition::AwaitPostHook {
                call,
                hook_binding_ids,
            }
        })
    }

    fn classify_input(
        &self,
        context: ProgramContext<'_>,
        input: ProgramInput,
        now_unix_ms: u64,
    ) -> Result<ClassifiedProgramInput, CoreError> {
        match input {
            ProgramInput::ToolResult { action_id, result } => {
                let disposition = self
                    .tool_result_disposition(context, &action_id)
                    .ok_or_else(|| {
                        CoreError::invariant("pending program tool operation was not found")
                    })?;
                let result = match &disposition {
                    ProgramToolResultDisposition::Commit { call } => {
                        self.within_result_limits(call, result, false)
                    }
                    ProgramToolResultDisposition::AwaitPostHook { call, .. } => {
                        self.within_result_limits(call, result, true)
                    }
                };
                Ok(match disposition {
                    ProgramToolResultDisposition::Commit { call } => ClassifiedProgramInput {
                        accepted_result: Some(accepted_program_result(call, result.clone())),
                        transition: ProgramInputTransition::Resume { action_id, result },
                    },
                    ProgramToolResultDisposition::AwaitPostHook {
                        hook_binding_ids, ..
                    } => ClassifiedProgramInput {
                        accepted_result: None,
                        transition: ProgramInputTransition::AwaitPostHook {
                            action_id,
                            hook_binding_ids,
                            result,
                        },
                    },
                })
            }
            ProgramInput::ApprovalResult { action_id, result } => {
                let call = self.pending_approval(&action_id).ok_or_else(|| {
                    CoreError::invariant("pending program approval was not found")
                })?;

                Ok(ClassifiedProgramInput {
                    accepted_result: Some(accepted_program_result(call.clone(), result.clone())),
                    transition: ProgramInputTransition::Resume { action_id, result },
                })
            }
            ProgramInput::HookCompleted { action_id, result } => {
                match (self.pending_hook(&action_id), result) {
                    (
                        Some((HookSide::Pre, original)),
                        HookResult::PreToolCall(PreToolCallOutput::Continue {
                            effective_arguments,
                        }),
                    ) => {
                        let effective_call =
                            context.effective_call(original, effective_arguments)?;
                        Ok(match context.admit(&effective_call) {
                            Admission::Deny(result) => ClassifiedProgramInput::resume_after_hook(
                                action_id,
                                &effective_call,
                                result,
                            ),
                            Admission::Ask(grant_key) => ClassifiedProgramInput {
                                accepted_result: None,
                                transition: ProgramInputTransition::AwaitApprovalAfterPreHook {
                                    action_id,
                                    programmatic_name: context
                                        .resolve_programmatic_name(&effective_call)?,
                                    effective_call,
                                    grant_key,
                                },
                            },
                            Admission::Run => match self.clock().fit(
                                &effective_call,
                                context.settings().max_elapsed_ms,
                                now_unix_ms,
                            ) {
                                Ok(sent_call) => ClassifiedProgramInput {
                                    accepted_result: None,
                                    transition: ProgramInputTransition::ContinueAfterPreHook {
                                        action_id,
                                        effective_call,
                                        sent_call,
                                    },
                                },
                                Err(result) => ClassifiedProgramInput::resume_after_hook(
                                    action_id,
                                    &effective_call,
                                    *result,
                                ),
                            },
                        })
                    }
                    (
                        Some((HookSide::Pre, call)),
                        HookResult::PreToolCall(PreToolCallOutput::Skip { reason }),
                    ) => {
                        let result = skipped_tool_result(reason)?;
                        Ok(ClassifiedProgramInput::resume_after_hook(
                            action_id, call, result,
                        ))
                    }
                    (Some((HookSide::Post, call)), HookResult::PostToolCall { tool_result }) => {
                        Ok(ClassifiedProgramInput::resume_after_hook(
                            action_id,
                            call,
                            self.within_result_limits(call, tool_result, false),
                        ))
                    }
                    (Some((HookSide::Pre, _)), _) => Err(CoreError::invalid_command(
                        "pre-tool hook requires a pre_tool_call result",
                    )),
                    (Some((HookSide::Post, _)), _) => Err(CoreError::invalid_command(
                        "post-tool hook requires a post_tool_call result",
                    )),
                    (None, _) => Err(CoreError::invariant("pending program hook was not found")),
                }
            }
            ProgramInput::HookFailed { action_id, error } => {
                let (_, call) = self
                    .pending_hook(&action_id)
                    .ok_or_else(|| CoreError::invariant("pending program hook was not found"))?;
                let result = hook_failure_tool_result(error);
                Ok(ClassifiedProgramInput::resume_after_hook(
                    action_id, call, result,
                ))
            }
        }
    }

    pub(crate) fn advance(
        &mut self,
        context: ProgramContext<'_>,
        parent_call: &ToolCall,
        input: ProgramInput,
        determinism: DeterminismContext,
    ) -> Result<ProgramTransition, CoreError> {
        let mut candidate = self.clone();
        let transition = candidate.advance_in_place(context, parent_call, input, determinism)?;
        candidate.sync_clock(determinism.time_unix_ms);
        *self = candidate;
        Ok(transition)
    }

    fn advance_in_place(
        &mut self,
        context: ProgramContext<'_>,
        parent_call: &ToolCall,
        input: ProgramInput,
        determinism: DeterminismContext,
    ) -> Result<ProgramTransition, CoreError> {
        let now_unix_ms = determinism.time_unix_ms;
        let input = match input {
            ProgramInput::ToolResult { action_id, result } => {
                let result = self.settle_sleep_result(
                    &action_id,
                    result,
                    context.settings().max_elapsed_ms,
                    now_unix_ms,
                );
                ProgramInput::ToolResult { action_id, result }
            }
            input => input,
        };
        let ClassifiedProgramInput {
            accepted_result,
            transition,
        } = self.classify_input(context, input, now_unix_ms)?;
        let mut accepted_results = Vec::from_iter(accepted_result);
        let advance = match transition {
            ProgramInputTransition::Resume { action_id, result } => {
                match self.resolve_pending(&action_id, result)? {
                    None => Ok(ProgramAdvance::Pending {
                        actions: Vec::new(),
                    }),
                    Some(continuation) => {
                        let (outcome, actions) = drive_program_execution(
                            context,
                            parent_call,
                            continuation,
                            determinism,
                            &mut accepted_results,
                        )?;
                        match outcome {
                            ProgramOutcome::Pending(execution) => {
                                *self = execution;
                                Ok(ProgramAdvance::Pending { actions })
                            }
                            ProgramOutcome::Completed(completed) if actions.is_empty() => {
                                Ok(ProgramAdvance::Completed { completed })
                            }
                            ProgramOutcome::Completed(_) => Err(CoreError::invariant(
                                "completed program execution produced pending actions",
                            )),
                        }
                    }
                }
            }
            ProgramInputTransition::AwaitPostHook {
                action_id,
                hook_binding_ids,
                result,
            } => {
                let action =
                    self.await_post_hook(&action_id, hook_binding_ids, result, context.turn_id())?;
                Ok(ProgramAdvance::Pending {
                    actions: vec![action],
                })
            }
            ProgramInputTransition::AwaitApprovalAfterPreHook {
                action_id,
                effective_call,
                grant_key,
                programmatic_name,
            } => {
                let approval = self.await_approval_after_pre_hook(
                    &action_id,
                    effective_call,
                    grant_key,
                    programmatic_name,
                    context.turn_id(),
                )?;

                Ok(ProgramAdvance::Pending {
                    actions: vec![approval],
                })
            }
            ProgramInputTransition::ContinueAfterPreHook {
                action_id,
                effective_call,
                sent_call,
            } => {
                let action = self.continue_after_pre_hook(
                    &action_id,
                    &effective_call,
                    sent_call,
                    context.turn_id(),
                )?;
                Ok(ProgramAdvance::Pending {
                    actions: vec![action],
                })
            }
        }?;
        Ok(ProgramTransition {
            advance,
            accepted_results,
        })
    }
}

fn accepted_program_result(call: ExternalToolCall, result: ToolResult) -> AcceptedProgramResult {
    AcceptedProgramResult {
        action_id: call.action_id,
        operation_id: call.call_id,
        call: call.call,
        result,
    }
}

/// Largest serialized nested result a program receives; see
/// `within_nested_result_limit`.
const NESTED_RESULT_LIMIT_BYTES: usize = 8 * 1024 * 1024;

/// Quick fix for a trap that left sessions unable to resume: a large nested
/// result was copied into the post-tool hook input and output, the checkpoint,
/// and the Runtime's journal before the program could use it, which could push
/// a stored document past the Runtime's 64 MiB size limit. Such a result now
/// becomes a small failure the program can catch, on arrival and after the
/// post-tool hook.
///
/// The limit is half the 16 MiB replay limit because the Runtime records one
/// result several times between two journal compactions; 8 MiB keeps that
/// within its size limit, and V8 already runs out of memory replaying values
/// of that size.
///
/// This deliberately bounds one result only; `within_result_limits` also bounds
/// their sum. Neither covers direct calls or results the Runtime journals
/// before Core sees them. Passing large results by reference
/// is the intended complete solution.
fn within_nested_result_limit(result: ToolResult) -> ToolResult {
    if serialized_len(&result) <= NESTED_RESULT_LIMIT_BYTES {
        return result;
    }
    let limit_mib = NESTED_RESULT_LIMIT_BYTES / (1024 * 1024);
    result_too_large(
        "tool_result_too_large",
        format!(
            "The tool ran, but its result is too large to return to run_typescript: it exceeds the {limit_mib} MiB limit. Request a smaller result by filtering, paginating, or narrowing the query, or split the work across multiple run_typescript calls."
        ),
        NESTED_RESULT_LIMIT_BYTES,
    )
}

fn result_too_large(code: &str, message: String, limit_bytes: usize) -> ToolResult {
    ToolResult::Failure {
        content: text_content(message.clone()),
        structured_content: StructuredContent::default(),
        meta: None,
        error: ProtocolError {
            code: code.to_string(),
            message,
            retryable: false,
            details: json!({ "limit_bytes": limit_bytes }),
        },
    }
}

pub(super) fn serialized_len(value: &impl serde::Serialize) -> usize {
    struct ByteCounter(usize);
    impl std::io::Write for ByteCounter {
        fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
            self.0 += bytes.len();
            Ok(bytes.len())
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }
    let mut counter = ByteCounter(0);
    serde_json::to_writer(&mut counter, value).expect("program state serializes");
    counter.0
}

/// A replay-stable identity for one TypeScript evaluation.
///
/// Derived from the context length rather than a counter so that replaying the
/// same command sequence produces the same id, which V8 replay depends on.
pub(crate) fn typescript_execution_id(message_count: usize, call_id: &str, round: u32) -> String {
    format!("typescript:{message_count}:{call_id}:{round}")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::features::programmatic_tool_calling::model::ToolFunction;
    use crate::core::testing::*;
    use crate::core::wire::content::{
        Annotations, ImageContent, MetaObject, Resource, Role as ContentRole,
    };

    /// An external call `name` with call number `call`, in the given state.
    fn summary_call(call: usize, name: &str, status: CallStatus) -> ToolState {
        let id = format!("operation-{call}");
        let function = ToolFunction {
            name: name.to_string(),
            arguments: json!({}),
        };
        match status {
            CallStatus::Done => ToolState::Resolved {
                kind: ToolKind::External,
                id,
                function,
                result: json!({}),
                content: Vec::new(),
            },
            CallStatus::Failed => ToolState::Rejected {
                kind: ToolKind::External,
                id,
                function,
                error: json!({"name": "ToolError", "message": "exit code 1"}),
                content: Vec::new(),
            },
            CallStatus::Running | CallStatus::NotStarted => ToolState::Pending {
                kind: ToolKind::External,
                id,
                function,
            },
        }
    }

    fn call_summary(tool_state: &[ToolState], log_positions: &[usize]) -> Option<String> {
        program_call_summary(
            tool_state,
            log_positions,
            |name| format!("tools.{name}"),
            |id| {
                if id == "operation-12" {
                    CallStatus::Running
                } else {
                    CallStatus::NotStarted
                }
            },
        )
    }

    #[test]
    fn call_summary_merges_calls_and_places_log_lines() {
        let mut tool_state = Vec::new();
        for call in 1..=6 {
            tool_state.push(summary_call(
                call,
                "file_system.read_file",
                CallStatus::Done,
            ));
        }
        tool_state.push(summary_call(7, "file_system.bash", CallStatus::Failed));
        for call in 8..=10 {
            tool_state.push(summary_call(
                call,
                "file_system.search_replace",
                CallStatus::Done,
            ));
        }
        tool_state.push(summary_call(11, "file_system.read_file", CallStatus::Done));
        tool_state.push(summary_call(12, "file_system.bash", CallStatus::Running));
        for call in 13..=15 {
            tool_state.push(summary_call(
                call,
                "file_system.write_file",
                CallStatus::NotStarted,
            ));
        }

        assert_eq!(
            call_summary(&tool_state, &[0, 7, 7]).unwrap(),
            "Calls, in order:
console.log 1
tools.file_system.read_file 1-6: done
tools.file_system.bash 7: failed, check effects
console.log 2-3
tools.file_system.search_replace 8-10: done
tools.file_system.read_file 11: done
tools.file_system.bash 12: still running, check effects
tools.file_system.write_file 13-15: never started
Do not repeat done calls; continue with the rest."
        );
        assert_eq!(call_summary(&[], &[0]), None);
    }

    #[test]
    fn long_call_summary_keeps_its_first_and_last_entries() {
        let tool_state = (1..=40)
            .map(|call| {
                let name = if call % 2 == 1 {
                    "file_system.read_file"
                } else {
                    "file_system.write_file"
                };
                summary_call(call, name, CallStatus::Done)
            })
            .collect::<Vec<_>>();

        let summary = call_summary(&tool_state, &[12]).unwrap();

        let lines = summary.lines().collect::<Vec<_>>();
        assert_eq!(lines.len(), 26);
        assert_eq!(lines[8], "tools.file_system.write_file 8: done");
        assert_eq!(
            lines[9],
            "+18 more entries (calls 9-25: 17 done; console.log 1)"
        );
        assert_eq!(lines[10], "tools.file_system.write_file 26: done");
        assert_eq!(lines[24], "tools.file_system.write_file 40: done");
    }

    fn advance_tool_result(state: HarnessState, action_id: String, result: ToolResult) -> Step {
        let call_id = state
            .pending_tool_call_id(&action_id)
            .expect("test requires a pending program operation")
            .to_string();
        let command = match result {
            result @ ToolResult::Success { .. } => HarnessCommand::ToolSucceeded {
                action_id,
                call_id,
                result,
            },
            result @ ToolResult::Failure { .. } => HarnessCommand::ToolFailed {
                action_id,
                call_id,
                result,
            },
        };
        advance(state, command).unwrap()
    }

    #[test]
    fn direct_and_programmatic_calls_select_the_same_hook_bindings() {
        let mut config = config();
        add_tool_hook(
            &mut config,
            "read-hook",
            HookPoint::PreToolCall,
            0,
            HookToolTarget::Filesystem,
            "file_system.read_file",
        );
        let direct = advance_llm(
            started_with(config.clone()),
            assistant_tool("read_file", json!({"path": "direct.txt"})),
        );
        let programmatic = advance_llm(
            started_with(config),
            assistant_tool(
                "run_typescript",
                json!({"code": "async function main() { return tools.file_system.read_file({ path: 'programmatic.txt' }); }"}),
            ),
        );

        let selected = |action: Option<Action>| match action {
            Some(Action::Hook {
                hook_binding_ids,
                call: HookCall::PreToolCall { tool_call },
                ..
            }) => (
                hook_binding_ids,
                tool_call.call.hook_tool_key().qualified_name,
            ),
            action => panic!("expected a pre-tool hook action, got {action:?}"),
        };
        assert_eq!(selected(direct.effect), selected(programmatic.effect));
    }

    #[test]
    fn routes_programmatic_filesystem_effects_then_replays_typescript() {
        let step = advance_llm(
            started(),
            assistant_tool(
                "run_typescript",
                json!({"code": "async function main() { return tools.file_system.read_file({ path: 'a.txt' }); }"}),
            ),
        );
        let Some(Action::RuntimeBuiltinTool {
            effect_id,
            operation_id,
            call,
            ..
        }) = step.effect
        else {
            panic!("expected a filesystem effect");
        };
        assert_eq!(
            call.name,
            crate::core::tools::external::RuntimeBuiltinToolName::FileSystemReadFile
        );

        let step = advance_tool(
            step.state,
            effect_id.clone(),
            json!({"content": "hello"}),
            None,
        );
        assert_eq!(
            step.accepted_tool_result,
            Some(AcceptedToolResult {
                turn_id: "turn-1".to_string(),
                action_id: effect_id,
                operation_id,
                result: ToolResult::Success {
                    content: Vec::new(),
                    structured_content: StructuredContent::present(json!({"content": "hello"})),
                    meta: None,
                },
            })
        );
        assert!(matches!(step.effect, Some(Action::Completion { .. })));
        assert!(message_text(step.state.context.messages().last().unwrap()).contains("hello"));
    }

    #[test]
    fn promise_all_dispatches_sibling_programmatic_effects_and_accepts_out_of_order_results() {
        let first = advance_llm(
            started(),
            assistant_tool(
                "run_typescript",
                json!({
                    "code": "async function main() { const [first, second] = await Promise.all([tools.file_system.read_file({ path: 'first.txt' }), tools.file_system.read_file({ path: 'second.txt' })]); return { first, second }; }"
                }),
            ),
        );
        let actions = first
            .effects
            .iter()
            .map(|effect| match effect {
                Action::RuntimeBuiltinTool {
                    effect_id, call, ..
                } => (
                    effect_id.clone(),
                    call.arguments["path"].as_str().unwrap().to_string(),
                ),
                effect => panic!("expected programmatic tool effect, got {effect:?}"),
            })
            .collect::<Vec<_>>();
        assert_eq!(
            actions
                .iter()
                .map(|(_, path)| path.as_str())
                .collect::<Vec<_>>(),
            ["first.txt", "second.txt"]
        );

        let second = advance_tool(
            first.state,
            actions[1].0.clone(),
            json!({"content": "second"}),
            None,
        );
        assert!(second.effects.is_empty());
        assert!(second.effect.is_none());

        let completed = advance_tool(
            second.state,
            actions[0].0.clone(),
            json!({"content": "first"}),
            None,
        );
        assert!(matches!(completed.effect, Some(Action::Completion { .. })));
        let content = message_text(completed.state.context.messages().last().unwrap());
        assert!(content.contains("first") && content.contains("second"));
    }

    #[test]
    fn multiple_run_typescript_calls_own_independent_execution_frames() {
        let message = assistant_tools(vec![
            (
                "program-1",
                "run_typescript",
                json!({
                    "code": "async function main() { return tools.file_system.read_file({ path: 'first.txt' }); }"
                }),
            ),
            (
                "program-2",
                "run_typescript",
                json!({
                    "code": "async function main() { return tools.file_system.read_file({ path: 'second.txt' }); }"
                }),
            ),
        ]);
        let first = advance_llm(started(), message);
        let actions = first
            .effects
            .iter()
            .map(|effect| match effect {
                Action::RuntimeBuiltinTool {
                    effect_id, call, ..
                } => (
                    effect_id.clone(),
                    call.arguments["path"].as_str().unwrap().to_string(),
                ),
                effect => panic!("expected programmatic tool effect, got {effect:?}"),
            })
            .collect::<Vec<_>>();
        assert_eq!(actions.len(), 2);

        let second = advance_tool(
            first.state,
            actions[1].0.clone(),
            json!({"content": "second"}),
            None,
        );
        let completed = advance_tool(
            second.state,
            actions[0].0.clone(),
            json!({"content": "first"}),
            None,
        );

        assert!(matches!(completed.effect, Some(Action::Completion { .. })));
        assert_eq!(
            completed
                .state
                .context
                .messages()
                .iter()
                .filter_map(|stored| match &stored.message {
                    Message::Tool { tool_call_id, .. } => Some(tool_call_id.as_str()),
                    _ => None,
                })
                .collect::<Vec<_>>(),
            ["program-1", "program-2"]
        );
    }

    ///
    /// *Prepare*: A nested result contains result metadata and a user-only image with annotations.
    /// *Do*: Commit the result and allow `run_typescript` to finish.
    /// *Assert*: The committed external result stays exact while model context contains only the
    /// structured value.
    ///
    #[test]
    fn programmatic_tool_results_preserve_client_data_outside_model_context() {
        // Prepare
        let first = advance_llm(
            started(),
            assistant_tool(
                "run_typescript",
                json!({
                    "code": "async function main() { return tools.file_system.read_file({ path: 'secret.txt' }); }"
                }),
            ),
        );
        let action_id = awaiting_action_id(&first.state);
        let result = ToolResult::Success {
            content: vec![ContentBlock::Image(
                ImageContent::new("dXNlci1vbmx5", "image/png")
                    .with_annotations(
                        Annotations::default()
                            .with_audience(vec![ContentRole::User])
                            .with_priority(0.5),
                    )
                    .with_meta(MetaObject(
                        json!({"block": "client-visible"})
                            .as_object()
                            .expect("block metadata fixture is an object")
                            .clone(),
                    )),
            )],
            structured_content: StructuredContent::present(json!({"content": "visible"})),
            meta: Some(
                json!({"result": "client-visible"})
                    .as_object()
                    .expect("result metadata fixture is an object")
                    .clone(),
            ),
        };

        // Do
        let call_id = first
            .state
            .pending_tool_call_id(&action_id)
            .expect("test requires a pending program operation")
            .to_string();
        let completed = advance(
            first.state,
            HarnessCommand::ToolSucceeded {
                action_id,
                call_id,
                result: result.clone(),
            },
        )
        .unwrap();

        // Assert
        assert_eq!(completed.accepted_tool_result.unwrap().result, result);
        assert_eq!(
            message_content(&completed.state.context.messages().last().unwrap().message),
            text_content(r#"{"content":"visible"}"#)
        );
    }

    ///
    /// *Prepare*: A program returns structured data from a tool whose content also contains a
    /// large text fallback and an image.
    /// *Do*: Complete the nested tool call and let `run_typescript` finish normally.
    /// *Assert*: The model receives the compact final JSON and provenance-labeled image, but not
    /// the nested text fallback.
    ///
    #[test]
    fn ordinary_program_results_keep_structured_output_and_only_rich_nested_content() {
        // Prepare
        let first = advance_llm(
            started(),
            assistant_tool(
                "run_typescript",
                json!({
                    "code": "async function main() { const result = await tools.file_system.read_file({ path: 'report.txt' }); return { content: result.content }; }"
                }),
            ),
        );
        let action_id = awaiting_action_id(&first.state);
        let image = ContentBlock::image("aW1hZ2U=", "image/png");
        let result = ToolResult::Success {
            content: vec![
                ContentBlock::text("large intermediary text".to_string()),
                image.clone(),
            ],
            structured_content: StructuredContent::present(json!({"content": "summary"})),
            meta: None,
        };

        // Do
        let completed = advance_tool_result(first.state, action_id, result);

        // Assert
        let message = &completed.state.context.messages().last().unwrap().message;
        assert!(matches!(
            message,
            Message::Tool {
                outcome: ToolOutcome::Success,
                meta: None,
                ..
            }
        ));
        assert_eq!(
            message_content(message),
            vec![
                ContentBlock::text(r#"{"content":"summary"}"#.to_string()),
                ContentBlock::text(
                    "Additional content from `tools.file_system.read_file` (call 1):",
                ),
                image,
            ]
        );
        assert!(!message_text(message).contains("large intermediary text"));
    }

    ///
    /// *Prepare*: A rich-only nested result enters the sandbox as image and resource-link blocks.
    /// *Do*: The program filters the array to images and returns it.
    /// *Assert*: The returned images replace the whole outer result without JSON or automatically
    /// retained sibling blocks.
    ///
    #[test]
    fn returned_content_blocks_authoritatively_replace_automatic_aggregation() {
        // Prepare
        let first = advance_llm(
            started(),
            assistant_tool(
                "run_typescript",
                json!({
                    "code": "async function main() { const blocks = await tools.file_system.read_file({ path: 'image.bin' }); return blocks.filter((block) => block.type === 'image'); }"
                }),
            ),
        );
        let action_id = awaiting_action_id(&first.state);
        let image = ContentBlock::image("aW1hZ2U=", "image/png");
        let resource = ContentBlock::resource_link(
            Resource::new("resource://report", "report").with_mime_type("application/pdf"),
        );
        let result = ToolResult::Success {
            content: vec![image.clone(), resource],
            structured_content: StructuredContent::Absent,
            meta: None,
        };

        // Do
        let completed = advance_tool_result(first.state, action_id, result);

        // Assert
        let message = &completed.state.context.messages().last().unwrap().message;
        assert_eq!(message_content(message), vec![image]);
    }

    ///
    /// *Prepare*: A program returns an MCP camelCase image block without calling another tool.
    /// *Do*: Complete the `run_typescript` call.
    /// *Assert*: Core accepts and normalizes the block instead of treating the array as JSON text.
    ///
    #[test]
    fn returned_content_blocks_accept_mcp_camel_case_fields() {
        // Prepare
        let code = "async function main() { return [{ type: 'image', data: 'aW1hZ2U=', mimeType: 'image/png' }]; }";

        // Do
        let completed = advance_llm(
            started(),
            assistant_tool("run_typescript", json!({"code": code})),
        );

        // Assert
        assert_eq!(
            message_content(&completed.state.context.messages().last().unwrap().message),
            vec![ContentBlock::image("aW1hZ2U=", "image/png")]
        );
    }

    ///
    /// *Prepare*: Two sibling operations each return structured data and an image.
    /// *Do*: Resolve the second operation before the first.
    /// *Assert*: Retained rich content follows source-program order rather than completion order.
    ///
    #[test]
    fn parallel_rich_content_preserves_program_order() {
        // Prepare
        let first = advance_llm(
            started(),
            assistant_tool(
                "run_typescript",
                json!({
                    "code": "async function main() { const [first, second] = await Promise.all([tools.file_system.read_file({ path: 'first.txt' }), tools.file_system.read_file({ path: 'second.txt' })]); return { first: first.content, second: second.content }; }"
                }),
            ),
        );
        let actions = first
            .effects
            .iter()
            .map(|effect| match effect {
                Action::RuntimeBuiltinTool {
                    effect_id, call, ..
                } => (
                    effect_id.clone(),
                    call.arguments["path"].as_str().unwrap().to_string(),
                ),
                effect => panic!("expected programmatic tool effect, got {effect:?}"),
            })
            .collect::<Vec<_>>();
        let image = |data: &str| ContentBlock::image(data, "image/png");
        let tool_result = |content: &str, image: ContentBlock| ToolResult::Success {
            content: vec![image],
            structured_content: StructuredContent::present(json!({"content": content})),
            meta: None,
        };

        // Do
        let second = advance_tool_result(
            first.state,
            actions[1].0.clone(),
            tool_result("second", image("c2Vjb25k")),
        );
        let completed = advance_tool_result(
            second.state,
            actions[0].0.clone(),
            tool_result("first", image("Zmlyc3Q=")),
        );

        // Assert
        assert_eq!(
            message_content(&completed.state.context.messages().last().unwrap().message),
            vec![
                ContentBlock::text(r#"{"first":"first","second":"second"}"#),
                ContentBlock::text(
                    "Additional content from `tools.file_system.read_file` (call 1):",
                ),
                image("Zmlyc3Q="),
                ContentBlock::text(
                    "Additional content from `tools.file_system.read_file` (call 2):",
                ),
                image("c2Vjb25k"),
            ]
        );
    }

    ///
    /// *Prepare*: A program returns an empty array.
    /// *Do*: Complete `run_typescript` without nested calls.
    /// *Assert*: The array remains an ordinary structured value with compact JSON content.
    ///
    #[test]
    fn empty_content_block_array_uses_ordinary_json_mode() {
        // Prepare / Do
        let completed = advance_llm(
            started(),
            assistant_tool(
                "run_typescript",
                json!({"code": "async function main() { return []; }"}),
            ),
        );

        // Assert
        let message = &completed.state.context.messages().last().unwrap().message;
        assert!(matches!(
            message,
            Message::Tool {
                outcome: ToolOutcome::Success,
                ..
            }
        ));
        assert_eq!(message_content(message), text_content("[]"));
    }

    #[test]
    fn invalid_content_block_array_uses_ordinary_json_mode() {
        let completed = advance_llm(
            started(),
            assistant_tool(
                "run_typescript",
                json!({
                    "code": "async function main() { return [{ type: 'image', data: 'missing-mime-type' }]; }"
                }),
            ),
        );

        let content = message_content(&completed.state.context.messages().last().unwrap().message);
        let [ContentBlock::Text(content)] = content.as_slice() else {
            panic!("invalid block arrays should produce one JSON text block");
        };
        assert_eq!(
            serde_json::from_str::<Value>(&content.text).unwrap(),
            json!([{"type": "image", "data": "missing-mime-type"}])
        );
    }

    #[test]
    fn authoritative_content_blocks_follow_program_logs() {
        let completed = advance_llm(
            started(),
            assistant_tool(
                "run_typescript",
                json!({
                    "code": "async function main() { console.log('selected'); return [{ type: 'image', data: 'aW1hZ2U=', mimeType: 'image/png' }]; }"
                }),
            ),
        );

        assert_eq!(
            message_content(&completed.state.context.messages().last().unwrap().message),
            vec![
                ContentBlock::text("stdout:\nselected".to_string()),
                ContentBlock::image("aW1hZ2U=", "image/png"),
            ]
        );
    }

    ///
    /// *Prepare*: A program authoritatively returns only blocks addressed to the user.
    /// *Do*: Project the successful result into assistant model context.
    /// *Assert*: The assistant receives empty content without a synthetic disclosure.
    ///
    #[test]
    fn user_only_authoritative_blocks_produce_empty_success_content() {
        // Prepare
        let code = "async function main() { return [{ type: 'image', data: 'dXNlcg==', mimeType: 'image/png', annotations: { audience: ['user'] }, _meta: { private: true } }]; }";

        // Do
        let completed = advance_llm(
            started(),
            assistant_tool("run_typescript", json!({"code": code})),
        );

        // Assert
        let message = &completed.state.context.messages().last().unwrap().message;
        assert!(matches!(
            message,
            Message::Tool {
                outcome: ToolOutcome::Success,
                meta: None,
                ..
            }
        ));
        assert!(message_content(message).is_empty());
    }

    ///
    /// *Prepare*: A nested tool explicitly returns `structuredContent: null` plus JSON text.
    /// *Do*: Return the nested value from the TypeScript program.
    /// *Assert*: The sandbox and outer result use the content fallback instead of JSON null.
    ///
    #[test]
    fn explicit_null_structured_content_falls_back_to_content_end_to_end() {
        // Prepare
        let first = advance_llm(
            started(),
            assistant_tool(
                "run_typescript",
                json!({
                    "code": "async function main() { return tools.file_system.read_file({ path: 'fallback.txt' }); }"
                }),
            ),
        );
        let action_id = awaiting_action_id(&first.state);
        let result = ToolResult::Success {
            content: text_content(r#"{"content":"fallback"}"#),
            structured_content: StructuredContent::present(Value::Null),
            meta: None,
        };

        // Do
        let completed = advance_tool_result(first.state, action_id, result);

        // Assert
        let message = &completed.state.context.messages().last().unwrap().message;
        assert_eq!(
            message_content(message),
            text_content(r#"{"content":"fallback"}"#)
        );
    }

    ///
    /// *Prepare*: A nested tool fails with text feedback and an image while the program leaves the
    /// rejection uncaught.
    /// *Do*: Resume the program with the failed tool result.
    /// *Assert*: The outer tool result is failed, names the program error, and retains only the
    /// provenance-labeled image from the nested result.
    ///
    #[test]
    fn uncaught_program_errors_are_failed_results_with_rich_context() {
        // Prepare
        let first = advance_llm(
            started(),
            assistant_tool(
                "run_typescript",
                json!({
                    "code": "async function main() { return tools.file_system.read_file({ path: 'missing.txt' }); }"
                }),
            ),
        );
        let action_id = awaiting_action_id(&first.state);
        let image = ContentBlock::image("ZXJyb3I=", "image/png");
        let result = ToolResult::Failure {
            content: vec![
                ContentBlock::text("verbose nested failure".to_string()),
                image.clone(),
            ],
            structured_content: StructuredContent::Absent,
            meta: None,
            error: ProtocolError {
                code: "not_found".to_string(),
                message: "file was not found".to_string(),
                retryable: false,
                details: Value::Null,
            },
        };

        // Do
        let completed = advance_tool_result(first.state, action_id, result);

        // Assert
        let message = &completed.state.context.messages().last().unwrap().message;
        assert!(matches!(
            message,
            Message::Tool {
                outcome: ToolOutcome::Failure,
                ..
            }
        ));
        let content = message_content(message);
        assert!(matches!(
            &content[0],
            ContentBlock::Text(content)
                if content.text.starts_with("run_typescript failed: ")
                    && content.text.contains("file was not found")
        ));
        assert!(matches!(
            &content[1],
            ContentBlock::Text(content)
                if content.text == "Calls, in order:\ntools.file_system.read_file 1: failed, check effects"
        ));
        assert!(matches!(
            &content[2],
            ContentBlock::Text(content)
                if content.text == "Additional content from `tools.file_system.read_file` (call 1):"
        ));
        assert_eq!(content[3], image);
        assert!(!message_text(message).contains("verbose nested failure"));
    }

    ///
    /// *Prepare*: A program writes to stdout and stderr before returning structured data.
    /// *Do*: Complete the local `run_typescript` execution.
    /// *Assert*: The compact return value comes first, followed by labeled non-empty logs.
    ///
    #[test]
    fn program_logs_follow_the_compact_return_value() {
        // Prepare
        let code = "async function main() { console.log('hello'); console.error('warning'); return { ok: true }; }";

        // Do
        let completed = advance_llm(
            started(),
            assistant_tool("run_typescript", json!({"code": code})),
        );

        // Assert
        assert_eq!(
            message_content(&completed.state.context.messages().last().unwrap().message),
            vec![
                ContentBlock::text(r#"{"ok":true}"#.to_string()),
                ContentBlock::text("stdout:\nhello".to_string()),
                ContentBlock::text("stderr:\nwarning".to_string()),
            ]
        );
    }

    #[test]
    fn completed_typescript_execution_stays_inside_the_core() {
        let step = advance_llm(
            started(),
            assistant_tool(
                "run_typescript",
                json!({"code": "async function main() { return 42; }"}),
            ),
        );

        assert!(matches!(step.effect, Some(Action::Completion { .. })));
        assert!(message_text(step.state.context.messages().last().unwrap()).contains("42"));
    }

    #[test]
    fn empty_typescript_code_is_evaluated_by_the_sandbox() {
        let step = advance_llm(
            started(),
            assistant_tool("run_typescript", json!({"code": ""})),
        );

        assert!(matches!(step.effect, Some(Action::Completion { .. })));
        let message = step.state.context.messages().last().unwrap();
        assert_eq!(message_role(&message.message), Role::Tool);
        assert!(!message_text(message).contains("InvalidToolCall"));
        assert!(message_text(message).contains("must define async function main"));
    }

    #[test]
    fn programmatic_operation_ids_are_unique_across_turns() {
        let first = advance_llm(
            started(),
            assistant_tool(
                "run_typescript",
                json!({"code": "async function main() { return tools.file_system.read_file({ path: 'first.txt' }); }"}),
            ),
        );
        let (first_action_id, first_operation_id) = match first.effect {
            Some(Action::RuntimeBuiltinTool {
                effect_id,
                operation_id,
                ..
            }) => (effect_id, operation_id),
            effect => panic!("expected first programmatic tool effect, got {effect:?}"),
        };
        let completed_program = advance_tool(
            first.state,
            first_action_id,
            json!({"content": "first"}),
            None,
        );
        let completed_turn = advance_llm(completed_program.state, assistant_text("first answer"));
        let second_turn = advance(
            completed_turn.state,
            user_message("second turn", UserMessageMode::Queue),
        )
        .unwrap();
        let second = advance_llm(
            second_turn.state,
            assistant_tool(
                "run_typescript",
                json!({"code": "async function main() { return tools.file_system.read_file({ path: 'second.txt' }); }"}),
            ),
        );
        let Some(Action::RuntimeBuiltinTool {
            operation_id: second_operation_id,
            ..
        }) = second.effect
        else {
            panic!("expected second programmatic tool effect");
        };

        assert_ne!(first_operation_id, second_operation_id);
    }

    #[test]
    fn resumes_typescript_internally_between_external_tool_effects() {
        let first = advance_llm(
            started(),
            assistant_tool(
                "run_typescript",
                json!({
                    "code": "async function main() { const first = await tools.file_system.read_file({ path: 'first.txt' }); const second = await tools.file_system.read_file({ path: 'second.txt' }); return { first, second }; }"
                }),
            ),
        );
        let first_action_id = awaiting_action_id(&first.state);
        assert!(matches!(
            first.effect,
            Some(Action::RuntimeBuiltinTool { ref call, .. })
                if call.arguments["path"] == "first.txt"
        ));

        let second = advance_tool(
            first.state,
            first_action_id,
            json!({"content": "first"}),
            None,
        );
        let second_action_id = awaiting_action_id(&second.state);
        assert!(matches!(
            second.effect,
            Some(Action::RuntimeBuiltinTool { ref call, .. })
                if call.arguments["path"] == "second.txt"
        ));

        let completed = advance_tool(
            second.state,
            second_action_id,
            json!({"content": "second"}),
            None,
        );

        assert!(matches!(completed.effect, Some(Action::Completion { .. })));
        let content = message_text(completed.state.context.messages().last().unwrap());
        assert!(content.contains("first") && content.contains("second"));
    }
}
