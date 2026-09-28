use crate::core::error::CoreError;
use serde_json::Value;
use serde_json::json;

use crate::core::features::programmatic_tool_calling::code_mode::{
    self, CodeResult, EvaluationOutcome, EvaluationRequest,
};
use crate::core::features::programmatic_tool_calling::model::PartialEvaluation;
use crate::core::features::programmatic_tool_calling::model::ToolKind;
use crate::core::features::programmatic_tool_calling::model::ToolState;
use crate::core::features::programmatic_tool_calling::state::{
    PendingProgramHook, PendingProgramOperation, ProgramExecution,
};
use crate::core::features::programmatic_tool_calling::{
    CompletedProgramResult, ProgramContext, ProgramOutcome, TypeScriptTool,
};
use crate::core::hooks::{
    HookCall, HookPoint, HookResult, PreToolCallOutput, hook_action_id, hook_failure_tool_result,
    skipped_tool_result,
};
use crate::core::step_protocol::Action;
use crate::core::step_protocol::DeterminismContext;
use crate::core::tools::external::{ExternalToolCall, ToolOrigin, effect_id_for_operation};
use crate::core::tools::result::model_visible_content_block;
use crate::core::wire::content::ContentBlock;
use crate::core::wire::content::text_content;
use crate::core::wire::tool::{ProtocolError, StructuredContent, ToolCall, ToolResult};

pub(super) fn start_program_execution(
    context: ProgramContext<'_>,
    call: &ToolCall,
    determinism: DeterminismContext,
) -> Result<(ProgramOutcome, Vec<Action>), CoreError> {
    let code = call
        .arguments
        .get("code")
        .and_then(Value::as_str)
        .ok_or_else(|| CoreError::invalid_command("run_typescript code must be a string"))?;
    drive_program_execution(
        context,
        call,
        PartialEvaluation {
            code: code.to_string(),
            input: json!({}),
            tool_state: Vec::new(),
        },
        0,
        determinism,
    )
}

enum ProgramResume<'a> {
    Tool { action_id: &'a str },
    Hook { action_id: &'a str },
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

enum ProgramInputTransition {
    ResumeTool {
        action_id: String,
        result: ToolResult,
    },
    AwaitPostHook {
        action_id: String,
        call: ExternalToolCall,
        hook_binding_ids: Vec<String>,
        result: ToolResult,
    },
    ContinueAfterPreHook {
        action_id: String,
        effective_call: ExternalToolCall,
    },
    ResumeHook {
        action_id: String,
        result: ToolResult,
    },
}

pub(crate) enum ProgramInput {
    ToolResult {
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
    pub(crate) accepted_result: Option<AcceptedProgramResult>,
}

fn drive_program_execution(
    context: ProgramContext<'_>,
    call: &ToolCall,
    partial_evaluation: PartialEvaluation,
    round: u32,
    determinism: DeterminismContext,
) -> Result<(ProgramOutcome, Vec<Action>), CoreError> {
    let execution_id = typescript_execution_id(context.message_count(), &call.id, round);
    match code_mode::evaluate(EvaluationRequest {
        partial_evaluation: &partial_evaluation,
        tools: context.descriptors(),
        execution_id: &execution_id,
        determinism,
        max_program_effects: context.settings().max_effects,
        max_program_operations: context.settings().max_operations,
    })
    .map_err(CoreError::invariant)?
    {
        EvaluationOutcome::PartialEvaluation { partial_evaluation } => {
            let mut pending_operations = Vec::new();
            let mut effects = Vec::new();
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
                            function.name
                        ))
                    })?;
                let call = ExternalToolCall {
                    action_id: effect_id_for_operation(ToolOrigin::Programmatic, operation_id),
                    call_id: operation_id.clone(),
                    origin: ToolOrigin::Programmatic,
                    call: resolved.tool,
                };
                let hook_binding_ids = resolved.pre_hook_binding_ids;
                let operation = if !hook_binding_ids.is_empty() {
                    let hook_action_id = hook_action_id(&call.action_id, HookPoint::PreToolCall);
                    effects.push(Action::hook(
                        hook_action_id.clone(),
                        context.turn_id(),
                        hook_binding_ids.clone(),
                        HookCall::PreToolCall {
                            tool_call: (&call).into(),
                        },
                    )?);
                    PendingProgramOperation::awaiting_pre_hook(
                        call,
                        hook_action_id,
                        hook_binding_ids,
                    )
                } else {
                    effects.push(Action::external_tool(&call, context.turn_id()));
                    PendingProgramOperation::pending(call)
                };
                pending_operations.push(operation);
            }
            if pending_operations.is_empty() {
                return Err(CoreError::invalid_command(
                    "TypeScript partial evaluation contains no pending effects",
                ));
            }
            Ok((
                ProgramOutcome::Pending(ProgramExecution::new(
                    partial_evaluation,
                    pending_operations,
                    round,
                )),
                effects,
            ))
        }
        EvaluationOutcome::CodeResult {
            stdout,
            stderr,
            tool_state,
            result,
        } => {
            let result = completed_program_tool_result(
                context.descriptors(),
                stdout.as_deref(),
                stderr.as_deref(),
                &tool_state,
                result,
            )?;
            Ok((
                ProgramOutcome::Completed(Box::new(CompletedProgramResult { result })),
                Vec::new(),
            ))
        }
        EvaluationOutcome::Error { error } => {
            let result = failed_program_tool_result(context.descriptors(), None, None, &[], error)?;
            Ok((
                ProgramOutcome::Completed(Box::new(CompletedProgramResult { result })),
                Vec::new(),
            ))
        }
    }
}

fn completed_program_tool_result(
    descriptors: &[TypeScriptTool],
    stdout: Option<&str>,
    stderr: Option<&str>,
    tool_state: &[ToolState],
    result: CodeResult,
) -> Result<ToolResult, CoreError> {
    match result {
        CodeResult::Success { value } => {
            if let Some(blocks) = returned_content_blocks(&value) {
                let mut content = program_logs(stdout, stderr);
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
            content.extend(program_logs(stdout, stderr));
            content.extend(retained_program_content(descriptors, tool_state));
            Ok(ToolResult::Success {
                content,
                structured_content: StructuredContent::present(value),
                meta: None,
            })
        }
        CodeResult::Error { error } => {
            failed_program_tool_result(descriptors, stdout, stderr, tool_state, error)
        }
    }
}

fn failed_program_tool_result(
    descriptors: &[TypeScriptTool],
    stdout: Option<&str>,
    stderr: Option<&str>,
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
    content.extend(program_logs(stdout, stderr));
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

fn program_logs(stdout: Option<&str>, stderr: Option<&str>) -> Vec<ContentBlock> {
    let mut content = Vec::new();
    if let Some(stdout) = stdout.filter(|value| !value.is_empty()) {
        content.extend(text_content(format!("stdout:\n{stdout}")));
    }
    if let Some(stderr) = stderr.filter(|value| !value.is_empty()) {
        content.extend(text_content(format!("stderr:\n{stderr}")));
    }
    content
}

fn retained_program_content(
    descriptors: &[TypeScriptTool],
    tool_state: &[ToolState],
) -> Vec<ContentBlock> {
    retained_program_content_with_name(tool_state, |runtime_name| {
        descriptors
            .iter()
            .find(|descriptor| descriptor.name == runtime_name)
            .map(|descriptor| {
                format!(
                    "tools.{}.{}",
                    descriptor.programmatic_name.namespace, descriptor.programmatic_name.name
                )
            })
            .unwrap_or_else(|| format!("tools.{runtime_name}"))
    })
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
        input: &ProgramInput,
    ) -> Result<ClassifiedProgramInput, CoreError> {
        match input {
            ProgramInput::ToolResult { action_id, result } => {
                let disposition = self
                    .tool_result_disposition(context, action_id)
                    .ok_or_else(|| {
                        CoreError::invariant("pending program tool operation was not found")
                    })?;
                Ok(match disposition {
                    ProgramToolResultDisposition::Commit { call } => ClassifiedProgramInput {
                        accepted_result: Some(accepted_program_result(&call, result.clone())),
                        transition: ProgramInputTransition::ResumeTool {
                            action_id: action_id.clone(),
                            result: result.clone(),
                        },
                    },
                    ProgramToolResultDisposition::AwaitPostHook {
                        call,
                        hook_binding_ids,
                    } => ClassifiedProgramInput {
                        accepted_result: None,
                        transition: ProgramInputTransition::AwaitPostHook {
                            action_id: action_id.clone(),
                            call,
                            hook_binding_ids,
                            result: result.clone(),
                        },
                    },
                })
            }
            ProgramInput::HookCompleted { action_id, result } => {
                match (self.pending_hook(action_id), result) {
                    (
                        Some(PendingProgramHook::Pre(original)),
                        HookResult::PreToolCall(PreToolCallOutput::Continue {
                            effective_arguments,
                        }),
                    ) => Ok(ClassifiedProgramInput {
                        accepted_result: None,
                        transition: ProgramInputTransition::ContinueAfterPreHook {
                            action_id: action_id.clone(),
                            effective_call: context
                                .effective_call(original, effective_arguments.clone())?,
                        },
                    }),
                    (
                        Some(PendingProgramHook::Pre(call)),
                        HookResult::PreToolCall(PreToolCallOutput::Skip { reason }),
                    ) => {
                        let result = skipped_tool_result(reason.clone())?;
                        Ok(ClassifiedProgramInput {
                            accepted_result: Some(accepted_program_result(call, result.clone())),
                            transition: ProgramInputTransition::ResumeHook {
                                action_id: action_id.clone(),
                                result,
                            },
                        })
                    }
                    (
                        Some(PendingProgramHook::Post(call)),
                        HookResult::PostToolCall { tool_result },
                    ) => Ok(ClassifiedProgramInput {
                        accepted_result: Some(accepted_program_result(call, tool_result.clone())),
                        transition: ProgramInputTransition::ResumeHook {
                            action_id: action_id.clone(),
                            result: tool_result.clone(),
                        },
                    }),
                    (Some(PendingProgramHook::Pre(_)), _) => Err(CoreError::invalid_command(
                        "pre-tool hook requires a pre_tool_call result",
                    )),
                    (Some(PendingProgramHook::Post(_)), _) => Err(CoreError::invalid_command(
                        "post-tool hook requires a post_tool_call result",
                    )),
                    (None, _) => Err(CoreError::invariant("pending program hook was not found")),
                }
            }
            ProgramInput::HookFailed { action_id, error } => {
                let pending = self
                    .pending_hook(action_id)
                    .ok_or_else(|| CoreError::invariant("pending program hook was not found"))?;
                let call = pending.call();
                let result = hook_failure_tool_result(error.clone());
                Ok(ClassifiedProgramInput {
                    accepted_result: Some(accepted_program_result(call, result.clone())),
                    transition: ProgramInputTransition::ResumeHook {
                        action_id: action_id.clone(),
                        result,
                    },
                })
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
        let ClassifiedProgramInput {
            accepted_result,
            transition,
        } = self.classify_input(context, &input)?;
        let advance = match transition {
            ProgramInputTransition::ResumeTool { action_id, result } => self.resume(
                context,
                parent_call,
                ProgramResume::Tool {
                    action_id: &action_id,
                },
                result,
                determinism,
            ),
            ProgramInputTransition::AwaitPostHook {
                action_id,
                call,
                hook_binding_ids,
                result,
            } => {
                let hook_action_id = hook_action_id(&call.action_id, HookPoint::PostToolCall);
                let action = Action::hook(
                    hook_action_id.clone(),
                    context.turn_id(),
                    hook_binding_ids.clone(),
                    HookCall::PostToolCall {
                        tool_call: (&call).into(),
                        tool_result: result.clone(),
                    },
                )?;
                self.await_post_hook(&action_id, hook_action_id, hook_binding_ids, result)?;
                Ok(ProgramAdvance::Pending {
                    actions: vec![action],
                })
            }
            ProgramInputTransition::ContinueAfterPreHook {
                action_id,
                effective_call,
            } => {
                self.continue_after_pre_hook(&action_id, effective_call.clone())?;
                Ok(ProgramAdvance::Pending {
                    actions: vec![Action::external_tool(&effective_call, context.turn_id())],
                })
            }
            ProgramInputTransition::ResumeHook { action_id, result } => self.resume(
                context,
                parent_call,
                ProgramResume::Hook {
                    action_id: &action_id,
                },
                result,
                determinism,
            ),
        }?;
        Ok(ProgramTransition {
            advance,
            accepted_result,
        })
    }

    fn resume(
        &mut self,
        context: ProgramContext<'_>,
        parent_call: &ToolCall,
        resume: ProgramResume<'_>,
        result: ToolResult,
        determinism: DeterminismContext,
    ) -> Result<ProgramAdvance, CoreError> {
        let Some((outcome, actions)) =
            resume_program_execution(context, parent_call, self, resume, result, determinism)?
        else {
            return Ok(ProgramAdvance::Pending {
                actions: Vec::new(),
            });
        };
        Ok(match outcome {
            ProgramOutcome::Pending(execution) => {
                *self = execution;
                ProgramAdvance::Pending { actions }
            }
            ProgramOutcome::Completed(completed) => {
                if !actions.is_empty() {
                    return Err(CoreError::invariant(
                        "completed program execution produced pending actions",
                    ));
                }
                ProgramAdvance::Completed { completed }
            }
        })
    }
}

fn accepted_program_result(call: &ExternalToolCall, result: ToolResult) -> AcceptedProgramResult {
    AcceptedProgramResult {
        action_id: call.action_id.clone(),
        operation_id: call.call_id.clone(),
        result,
    }
}

fn resume_program_execution(
    context: ProgramContext<'_>,
    parent_call: &ToolCall,
    program: &mut ProgramExecution,
    resume: ProgramResume<'_>,
    result: ToolResult,
    determinism: DeterminismContext,
) -> Result<Option<(ProgramOutcome, Vec<Action>)>, CoreError> {
    let continuation = match resume {
        ProgramResume::Tool { action_id } => program.resolve_pending_tool(action_id, result)?,
        ProgramResume::Hook { action_id } => program.resolve_pending_hook(action_id, result)?,
    };
    if let Some(continuation) = continuation {
        let (next_state, next_effects) = drive_program_execution(
            context,
            parent_call,
            continuation.partial_evaluation,
            continuation.round,
            determinism,
        )?;
        return Ok(Some((next_state, next_effects)));
    }
    Ok(None)
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
    use crate::core::testing::*;
    use crate::core::wire::content::{
        Annotations, ImageContent, MetaObject, Resource, Role as ContentRole,
    };

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
                if content.text == "Additional content from `tools.file_system.read_file` (call 1):"
        ));
        assert_eq!(content[2], image);
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
    fn invalid_programmatic_tool_arguments_return_an_error_and_continue_the_turn() {
        let step = advance_llm(
            started(),
            assistant_tool(
                "run_typescript",
                json!({
                    "code": "async function main() { return tools.agent.spawn({ agentName: 'researcher', description: 'Investigate' }); }"
                }),
            ),
        );

        assert!(matches!(step.effect, Some(Action::Completion { .. })));
        let message = step.state.context.messages().last().unwrap();
        assert_eq!(message_role(&message.message), Role::Tool);
        assert!(message_text(message).contains("jsonSchemaArgumentValidationError"));
        assert!(message_text(message).contains("message"));
    }

    #[test]
    fn subagent_tools_use_the_runtime_subagent_route() {
        let step = advance_llm(
            started(),
            assistant_tool(
                "run_typescript",
                json!({"code": "async function main() { return tools.agent.spawn({agentName: 'researcher', message: 'Investigate'}); }"}),
            ),
        );

        assert!(matches!(
            step.effect,
            Some(Action::RuntimeBuiltinTool { call, .. })
                if call.name
                    == crate::core::tools::external::RuntimeBuiltinToolName::SubagentSpawn
        ));
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
