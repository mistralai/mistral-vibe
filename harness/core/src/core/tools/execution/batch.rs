#[cfg(test)]
use serde_json::json;

use crate::core::action_id;
use crate::core::error::CoreError;
use crate::core::features::large_output::{ResultDisposition, evaluate_result};
use crate::core::features::programmatic_tool_calling::{
    AcceptedProgramResult, CompletedProgramResult, ProgramAdvance, ProgramInput, ProgramOutcome,
    ProgramTransition, dispatch as dispatch_program,
};
use crate::core::features::tool_discovery;
use crate::core::hooks::{
    HookCall, HookPoint, HookResult, PreToolCallOutput, hook_action_id, hook_failure_tool_result,
    skipped_tool_result,
};
use crate::core::step_protocol::FilesystemResult;
use crate::core::step_protocol::{Action, DeterminismContext, Observation};
use crate::core::tools::context::ToolContext;
use crate::core::tools::execution::{
    LargeOutputSource, ToolBatch, ToolExecution, ToolExecutionState,
};
use crate::core::tools::external::ExternalToolCall;
use crate::core::tools::result::{invalid_tool_call_result, model_tool_result_message};
use crate::core::wire::message::Message;
use crate::core::wire::tool::{ProtocolError, ToolCall, ToolResult};

use super::direct::{direct_tool_execution_from_resolved_tools, external_tool_effect};

#[derive(Clone, Debug, PartialEq)]
pub(crate) enum ToolBatchResolution {
    AwaitingActions {
        batch: ToolBatch,
        actions: Vec<Action>,
    },
    ContinueTurn {
        messages: Vec<Message>,
    },
}

pub(crate) struct ToolBatchTransition {
    pub(crate) resolution: ToolBatchResolution,
    pub(crate) observations: Vec<Observation>,
}

pub(crate) fn start_tool_batch(
    tools: ToolContext<'_>,
    calls: Vec<ToolCall>,
    determinism: DeterminismContext,
) -> Result<ToolBatchTransition, CoreError> {
    let mut executions = Vec::with_capacity(calls.len());
    let mut effects = Vec::new();
    let mut observations = calls
        .iter()
        .map(|call| tool_execution_started(tools.turn_id, &call.id))
        .collect::<Vec<_>>();
    for call in calls {
        let (execution, mut call_effects, call_observations) =
            start_tool_execution(tools, call, determinism)?;
        executions.push(execution);
        effects.append(&mut call_effects);
        observations.extend(call_observations);
    }
    Ok(ToolBatchTransition {
        resolution: resolve_tool_batch(ToolBatch { executions }, effects),
        observations,
    })
}

fn start_tool_execution(
    tools: ToolContext<'_>,
    call: ToolCall,
    determinism: DeterminismContext,
) -> Result<(ToolExecution, Vec<Action>, Vec<Observation>), CoreError> {
    if let Some(error) = &call.argument_error {
        let result = invalid_tool_call_result(error.clone());
        return Ok(completed_execution(tools.turn_id, call, result, Vec::new()));
    }
    if let Some(result) = tool_discovery::dispatch(&call, |request| tools.search(request)) {
        let result = result?;
        let observations = result
            .summary
            .map(|summary| Observation::ToolDiscoveryFinished {
                turn_id: tools.turn_id.to_string(),
                call_id: call.id.clone(),
                summary,
            })
            .into_iter()
            .collect();
        return Ok(completed_execution(
            tools.turn_id,
            call,
            result.result,
            observations,
        ));
    }
    if let Some(result) =
        tools.with_program_context(|context| dispatch_program(context, &call, determinism))
    {
        let (outcome, effects) = result?;
        return match outcome {
            ProgramOutcome::Pending(execution) => Ok((
                ToolExecution {
                    call,
                    state: ToolExecutionState::ProgramPending { execution },
                },
                effects,
                Vec::new(),
            )),
            ProgramOutcome::Completed(completed) => {
                if !effects.is_empty() {
                    return Err(CoreError::invariant(
                        "completed program execution produced pending actions",
                    ));
                }
                let finalized = finalize_top_level_result(
                    tools,
                    &call,
                    LargeOutputSource::RunTypescript,
                    *completed,
                )?;
                Ok((
                    ToolExecution {
                        call,
                        state: finalized.state,
                    },
                    finalized.actions,
                    finalized.observations,
                ))
            }
        };
    }
    match direct_tool_execution_from_resolved_tools(tools, &call) {
        Ok((execution, effect)) => Ok((
            ToolExecution {
                call,
                state: execution,
            },
            vec![effect],
            Vec::new(),
        )),
        Err(error) => Ok(completed_execution(
            tools.turn_id,
            call,
            invalid_tool_call_result(error.detail().to_string()),
            Vec::new(),
        )),
    }
}

fn advance_program(
    tools: ToolContext<'_>,
    execution: &mut ToolExecution,
    input: ProgramInput,
    determinism: DeterminismContext,
    actions: &mut Vec<Action>,
    observations: &mut Vec<Observation>,
) -> Result<(), CoreError> {
    let parent_call = execution.call.clone();
    let ProgramTransition {
        advance,
        accepted_result,
    } = {
        let ToolExecutionState::ProgramPending { execution: program } = &mut execution.state else {
            return Err(CoreError::invariant(
                "program advance requires a pending program execution",
            ));
        };
        tools.with_program_context(|context| {
            program.advance(context, &parent_call, input, determinism)
        })?
    };
    if let Some(accepted) = accepted_result {
        observations.push(accepted_program_result(tools.turn_id, accepted));
    }
    match advance {
        ProgramAdvance::Pending {
            actions: next_actions,
        } => actions.extend(next_actions),
        ProgramAdvance::Completed { completed } => {
            let finalized = finalize_top_level_result(
                tools,
                &parent_call,
                LargeOutputSource::RunTypescript,
                *completed,
            )?;
            execution.state = finalized.state;
            actions.extend(finalized.actions);
            observations.extend(finalized.observations);
        }
    }
    Ok(())
}

struct FinalizedTopLevelResult {
    state: ToolExecutionState,
    actions: Vec<Action>,
    observations: Vec<Observation>,
}

fn finalize_top_level_result(
    tools: ToolContext<'_>,
    model_call: &ToolCall,
    source: LargeOutputSource,
    completed: CompletedProgramResult,
) -> Result<FinalizedTopLevelResult, CoreError> {
    finalize_tool_result(tools, model_call, source, completed.result)
}

fn finalize_tool_result(
    tools: ToolContext<'_>,
    model_call: &ToolCall,
    source: LargeOutputSource,
    result: ToolResult,
) -> Result<FinalizedTopLevelResult, CoreError> {
    let excluded = matches!(
        &source,
        LargeOutputSource::Direct { call } if call.call.is_skill_read()
    );
    let evaluated = (!excluded)
        .then(|| {
            evaluate_result(
                tools.large_output_policy(),
                &model_call.name,
                &model_call.id,
                result.clone(),
            )
        })
        .transpose()?
        .flatten();
    let Some(evaluated) = evaluated else {
        return Ok(completed_top_level_result(
            tools, model_call, source, result,
        ));
    };
    let observation = Observation::LargeOutputSerialized {
        turn_id: tools.turn_id.to_string(),
        call_id: model_call.id.clone(),
        tool_name: model_call.name.clone(),
        serialized_char_count: evaluated.serialized_char_count,
    };
    match evaluated.disposition {
        ResultDisposition::Inline(result) => {
            let mut completed = completed_top_level_result(tools, model_call, source, result);
            completed.observations.insert(0, observation);
            Ok(completed)
        }
        ResultDisposition::Write(pending) => {
            let origin = match source {
                LargeOutputSource::Direct { .. } => "direct",
                LargeOutputSource::RunTypescript => "run_typescript",
            };
            let action_id = action_id::filesystem("write", origin, &model_call.id);
            let action = Action::filesystem_write(
                action_id.clone(),
                tools.turn_id,
                pending.relative_path().to_string(),
                pending.serialized().to_string(),
            );
            Ok(FinalizedTopLevelResult {
                state: ToolExecutionState::AwaitingLargeOutputWrite {
                    action_id,
                    source,
                    pending,
                },
                actions: vec![action],
                observations: vec![observation],
            })
        }
    }
}

fn completed_top_level_result(
    tools: ToolContext<'_>,
    model_call: &ToolCall,
    source: LargeOutputSource,
    result: ToolResult,
) -> FinalizedTopLevelResult {
    let mut observations = match source {
        LargeOutputSource::Direct { call } => vec![accepted_direct_result(
            tools.turn_id,
            &model_call.id,
            &call,
            result.clone(),
        )],
        LargeOutputSource::RunTypescript => Vec::new(),
    };
    observations.push(tool_execution_finished(
        tools.turn_id,
        &model_call.id,
        result.clone(),
    ));
    FinalizedTopLevelResult {
        state: completed_external_tool_state(model_call, &result),
        actions: Vec::new(),
        observations,
    }
}

fn accepted_program_result(turn_id: &str, accepted: AcceptedProgramResult) -> Observation {
    Observation::ToolResultCommitted {
        turn_id: turn_id.to_string(),
        action_id: accepted.action_id,
        call_id: accepted.operation_id,
        result: accepted.result,
    }
}

fn accepted_direct_result(
    turn_id: &str,
    operation_id: &str,
    call: &ExternalToolCall,
    result: ToolResult,
) -> Observation {
    Observation::ToolResultCommitted {
        turn_id: turn_id.to_string(),
        action_id: call.action_id.clone(),
        call_id: operation_id.to_string(),
        result,
    }
}

fn completed_execution(
    turn_id: &str,
    call: ToolCall,
    result: ToolResult,
    mut observations: Vec<Observation>,
) -> (ToolExecution, Vec<Action>, Vec<Observation>) {
    observations.push(tool_execution_finished(turn_id, &call.id, result.clone()));
    let state = completed_external_tool_state(&call, &result);
    (ToolExecution { call, state }, Vec::new(), observations)
}

fn tool_execution_started(turn_id: &str, call_id: &str) -> Observation {
    Observation::ToolExecutionStarted {
        turn_id: turn_id.to_string(),
        call_id: call_id.to_string(),
    }
}

fn tool_execution_finished(turn_id: &str, call_id: &str, result: ToolResult) -> Observation {
    Observation::ToolExecutionFinished {
        turn_id: turn_id.to_string(),
        call_id: call_id.to_string(),
        result,
    }
}

fn resolve_tool_batch(batch: ToolBatch, actions: Vec<Action>) -> ToolBatchResolution {
    if batch
        .executions
        .iter()
        .all(|execution| matches!(execution.state, ToolExecutionState::Completed { .. }))
    {
        let messages = batch
            .executions
            .into_iter()
            .map(|execution| {
                let ToolExecutionState::Completed { message } = execution.state else {
                    unreachable!();
                };
                message
            })
            .collect();
        return ToolBatchResolution::ContinueTurn { messages };
    }
    ToolBatchResolution::AwaitingActions { batch, actions }
}

pub(crate) fn finish_tool_batch_action(
    tools: ToolContext<'_>,
    mut batch: ToolBatch,
    action_id: &str,
    result: ToolResult,
    determinism: DeterminismContext,
) -> Result<ToolBatchTransition, CoreError> {
    let execution_index = batch
        .executions
        .iter()
        .position(|execution| match &execution.state {
            ToolExecutionState::DirectPending { call } => call.action_id == action_id,
            ToolExecutionState::ProgramPending { execution } => {
                execution.handles_tool_action(action_id)
            }
            ToolExecutionState::DirectAwaitingPreHook { .. }
            | ToolExecutionState::DirectAwaitingPostHook { .. }
            | ToolExecutionState::AwaitingLargeOutputWrite { .. } => false,
            ToolExecutionState::Completed { .. } => false,
        })
        .ok_or_else(|| {
            CoreError::invariant(format!("pending tool action {action_id:?} was not found"))
        })?;
    let execution = batch
        .executions
        .get_mut(execution_index)
        .expect("located execution index");
    let mut effects = Vec::new();
    let mut observations = Vec::new();
    match &mut execution.state {
        ToolExecutionState::DirectPending { call } => {
            let effective_call = call.clone();
            let post_hook_binding_ids = tools.hook_binding_ids(
                HookPoint::PostToolCall,
                Some(&effective_call.hook_tool_key()),
            );
            if !post_hook_binding_ids.is_empty() {
                let hook_action_id =
                    hook_action_id(&effective_call.action_id, HookPoint::PostToolCall);
                effects.push(Action::hook(
                    hook_action_id.clone(),
                    tools.turn_id,
                    post_hook_binding_ids.clone(),
                    HookCall::PostToolCall {
                        tool_call: (&effective_call).into(),
                        tool_result: result.clone(),
                    },
                )?);
                execution.state = ToolExecutionState::DirectAwaitingPostHook {
                    hook_action_id,
                    hook_binding_ids: post_hook_binding_ids,
                    call: effective_call,
                    result,
                };
            } else {
                let finalized = finalize_tool_result(
                    tools,
                    &execution.call,
                    LargeOutputSource::Direct {
                        call: effective_call,
                    },
                    result,
                )?;
                execution.state = finalized.state;
                effects.extend(finalized.actions);
                observations.extend(finalized.observations);
            }
        }
        ToolExecutionState::ProgramPending { .. } => {
            advance_program(
                tools,
                execution,
                ProgramInput::ToolResult {
                    action_id: action_id.to_string(),
                    result,
                },
                determinism,
                &mut effects,
                &mut observations,
            )?;
        }
        ToolExecutionState::DirectAwaitingPreHook { .. }
        | ToolExecutionState::DirectAwaitingPostHook { .. } => unreachable!(),
        ToolExecutionState::AwaitingLargeOutputWrite { .. } => unreachable!(),
        ToolExecutionState::Completed { .. } => unreachable!(),
    }
    Ok(ToolBatchTransition {
        resolution: resolve_tool_batch(batch, effects),
        observations,
    })
}

pub(crate) fn finish_tool_batch_filesystem_write(
    tools: ToolContext<'_>,
    mut batch: ToolBatch,
    action_id: &str,
    result: Result<FilesystemResult, ProtocolError>,
) -> Result<ToolBatchTransition, CoreError> {
    let execution = batch
        .executions
        .iter_mut()
        .find(|execution| {
            matches!(
                &execution.state,
                ToolExecutionState::AwaitingLargeOutputWrite {
                    action_id: expected,
                    ..
                } if expected == action_id
            )
        })
        .ok_or_else(|| {
            CoreError::invariant(format!(
                "pending filesystem write action {action_id:?} was not found"
            ))
        })?;
    let ToolExecutionState::AwaitingLargeOutputWrite {
        source, pending, ..
    } = execution.state.clone()
    else {
        unreachable!();
    };
    let effective_result = match result {
        Ok(FilesystemResult::Write { model_path }) => pending.complete(&model_path),
        Err(error) => pending.fail(&error),
    };
    let mut observations = match source {
        LargeOutputSource::Direct { call } => vec![accepted_direct_result(
            tools.turn_id,
            &execution.call.id,
            &call,
            effective_result.clone(),
        )],
        LargeOutputSource::RunTypescript => Vec::new(),
    };
    observations.push(tool_execution_finished(
        tools.turn_id,
        &execution.call.id,
        effective_result.clone(),
    ));
    execution.state = completed_external_tool_state(&execution.call, &effective_result);
    Ok(ToolBatchTransition {
        resolution: resolve_tool_batch(batch, Vec::new()),
        observations,
    })
}

fn completed_external_tool_state(call: &ToolCall, result: &ToolResult) -> ToolExecutionState {
    ToolExecutionState::Completed {
        message: model_tool_result_message(call.id.clone(), call.name.clone(), result),
    }
}

#[derive(Clone)]
enum ToolHookLocation {
    DirectPre(usize),
    DirectPost(usize),
    Program {
        execution_index: usize,
        hook_action_id: String,
    },
}

fn locate_tool_hook(batch: &ToolBatch, action_id: &str) -> Option<ToolHookLocation> {
    for (execution_index, execution) in batch.executions.iter().enumerate() {
        match &execution.state {
            ToolExecutionState::DirectAwaitingPreHook { hook_action_id, .. }
                if hook_action_id == action_id =>
            {
                return Some(ToolHookLocation::DirectPre(execution_index));
            }
            ToolExecutionState::DirectAwaitingPostHook { hook_action_id, .. }
                if hook_action_id == action_id =>
            {
                return Some(ToolHookLocation::DirectPost(execution_index));
            }
            ToolExecutionState::ProgramPending { execution: program }
                if program.handles_hook_action(action_id) =>
            {
                return Some(ToolHookLocation::Program {
                    execution_index,
                    hook_action_id: action_id.to_string(),
                });
            }
            _ => {}
        }
    }
    None
}

pub(crate) fn finish_tool_batch_hook(
    tools: ToolContext<'_>,
    mut batch: ToolBatch,
    action_id: &str,
    result: HookResult,
    determinism: DeterminismContext,
) -> Result<ToolBatchTransition, CoreError> {
    let location = locate_tool_hook(&batch, action_id).ok_or_else(|| {
        CoreError::invariant(format!(
            "pending tool hook action {action_id:?} was not found"
        ))
    })?;
    let mut effects = Vec::new();
    let mut observations = Vec::new();
    match location {
        ToolHookLocation::DirectPre(execution_index) => {
            let execution = &mut batch.executions[execution_index];
            let ToolExecutionState::DirectAwaitingPreHook { call, .. } = &execution.state else {
                unreachable!();
            };
            let original = call.clone();
            let HookResult::PreToolCall(output) = result else {
                return Err(CoreError::invalid_command(
                    "pre-tool hook requires a pre_tool_call result",
                ));
            };
            match output {
                PreToolCallOutput::Continue {
                    effective_arguments,
                } => {
                    let effective_call = tools.effective_call(&original, effective_arguments)?;
                    effects.push(external_tool_effect(tools, &effective_call)?);
                    execution.state = ToolExecutionState::DirectPending {
                        call: effective_call,
                    };
                }
                PreToolCallOutput::Skip { reason } => {
                    let skipped = skipped_tool_result(reason)?;
                    let finalized = finalize_tool_result(
                        tools,
                        &execution.call,
                        LargeOutputSource::Direct { call: original },
                        skipped,
                    )?;
                    execution.state = finalized.state;
                    effects.extend(finalized.actions);
                    observations.extend(finalized.observations);
                }
            }
        }
        ToolHookLocation::DirectPost(execution_index) => {
            let execution = &mut batch.executions[execution_index];
            let HookResult::PostToolCall { tool_result } = result else {
                return Err(CoreError::invalid_command(
                    "post-tool hook requires a post_tool_call result",
                ));
            };
            let ToolExecutionState::DirectAwaitingPostHook { call, .. } = &execution.state else {
                unreachable!();
            };
            let finalized = finalize_tool_result(
                tools,
                &execution.call,
                LargeOutputSource::Direct { call: call.clone() },
                tool_result,
            )?;
            execution.state = finalized.state;
            effects.extend(finalized.actions);
            observations.extend(finalized.observations);
        }
        ToolHookLocation::Program {
            execution_index,
            hook_action_id,
        } => {
            advance_program(
                tools,
                &mut batch.executions[execution_index],
                ProgramInput::HookCompleted {
                    action_id: hook_action_id,
                    result,
                },
                determinism,
                &mut effects,
                &mut observations,
            )?;
        }
    }
    Ok(ToolBatchTransition {
        resolution: resolve_tool_batch(batch, effects),
        observations,
    })
}

pub(crate) fn fail_tool_batch_hook(
    tools: ToolContext<'_>,
    mut batch: ToolBatch,
    action_id: &str,
    error: ProtocolError,
    determinism: DeterminismContext,
) -> Result<ToolBatchTransition, CoreError> {
    let location = locate_tool_hook(&batch, action_id).ok_or_else(|| {
        CoreError::invariant(format!(
            "pending tool hook action {action_id:?} was not found"
        ))
    })?;
    let mut effects = Vec::new();
    let mut observations = Vec::new();
    match location {
        ToolHookLocation::DirectPre(execution_index)
        | ToolHookLocation::DirectPost(execution_index) => {
            let execution = &mut batch.executions[execution_index];
            let result = hook_failure_tool_result(error);
            let call = match &execution.state {
                ToolExecutionState::DirectAwaitingPreHook { call, .. }
                | ToolExecutionState::DirectAwaitingPostHook { call, .. } => call,
                _ => unreachable!(),
            };
            let finalized = finalize_tool_result(
                tools,
                &execution.call,
                LargeOutputSource::Direct { call: call.clone() },
                result,
            )?;
            execution.state = finalized.state;
            effects.extend(finalized.actions);
            observations.extend(finalized.observations);
        }
        ToolHookLocation::Program {
            execution_index,
            hook_action_id,
        } => {
            advance_program(
                tools,
                &mut batch.executions[execution_index],
                ProgramInput::HookFailed {
                    action_id: hook_action_id,
                    error,
                },
                determinism,
                &mut effects,
                &mut observations,
            )?;
        }
    }
    Ok(ToolBatchTransition {
        resolution: resolve_tool_batch(batch, effects),
        observations,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::testing::*;
    #[test]
    fn multiple_model_tool_calls_are_preserved_and_executed_in_order() {
        let mut config = config();
        config.capabilities.skills.push(SkillDefinition {
            name: "review".to_string(),
            description: "Review code.".to_string(),
            path: "/skills/review/SKILL.md".to_string(),
        });
        let message = assistant_tools(vec![
            ("skill-1", "skill", json!({"name": "review"})),
            ("skill-2", "skill", json!({"name": "review"})),
        ]);

        let first = advance_llm(started_with(config), message);
        let actions = first
            .effects
            .iter()
            .map(|effect| match effect {
                Action::RuntimeBuiltinTool {
                    effect_id,
                    operation_id,
                    ..
                } => (effect_id.clone(), operation_id.clone()),
                effect => panic!("expected skill effect, got {effect:?}"),
            })
            .collect::<Vec<_>>();
        assert_eq!(
            actions
                .iter()
                .map(|(_, operation_id)| operation_id.as_str())
                .collect::<Vec<_>>(),
            ["skill-1", "skill-2"]
        );

        let second = advance_tool(first.state, actions[1].0.clone(), json!("second"), None);
        assert_eq!(second.effect, None);

        let resumed = advance_tool(second.state, actions[0].0.clone(), json!("first"), None);
        assert!(matches!(resumed.effect, Some(Action::Completion { .. })));
        assert_eq!(
            resumed
                .state
                .context
                .messages()
                .iter()
                .filter_map(|stored| match &stored.message {
                    Message::Tool { tool_call_id, .. } => Some(tool_call_id.as_str()),
                    _ => None,
                })
                .collect::<Vec<_>>(),
            ["skill-1", "skill-2"]
        );
    }

    #[test]
    fn steering_after_programmatic_tool_result_preserves_the_program_continuation() {
        let program = advance_llm(
            started(),
            assistant_tool(
                "run_typescript",
                json!({"code": "async function main() { return tools.file_system.read_file({ path: 'a.txt' }); }"}),
            ),
        );
        let tool_action_id = awaiting_action_id(&program.state);
        let operation_id = match program.effect {
            Some(Action::RuntimeBuiltinTool { operation_id, .. }) => operation_id,
            effect => panic!("expected a programmatic tool effect, got {effect:?}"),
        };
        let steered = advance(
            program.state,
            user_message("use a different file", UserMessageMode::Steer),
        )
        .unwrap();

        assert_eq!(steered.effect, None);
        assert_eq!(awaiting_action_id(&steered.state), tool_action_id);

        let resumed = advance_tool(
            steered.state,
            tool_action_id,
            json!({"content": "old"}),
            None,
        );

        assert!(matches!(resumed.effect, Some(Action::Completion { .. })));
        assert_eq!(
            resumed
                .accepted_tool_result
                .as_ref()
                .map(|result| result.operation_id.as_str()),
            Some(operation_id.as_str())
        );
        assert!(
            message_text(
                &resumed.state.context.messages()[resumed.state.context.messages().len() - 2]
            )
            .contains("old")
        );
        assert_eq!(
            message_text(resumed.state.context.messages().last().unwrap()),
            "use a different file"
        );
    }

    #[test]
    fn invalid_control_tool_arguments_return_an_error_to_the_model() {
        let step = advance_llm(started(), assistant_tool("run_typescript", json!({})));

        assert!(matches!(step.effect, Some(Action::Completion { .. })));
        let message = step.state.context.messages().last().unwrap();
        assert_eq!(message_role(&message.message), Role::Tool);
        assert!(message_text(message).contains("InvalidToolCall"));
    }

    #[test]
    fn malformed_control_tool_arguments_preserve_the_provider_error() {
        let mut message = assistant_tool("run_typescript", json!({}));
        let Message::Assistant { content } = &mut message else {
            unreachable!()
        };
        let AssistantPart::Semantic(AssistantSemanticPart::ToolCall { arguments, .. }) =
            &mut content[0]
        else {
            unreachable!()
        };
        *arguments = ToolArguments::InvalidJson {
            raw: "{".to_string(),
            error: "tool arguments were not valid JSON at line 1 column 2".to_string(),
        };

        let step = advance_llm(started(), message);

        assert!(matches!(step.effect, Some(Action::Completion { .. })));
        let message = step.state.context.messages().last().unwrap();
        assert_eq!(message_role(&message.message), Role::Tool);
        assert!(message_text(message).contains("EOF while parsing an object"));
    }
}
