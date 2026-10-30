#[cfg(test)]
use serde_json::json;

use crate::core::action_id;
use crate::core::error::CoreError;
use crate::core::features::large_output::{ResultDisposition, evaluate_result};
use crate::core::features::permissions::{ApprovalFailureReason, ApprovalOutcome};
use crate::core::features::programmatic_tool_calling::{
    AcceptedProgramResult, CompletedProgramResult, ProgramAdvance, ProgramInput, ProgramOutcome,
    ProgramTransition, dispatch as dispatch_program,
};
use crate::core::features::tool_discovery;
use crate::core::hooks::{
    HookPoint, HookResult, PreToolCallOutput, hook_failure_tool_result, skipped_tool_result,
};
use crate::core::step_protocol::FilesystemResult;
use crate::core::step_protocol::{Action, DeterminismContext, Observation};
use crate::core::tools::context::ToolContext;
use crate::core::tools::execution::external_call::{
    Admission, ApprovalName, ExternalCallStage, HookSide, PendingExternalCall,
};
use crate::core::tools::execution::{
    LargeOutputSource, ToolBatch, ToolExecution, ToolExecutionState,
};
use crate::core::tools::external::ExternalToolCall;
use crate::core::tools::result::{
    approval_failed_result, approval_rejected_result, invalid_tool_call_result,
    model_tool_result_message,
};
use crate::core::wire::message::Message;
use crate::core::wire::tool::{ProtocolError, ToolCall, ToolResult};

use super::direct::direct_tool_execution_from_resolved_tools;

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
    let mixed_batch = calls.len() > 1;
    for call in calls {
        let (execution, mut call_effects, call_observations) = if mixed_batch && call.name == "wait"
        {
            completed_execution(
                tools.turn_id,
                call,
                invalid_tool_call_result("Call wait alone.".to_string()),
                Vec::new(),
            )
        } else {
            start_tool_execution(tools, call, determinism)?
        };
        executions.push(execution);
        effects.append(&mut call_effects);
        observations.extend(call_observations);
    }
    Ok(ToolBatchTransition {
        resolution: resolve_tool_batch(ToolBatch { executions }, effects),
        observations,
    })
}

/// What the model sees for arguments that are not JSON: the parse error, what
/// Core expects instead, and, when the arguments simply stop, the likely cause.
fn invalid_json_arguments_reason(error: &str) -> String {
    let mut reason = format!(
        "tool arguments are not valid JSON ({error}). Send the arguments as one JSON object that matches the tool's input schema."
    );
    if error.starts_with("EOF while parsing") {
        reason.push_str(
            " The arguments end early; if they were cut off, split long content into several smaller tool calls.",
        );
    }
    reason
}

fn start_tool_execution(
    tools: ToolContext<'_>,
    call: ToolCall,
    determinism: DeterminismContext,
) -> Result<(ToolExecution, Vec<Action>, Vec<Observation>), CoreError> {
    if let Some(error) = &call.argument_error {
        let result = invalid_tool_call_result(invalid_json_arguments_reason(error));
        return Ok(completed_execution(tools.turn_id, call, result, Vec::new()));
    }
    // With programmatic tool calling disabled, `search_tool_functions` and
    // `run_typescript` fall through to the direct routes and fail as unsupported.
    if tools.programmatic_enabled()
        && let Some(result) = tool_discovery::dispatch(&call, |request| tools.search(request))
    {
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
    let mut accepted_results = Vec::new();
    if tools.programmatic_enabled()
        && let Some(result) = tools.with_program_context(|context| {
            dispatch_program(context, &call, determinism, &mut accepted_results)
        })
    {
        let (outcome, effects) = result?;
        let mut observations = accepted_program_results(tools.turn_id, accepted_results);
        return match outcome {
            ProgramOutcome::Pending(execution) => Ok((
                ToolExecution {
                    call,
                    state: ToolExecutionState::ProgramPending { execution },
                },
                effects,
                observations,
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
                observations.extend(finalized.observations);
                Ok((
                    ToolExecution {
                        call,
                        state: finalized.state,
                    },
                    finalized.actions,
                    observations,
                ))
            }
        };
    }
    let mut pending = match direct_tool_execution_from_resolved_tools(tools, &call, determinism) {
        Ok(pending) => pending,
        Err(error) => {
            return Ok(completed_execution(
                tools.turn_id,
                call,
                invalid_tool_call_result(error.detail().to_string()),
                Vec::new(),
            ));
        }
    };
    if matches!(pending.stage, ExternalCallStage::Pending) {
        match tools.admit(&pending.call) {
            Admission::Run => {}
            Admission::Deny(result) => {
                let completed = completed_top_level_result(
                    tools,
                    &call,
                    LargeOutputSource::Direct { call: pending.call },
                    result,
                );
                return Ok((
                    ToolExecution {
                        call,
                        state: completed.state,
                    },
                    completed.actions,
                    completed.observations,
                ));
            }
            Admission::Ask(grant_key) => {
                pending = PendingExternalCall::awaiting_approval(
                    pending.call,
                    grant_key,
                    ApprovalName::Direct,
                );
            }
        }
    }
    let action = pending.action(tools.turn_id);
    Ok((
        ToolExecution {
            call,
            state: ToolExecutionState::Direct(pending),
        },
        vec![action],
        Vec::new(),
    ))
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
        accepted_results,
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
    observations.extend(accepted_program_results(tools.turn_id, accepted_results));
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
    // A file read already returns a bounded page and names the offset to read
    // on from; saving that page to another file would only add a detour.
    let excluded = matches!(
        &source,
        LargeOutputSource::Direct { call }
            if call.call.is_skill_read() || call.call.is_file_read()
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
        call: accepted.call,
        result: accepted.result,
    }
}

fn accepted_program_results(
    turn_id: &str,
    accepted_results: Vec<AcceptedProgramResult>,
) -> Vec<Observation> {
    accepted_results
        .into_iter()
        .map(|accepted| accepted_program_result(turn_id, accepted))
        .collect()
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
        call: call.call.clone(),
        result,
    }
}

fn completed_execution(
    turn_id: &str,
    call: ToolCall,
    result: ToolResult,
    mut observations: Vec<Observation>,
) -> (ToolExecution, Vec<Action>, Vec<Observation>) {
    let state = completed_external_tool_state(&call, &result);
    observations.push(tool_execution_finished(turn_id, &call.id, result));
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
            ToolExecutionState::Direct(pending) => pending.awaits_tool(action_id),
            ToolExecutionState::ProgramPending { execution } => {
                execution.handles_tool_action(action_id)
            }
            ToolExecutionState::AwaitingLargeOutputWrite { .. } => false,
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
        ToolExecutionState::Direct(pending) => {
            let post_hook_binding_ids = tools
                .hook_binding_ids(HookPoint::PostToolCall, Some(&pending.call.hook_tool_key()));
            if !post_hook_binding_ids.is_empty() {
                effects.push(pending.await_post_hook(post_hook_binding_ids, result, tools.turn_id));
            } else {
                let finalized = finalize_tool_result(
                    tools,
                    &execution.call,
                    LargeOutputSource::Direct {
                        call: pending.call.clone(),
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
    let completed = completed_top_level_result(tools, &execution.call, source, effective_result);
    execution.state = completed.state;
    Ok(ToolBatchTransition {
        resolution: resolve_tool_batch(batch, Vec::new()),
        observations: completed.observations,
    })
}

pub(crate) fn finish_tool_batch_approval(
    tools: ToolContext<'_>,
    mut batch: ToolBatch,
    action_id: &str,
    resolution: Result<ApprovalOutcome, ApprovalFailureReason>,
    determinism: DeterminismContext,
) -> Result<ToolBatchTransition, CoreError> {
    for execution_index in 0..batch.executions.len() {
        let ToolExecutionState::ProgramPending { execution: program } =
            &mut batch.executions[execution_index].state
        else {
            continue;
        };

        if program.pending_approval(action_id).is_none() {
            continue;
        }

        let result = match resolution {
            Ok(ApprovalOutcome::Approve) => {
                // Approval time never counts toward the program time budget;
                // an approved wait is fit to what remains of it.
                let budget_ms =
                    tools.with_program_context(|context| context.settings().max_elapsed_ms);
                match program
                    .approve_pending_approval(
                        action_id,
                        budget_ms,
                        determinism.time_unix_ms,
                        tools.turn_id,
                    )
                    .expect("program approval was found before resolution")
                {
                    Ok(action) => {
                        return Ok(ToolBatchTransition {
                            resolution: resolve_tool_batch(batch, vec![action]),
                            observations: Vec::new(),
                        });
                    }
                    Err(not_started) => not_started,
                }
            }
            Ok(ApprovalOutcome::Reject) => approval_rejected_result(),
            Err(failure_reason) => approval_failed_result(failure_reason),
        };
        let mut actions = Vec::new();
        let mut observations = Vec::new();
        advance_program(
            tools,
            &mut batch.executions[execution_index],
            ProgramInput::ApprovalResult {
                action_id: action_id.to_owned(),
                result,
            },
            determinism,
            &mut actions,
            &mut observations,
        )?;

        return Ok(ToolBatchTransition {
            resolution: resolve_tool_batch(batch, actions),
            observations,
        });
    }

    let execution = batch
        .executions
        .iter_mut()
        .find(|execution| {
            matches!(
                &execution.state,
                ToolExecutionState::Direct(pending) if pending.awaits_approval(action_id)
            )
        })
        .ok_or_else(|| {
            CoreError::invalid_command(format!(
                "pending approval action {action_id:?} was not found"
            ))
        })?;

    let ToolExecutionState::Direct(pending) = &mut execution.state else {
        unreachable!();
    };

    let result = match resolution {
        Ok(ApprovalOutcome::Approve) => {
            let action = pending.send(pending.call.clone(), tools.turn_id);

            return Ok(ToolBatchTransition {
                resolution: resolve_tool_batch(batch, vec![action]),
                observations: Vec::new(),
            });
        }
        Ok(ApprovalOutcome::Reject) => approval_rejected_result(),
        Err(failure_reason) => approval_failed_result(failure_reason),
    };
    let call = pending.call.clone();
    let completed = completed_top_level_result(
        tools,
        &execution.call,
        LargeOutputSource::Direct { call },
        result,
    );
    execution.state = completed.state;

    Ok(ToolBatchTransition {
        resolution: resolve_tool_batch(batch, Vec::new()),
        observations: completed.observations,
    })
}

fn completed_external_tool_state(call: &ToolCall, result: &ToolResult) -> ToolExecutionState {
    ToolExecutionState::Completed {
        message: model_tool_result_message(call.id.clone(), call.name.clone(), result),
    }
}

#[derive(Clone)]
enum ToolHookLocation {
    Direct {
        execution_index: usize,
        side: HookSide,
    },
    Program {
        execution_index: usize,
        hook_action_id: String,
    },
}

fn locate_tool_hook(batch: &ToolBatch, action_id: &str) -> Option<ToolHookLocation> {
    for (execution_index, execution) in batch.executions.iter().enumerate() {
        match &execution.state {
            ToolExecutionState::Direct(pending) => {
                if let Some(side) = pending.awaited_hook(action_id) {
                    return Some(ToolHookLocation::Direct {
                        execution_index,
                        side,
                    });
                }
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
        ToolHookLocation::Direct {
            execution_index,
            side,
        } => {
            let execution = &mut batch.executions[execution_index];
            let ToolExecutionState::Direct(pending) = &mut execution.state else {
                unreachable!();
            };
            let finished = match (side, result) {
                (
                    HookSide::Pre,
                    HookResult::PreToolCall(PreToolCallOutput::Continue {
                        effective_arguments,
                    }),
                ) => {
                    let effective_call =
                        tools.effective_call(&pending.call, effective_arguments)?;
                    match tools.admit(&effective_call) {
                        Admission::Run => {
                            effects.push(pending.send(effective_call, tools.turn_id));
                        }
                        Admission::Deny(result) => {
                            let completed = completed_top_level_result(
                                tools,
                                &execution.call,
                                LargeOutputSource::Direct {
                                    call: effective_call,
                                },
                                result,
                            );
                            execution.state = completed.state;
                            observations.extend(completed.observations);
                        }
                        Admission::Ask(grant_key) => {
                            effects.push(pending.await_approval(
                                effective_call,
                                grant_key,
                                ApprovalName::Direct,
                                tools.turn_id,
                            ));
                        }
                    }
                    None
                }
                (HookSide::Pre, HookResult::PreToolCall(PreToolCallOutput::Skip { reason })) => {
                    Some((skipped_tool_result(reason)?, pending.call.clone()))
                }
                (HookSide::Post, HookResult::PostToolCall { tool_result }) => {
                    Some((tool_result, pending.call.clone()))
                }
                (HookSide::Pre, _) => {
                    return Err(CoreError::invalid_command(
                        "pre-tool hook requires a pre_tool_call result",
                    ));
                }
                (HookSide::Post, _) => {
                    return Err(CoreError::invalid_command(
                        "post-tool hook requires a post_tool_call result",
                    ));
                }
            };
            if let Some((result, call)) = finished {
                let finalized = finalize_tool_result(
                    tools,
                    &execution.call,
                    LargeOutputSource::Direct { call },
                    result,
                )?;
                execution.state = finalized.state;
                effects.extend(finalized.actions);
                observations.extend(finalized.observations);
            }
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
        ToolHookLocation::Direct {
            execution_index, ..
        } => {
            let execution = &mut batch.executions[execution_index];
            let result = hook_failure_tool_result(error);
            let ToolExecutionState::Direct(pending) = &execution.state else {
                unreachable!();
            };
            let finalized = finalize_tool_result(
                tools,
                &execution.call,
                LargeOutputSource::Direct {
                    call: pending.call.clone(),
                },
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
        assert!(message_text(message).contains("tool arguments are not valid JSON"));
        assert!(message_text(message).contains("split long content"));
    }

    #[test]
    fn invalid_json_reason_names_the_expected_shape() {
        let reason = invalid_json_arguments_reason("expected value at line 1 column 1");
        assert_eq!(
            reason,
            "tool arguments are not valid JSON (expected value at line 1 column 1). Send the arguments as one JSON object that matches the tool's input schema."
        );
        assert!(
            invalid_json_arguments_reason("EOF while parsing a string at line 1 column 9")
                .ends_with("split long content into several smaller tool calls.")
        );
    }
}
