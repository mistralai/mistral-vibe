mod completion;
mod lifecycle_hooks;
mod outcome;

pub(crate) use lifecycle_hooks::{PendingLifecycleHook, SuspendedTurn};
pub(crate) use outcome::TurnOutcome;

use crate::core::error::CoreError;

use self::completion::candidate::{
    AcceptedCompletion, AcceptedCompletionContinuation, CandidateContext, CandidateHooks,
    CandidateTurn, CompletionEvent, CompletionFinish, CompletionResolution, CompletionTransition,
    CompletionWait, parse_completion_result, resolve_completion,
};
use self::completion::{AgentCompletionPlan, AgentCompletionRequest, plan_agent_completion};
use self::lifecycle_hooks::{
    HookResolution, resolve_failed_lifecycle_hook, resolve_lifecycle_hook,
};
use crate::core::features::compaction::{
    CompactionAttemptResolution, CompactionBudget, CompactionBudgets, CompactionRequest,
    CompactionResolution, CompactionStartResolution, PendingCompaction, fail_compaction,
    finish_compaction, start_compaction,
};
use crate::core::features::notifications::notification_message;
use crate::core::hooks::{HookCall, HookPoint, HookResult};
use crate::core::model_input_budget::estimate_model_input_tokens;
use crate::core::state::hook_binding_ids;
use crate::core::state::{
    ActivePhase, ActiveTurn, HarnessState, InterruptionResolution, QueuedTurn, TurnState,
};
use crate::core::step_protocol::Action;
use crate::core::step_protocol::DeterminismContext;
use crate::core::step_protocol::FilesystemResult;
use crate::core::step_protocol::{
    ActionAbandonedCause, CandidateDiscardCause, Observation, Outcome, TurnStopReason,
};
use crate::core::tools::execution::batch::{
    ToolBatchResolution, ToolBatchTransition, fail_tool_batch_hook, finish_tool_batch_action,
    finish_tool_batch_filesystem_write, finish_tool_batch_hook,
};
use crate::core::wire::completion::CompletionResult;
use crate::core::wire::content::ContentBlock;
use crate::core::wire::content::validate_non_empty_content;
use crate::core::wire::message::{Message, StoredMessage};
use crate::core::wire::tool::{ProtocolError, ToolResult};
use crate::core::wire::user_message::UserMessageMode;

pub(crate) enum TurnEvent {
    UserMessage {
        turn_id: String,
        content: Vec<ContentBlock>,
        mode: UserMessageMode,
    },
    ContextMessage {
        content: Vec<ContentBlock>,
    },
    ManualCompaction {
        extra_instructions: String,
    },
    Interrupt {
        expected_turn_id: String,
        reason: Option<String>,
    },
    FailTurn {
        expected_turn_id: String,
        action_id: String,
        error: ProtocolError,
    },
    Completion {
        action_id: String,
        result: Result<CompletionResult, ProtocolError>,
    },
    Hook {
        action_id: String,
        result: Result<HookResult, ProtocolError>,
    },
    Tool {
        action_id: String,
        call_id: String,
        report: ReportedToolResult,
    },
    Filesystem {
        action_id: String,
        result: Result<FilesystemResult, ProtocolError>,
    },
}

pub(crate) enum ReportedToolResult {
    Succeeded(ToolResult),
    Failed(ToolResult),
}

pub(crate) fn dispatch(
    state: &mut HarnessState,
    event: TurnEvent,
    determinism: DeterminismContext,
) -> Result<Outcome, CoreError> {
    match event {
        TurnEvent::UserMessage {
            turn_id,
            content,
            mode: UserMessageMode::Queue,
        } if !matches!(state.turn, TurnState::Compacting { .. }) => {
            handle_user_message(state, turn_id, content, UserMessageMode::Queue)
        }
        TurnEvent::UserMessage {
            turn_id,
            content,
            mode: UserMessageMode::Steer,
        } if state.active().is_some() => {
            handle_user_message(state, turn_id, content, UserMessageMode::Steer)
        }
        TurnEvent::UserMessage { .. } => Err(invalid_state(state)),

        TurnEvent::ContextMessage { content } if state.pending_action_ids().is_empty() => {
            handle_context_message(state, content)
        }
        TurnEvent::ContextMessage { .. } => Err(invalid_state(state)),

        TurnEvent::ManualCompaction { extra_instructions }
            if !matches!(
                state.turn,
                TurnState::Active(_) | TurnState::Compacting { .. }
            ) =>
        {
            start_manual_compaction(state, extra_instructions)
        }
        TurnEvent::ManualCompaction { .. } => Err(invalid_state(state)),

        TurnEvent::Interrupt {
            expected_turn_id,
            reason,
        } if state.active().is_some() => interrupt(state, expected_turn_id, reason),
        TurnEvent::Interrupt {
            expected_turn_id, ..
        } if state.is_idempotent_late_interrupt(&expected_turn_id) => Ok(Outcome::quiet()),
        TurnEvent::Interrupt { .. } => Err(invalid_state(state)),

        TurnEvent::FailTurn {
            expected_turn_id,
            action_id,
            error,
        } if state.active().is_some() => fail_turn(state, expected_turn_id, action_id, error),
        TurnEvent::FailTurn { .. } => Err(invalid_state(state)),

        TurnEvent::Completion { action_id, result } => {
            finish_pending_completion(state, action_id, result, determinism)
        }
        TurnEvent::Hook { action_id, result } => {
            finish_pending_hook(state, action_id, result, determinism)
        }
        TurnEvent::Tool {
            action_id,
            call_id,
            report,
        } => finish_pending_tool(state, action_id, call_id, report, determinism),
        TurnEvent::Filesystem { action_id, result } => {
            finish_pending_filesystem(state, action_id, result)
        }
    }
}

fn finish_pending_completion(
    state: &mut HarnessState,
    action_id: String,
    result: Result<CompletionResult, ProtocolError>,
    determinism: DeterminismContext,
) -> Result<Outcome, CoreError> {
    enum PendingCompletion {
        Agent,
        Compaction(PendingCompaction),
    }

    let pending = match &state.turn {
        TurnState::Compacting { pending } if pending.action_id() == action_id => {
            PendingCompletion::Compaction(pending.clone())
        }
        TurnState::Active(active) => match &active.phase {
            ActivePhase::AwaitingCompletion {
                action_id: expected,
            } if expected == &action_id => PendingCompletion::Agent,
            ActivePhase::AwaitingCompaction { pending } if pending.action_id() == action_id => {
                PendingCompletion::Compaction(pending.clone())
            }
            ActivePhase::AwaitingToolBatch { .. } | ActivePhase::AwaitingHook { .. } => {
                return Err(invalid_state(state));
            }
            ActivePhase::AwaitingCompletion { .. } | ActivePhase::AwaitingCompaction { .. } => {
                return Err(invalid_state(state));
            }
        },
        TurnState::Idle | TurnState::Terminal { .. } => return Err(invalid_state(state)),
        TurnState::Compacting { .. } => return Err(invalid_state(state)),
    };
    match (pending, result) {
        (PendingCompletion::Agent, result) => {
            finish_agent_completion(state, action_id, result, determinism)
        }
        (PendingCompletion::Compaction(pending), Ok(result)) => {
            let candidate = parse_completion_result(result)?;
            let tools = state.resolved_tools.top_level_tools().to_vec();
            let token_threshold = state.config.settings.context.compaction.token_threshold();
            let resolution = finish_compaction(
                &mut state.context,
                &mut state.last_reported_context_tokens,
                pending,
                candidate,
                &tools,
                CompactionBudget {
                    token_threshold,
                    image_delivery: state.config.settings.context.image_delivery.agent,
                },
            )?;
            apply_compaction_attempt_resolution(state, resolution)
        }
        (PendingCompletion::Compaction(pending), Err(error)) => {
            apply_compaction_resolution(state, fail_compaction(pending, error))
        }
    }
}

fn apply_compaction_attempt_resolution(
    state: &mut HarnessState,
    resolution: CompactionAttemptResolution,
) -> Result<Outcome, CoreError> {
    match resolution {
        CompactionAttemptResolution::Retry(pending) => install_compaction_retry(state, pending),
        CompactionAttemptResolution::Resolve(resolution) => {
            apply_compaction_resolution(state, *resolution)
        }
    }
}

fn apply_compaction_resolution(
    state: &mut HarnessState,
    resolution: CompactionResolution,
) -> Result<Outcome, CoreError> {
    match resolution {
        CompactionResolution::ContinueTurn { outcome } => {
            // Compaction just ran, so resume with an agent completion without
            // checking the automatic threshold again.
            state.mark_model_input_ready()?;
            let action = continue_after_compaction(state)?;
            Ok(outcome.after(outcome_for_action(state, action)?))
        }
        CompactionResolution::ReturnIdle { outcome } => {
            state.turn = TurnState::Idle;
            state.notifications.mark_ready();
            Ok(outcome)
        }
        CompactionResolution::FailTurn {
            mut outcome,
            turn_id,
            error,
        } => {
            outcome.observe(Observation::TurnFailed {
                turn_id,
                error: error.clone(),
            });
            let turn_id = state.finish_turn(TurnOutcome::Failed {
                error: error.clone(),
            })?;
            outcome.complete_turn(turn_id, TurnOutcome::Failed { error });
            Ok(outcome)
        }
    }
}

fn install_compaction_retry(
    state: &mut HarnessState,
    pending: PendingCompaction,
) -> Result<Outcome, CoreError> {
    match &mut state.turn {
        TurnState::Compacting { pending: current } => *current = pending,
        TurnState::Active(active)
            if matches!(active.phase, ActivePhase::AwaitingCompaction { .. }) =>
        {
            active.phase = ActivePhase::AwaitingCompaction { pending };
        }
        TurnState::Idle | TurnState::Active(_) | TurnState::Terminal { .. } => {
            return Err(CoreError::invariant(
                "compaction retry has no pending compaction state",
            ));
        }
    }
    Ok(Outcome::action(pending_single_action(state)?))
}

fn finish_pending_hook(
    state: &mut HarnessState,
    action_id: String,
    result: Result<HookResult, ProtocolError>,
    determinism: DeterminismContext,
) -> Result<Outcome, CoreError> {
    let Some(phase) = state.active().map(|active| active.phase.clone()) else {
        return Err(invalid_state(state));
    };
    match (phase, result) {
        (ActivePhase::AwaitingHook { pending }, Ok(result)) => {
            let resolution = resolve_lifecycle_hook(pending, action_id, result)?;
            apply_lifecycle_hook_resolution(state, resolution, determinism)
        }
        (ActivePhase::AwaitingHook { pending }, Err(error)) => {
            let resolution = resolve_failed_lifecycle_hook(pending, action_id, error)?;
            apply_lifecycle_hook_resolution(state, resolution, determinism)
        }
        (ActivePhase::AwaitingToolBatch { batch }, Ok(result)) => {
            let tools = state.tool_context()?;
            let resolution = finish_tool_batch_hook(tools, batch, &action_id, result, determinism)?;
            apply_tool_batch_transition(state, resolution)
        }
        (ActivePhase::AwaitingToolBatch { batch }, Err(error)) => {
            let tools = state.tool_context()?;
            let resolution = fail_tool_batch_hook(tools, batch, &action_id, error, determinism)?;
            apply_tool_batch_transition(state, resolution)
        }
        (ActivePhase::AwaitingCompletion { .. } | ActivePhase::AwaitingCompaction { .. }, _) => {
            Err(invalid_state(state))
        }
    }
}

fn finish_pending_tool(
    state: &mut HarnessState,
    action_id: String,
    call_id: String,
    report: ReportedToolResult,
    determinism: DeterminismContext,
) -> Result<Outcome, CoreError> {
    if !state.pending_tool_call_matches(&action_id, &call_id) {
        return Err(invalid_state(state));
    }
    let result = match report {
        ReportedToolResult::Succeeded(result @ ToolResult::Success { .. })
        | ReportedToolResult::Failed(result @ ToolResult::Failure { .. }) => result,
        ReportedToolResult::Succeeded(ToolResult::Failure { .. }) => {
            return Err(CoreError::invalid_command(
                "tool_succeeded requires a success result",
            ));
        }
        ReportedToolResult::Failed(ToolResult::Success { .. }) => {
            return Err(CoreError::invalid_command(
                "tool_failed requires a failure result",
            ));
        }
    };
    let Some(ActivePhase::AwaitingToolBatch { batch }) =
        state.active().map(|active| active.phase.clone())
    else {
        return Err(invalid_state(state));
    };
    let tools = state.tool_context()?;
    let resolution = finish_tool_batch_action(tools, batch, &action_id, result, determinism)?;
    apply_tool_batch_transition(state, resolution)
}

fn finish_pending_filesystem(
    state: &mut HarnessState,
    action_id: String,
    result: Result<FilesystemResult, ProtocolError>,
) -> Result<Outcome, CoreError> {
    let Some(ActivePhase::AwaitingToolBatch { batch }) =
        state.active().map(|active| active.phase.clone())
    else {
        return Err(invalid_state(state));
    };
    let tools = state.tool_context()?;
    let resolution = finish_tool_batch_filesystem_write(tools, batch, &action_id, result)?;
    apply_tool_batch_transition(state, resolution)
}

fn invalid_state(state: &HarnessState) -> CoreError {
    CoreError::invalid_state(state.command_state())
}

fn next_completion(state: &HarnessState) -> Result<ActivePhase, CoreError> {
    Ok(active_phase_for_agent_completion(
        plan_next_agent_completion(state)?,
    ))
}

fn model_input_reaches_compaction_threshold(state: &HarnessState) -> Result<bool, CoreError> {
    let Some(token_threshold) = state.config.settings.context.compaction.token_threshold() else {
        return Ok(false);
    };
    // The serialized-bytes estimate under-counts for tokenizers that pack
    // more tokens per byte than the fixed ratio assumes, so trust whichever
    // is larger: the estimate or the provider's last reported context size.
    let estimate = estimate_model_input_tokens(
        state.context.messages(),
        state.resolved_tools.top_level_tools(),
        state.config.settings.context.image_delivery.agent,
    )?;
    let reported = state.last_reported_context_tokens.unwrap_or(0);
    Ok(estimate.max(reported) >= token_threshold)
}

fn start_automatic_compaction(
    state: &mut HarnessState,
    turn_id: &str,
    iterations: u32,
) -> Result<CompactionStartResolution, CoreError> {
    let task_id = state.config.task_id.clone();
    let generated_system = state.generated_system_message();
    let tools = state.resolved_tools.top_level_tools().to_vec();
    let token_threshold = state.config.settings.context.compaction.token_threshold();
    start_compaction(
        &mut state.context,
        &mut state.compaction_count,
        generated_system,
        &task_id,
        CompactionRequest::automatic(turn_id, iterations),
        &tools,
        CompactionBudgets {
            request: CompactionBudget {
                token_threshold,
                image_delivery: state.config.settings.context.image_delivery.compaction,
            },
            replacement: CompactionBudget {
                token_threshold,
                image_delivery: state.config.settings.context.image_delivery.agent,
            },
        },
    )
}

fn plan_next_agent_completion(state: &HarnessState) -> Result<AgentCompletionPlan, CoreError> {
    plan_agent_completion(AgentCompletionRequest::new(
        &state.config.task_id,
        state.context.message_count(),
        state.compaction_count,
        hook_binding_ids(state, HookPoint::PreLlmCall, None),
    ))
}

fn active_phase_for_agent_completion(plan: AgentCompletionPlan) -> ActivePhase {
    match plan {
        AgentCompletionPlan::AwaitCompletion { action_id } => {
            ActivePhase::AwaitingCompletion { action_id }
        }
        AgentCompletionPlan::AwaitPreLlmHook { pending } => {
            ActivePhase::AwaitingHook { pending: *pending }
        }
    }
}

fn pending_single_action(state: &HarnessState) -> Result<Action, CoreError> {
    let mut actions = state.pending_actions();
    if actions.len() != 1 {
        return Err(CoreError::invariant(format!(
            "expected exactly one pending action, found {}",
            actions.len()
        )));
    }
    Ok(actions.pop().expect("pending action count was checked"))
}

fn install_active_phase(state: &mut HarnessState, phase: ActivePhase) -> Result<Action, CoreError> {
    state.require_active_mut()?.phase = phase;
    pending_single_action(state)
}

fn continue_after_compaction(state: &mut HarnessState) -> Result<Action, CoreError> {
    let plan = plan_next_agent_completion(state)?;
    install_active_phase(state, active_phase_for_agent_completion(plan))
}

fn start_manual_compaction(
    state: &mut HarnessState,
    extra_instructions: String,
) -> Result<Outcome, CoreError> {
    let task_id = state.config.task_id.clone();
    let generated_system = state.generated_system_message();
    let tools = state.resolved_tools.top_level_tools().to_vec();
    let token_threshold = state.config.settings.context.compaction.token_threshold();
    match start_compaction(
        &mut state.context,
        &mut state.compaction_count,
        generated_system,
        &task_id,
        CompactionRequest::manual(&extra_instructions),
        &tools,
        CompactionBudgets {
            request: CompactionBudget {
                token_threshold,
                image_delivery: state.config.settings.context.image_delivery.compaction,
            },
            replacement: CompactionBudget {
                token_threshold,
                image_delivery: state.config.settings.context.image_delivery.agent,
            },
        },
    )? {
        CompactionStartResolution::Pending(pending) => {
            state.turn = TurnState::Compacting { pending };
            Ok(Outcome::action(pending_single_action(state)?))
        }
        CompactionStartResolution::Failed(resolution) => {
            apply_compaction_resolution(state, *resolution)
        }
    }
}

/// Installs the next completion on the active turn and yields its action.
fn continue_active_turn(state: &mut HarnessState) -> Result<Action, CoreError> {
    let phase = next_completion(state)?;
    install_active_phase(state, phase)
}

fn finish_agent_completion(
    state: &mut HarnessState,
    action_id: String,
    result: Result<CompletionResult, ProtocolError>,
    determinism: DeterminismContext,
) -> Result<Outcome, CoreError> {
    let event = match result {
        Ok(result) => CompletionEvent::Succeeded { action_id, result },
        Err(error) => CompletionEvent::Failed { action_id, error },
    };
    apply_completion_event(state, event, determinism)
}

fn apply_lifecycle_hook_resolution(
    state: &mut HarnessState,
    resolution: HookResolution,
    determinism: crate::core::step_protocol::DeterminismContext,
) -> Result<Outcome, CoreError> {
    match resolution {
        HookResolution::RestoreTurn { suspended } => {
            restore_suspended_turn(state, suspended);
            Ok(Outcome::quiet())
        }
        HookResolution::CommitPreAgentTurn {
            suspended,
            turn_id,
            message,
        } => {
            restore_suspended_turn(state, suspended);
            commit_turn(state, turn_id, vec![message])
        }
        HookResolution::ResolveCompletion(resolution) => {
            apply_completion_event(state, CompletionEvent::Hook(resolution), determinism)
        }
    }
}

fn apply_completion_event(
    state: &mut HarnessState,
    event: CompletionEvent,
    determinism: DeterminismContext,
) -> Result<Outcome, CoreError> {
    let transition = {
        let active = state.require_active()?;
        let post_llm_hook_binding_ids = hook_binding_ids(state, HookPoint::PostLlmCall, None);
        let post_agent_hook_binding_ids = hook_binding_ids(state, HookPoint::PostAgentTurn, None);
        let tools = crate::core::tools::context::ToolContext::new(
            &state.resolved_tools,
            &state.hook_binding_index,
            &active.turn_id,
            state.context.message_count() + 1,
        );
        resolve_completion(
            CandidateContext::new(
                CandidateTurn::new(active.iterations, state.config.settings.turn.max_iterations),
                CandidateHooks::new(post_llm_hook_binding_ids, post_agent_hook_binding_ids),
                tools,
                determinism,
            ),
            event,
        )?
    };
    apply_completion_transition(state, transition)
}

fn apply_completion_transition(
    state: &mut HarnessState,
    transition: CompletionTransition,
) -> Result<Outcome, CoreError> {
    match transition.resolution {
        CompletionResolution::Wait(wait) => {
            let phase = match *wait {
                CompletionWait::Completion(plan) => active_phase_for_agent_completion(plan),
                CompletionWait::LifecycleHook(pending) => {
                    ActivePhase::AwaitingHook { pending: *pending }
                }
            };
            let action = install_active_phase(state, phase)?;
            outcome_for_action(state, action)
        }
        CompletionResolution::Retry {
            action_id,
            next_iteration,
            feedback,
        } => {
            let mut outcome = Outcome::quiet();
            outcome.observe(Observation::AgentCompletionCandidateDiscarded {
                turn_id: active_turn_id(state)?.to_string(),
                action_id,
                cause: CandidateDiscardCause::Retry,
            });
            state.require_active_mut()?.iterations = next_iteration;
            state.context.push(feedback);
            let action = continue_active_turn(state)?;
            Ok(outcome.after(outcome_for_action(state, action)?))
        }
        CompletionResolution::Finish(finish) => apply_completion_finish(state, finish),
        CompletionResolution::Accept(accepted) => apply_accepted_completion(state, accepted),
    }
}

fn apply_completion_finish(
    state: &mut HarnessState,
    finish: CompletionFinish,
) -> Result<Outcome, CoreError> {
    let mut outcome = Outcome::quiet();
    match finish {
        CompletionFinish::Skipped { action_id } => {
            outcome.observe(Observation::AgentCompletionCandidateDiscarded {
                turn_id: active_turn_id(state)?.to_string(),
                action_id,
                cause: CandidateDiscardCause::Skipped,
            });
            let turn_id = state.finish_turn(TurnOutcome::Completed { output: Vec::new() })?;
            outcome.observe(Observation::TurnCompleted {
                turn_id: turn_id.clone(),
                output: Vec::new(),
                stop_reason: None,
            });
            outcome.complete_turn(turn_id, TurnOutcome::Completed { output: Vec::new() });
        }
        CompletionFinish::Rejected {
            action_id,
            next_iteration,
            reason,
        } => {
            outcome.observe(Observation::AgentCompletionCandidateDiscarded {
                turn_id: active_turn_id(state)?.to_string(),
                action_id,
                cause: CandidateDiscardCause::Rejected,
            });
            state.require_active_mut()?.iterations = next_iteration;
            let turn_id = state.finish_turn(TurnOutcome::Rejected {
                reason: reason.clone(),
            })?;
            outcome.observe(Observation::TurnCompleted {
                turn_id: turn_id.clone(),
                output: Vec::new(),
                stop_reason: None,
            });
            outcome.complete_turn(turn_id, TurnOutcome::Rejected { reason });
        }
        CompletionFinish::Failed { action_id, error } => {
            outcome.observe(Observation::AgentCompletionCandidateDiscarded {
                turn_id: active_turn_id(state)?.to_string(),
                action_id,
                cause: CandidateDiscardCause::Failure {
                    error: error.clone(),
                },
            });
            outcome.observe(Observation::TurnFailed {
                turn_id: active_turn_id(state)?.to_string(),
                error: error.clone(),
            });
            let turn_id = state.finish_turn(TurnOutcome::Failed {
                error: error.clone(),
            })?;
            outcome.complete_turn(turn_id, TurnOutcome::Failed { error });
        }
    }
    Ok(outcome)
}

fn apply_accepted_completion(
    state: &mut HarnessState,
    accepted: AcceptedCompletion,
) -> Result<Outcome, CoreError> {
    let AcceptedCompletion {
        action_id,
        next_iteration,
        context_tokens,
        message,
        observable_candidate,
        continuation,
    } = accepted;
    state.require_active_mut()?.iterations = next_iteration;
    state.context.push(message);
    if let Some(tokens) = context_tokens {
        state.last_reported_context_tokens = Some(tokens);
    }
    let mut outcome = Outcome::quiet();
    if let Some(candidate) = observable_candidate {
        outcome.observe(Observation::AssistantMessageCommitted {
            turn_id: active_turn_id(state)?.to_string(),
            action_id,
            candidate,
        });
    }
    match continuation {
        AcceptedCompletionContinuation::Complete { output } => {
            state.mark_model_input_ready()?;
            if state.has_pending_model_input() {
                let action = continue_active_turn(state)?;
                Ok(outcome.after(outcome_for_action(state, action)?))
            } else {
                Ok(outcome.after(complete_output(state, output)?))
            }
        }
        AcceptedCompletionContinuation::ToolBatch(transition) => {
            Ok(outcome.after(apply_tool_batch_transition(state, *transition)?))
        }
    }
}

fn apply_tool_batch_transition(
    state: &mut HarnessState,
    transition: ToolBatchTransition,
) -> Result<Outcome, CoreError> {
    let ToolBatchTransition {
        resolution,
        observations,
    } = transition;
    let mut outcome = Outcome::quiet();
    for observation in observations {
        outcome.observe(observation);
    }
    let continuation = match resolution {
        ToolBatchResolution::AwaitingActions { batch, actions } => {
            state.require_active_mut()?.phase = ActivePhase::AwaitingToolBatch { batch };
            Outcome::actions(actions)
        }
        ToolBatchResolution::ContinueTurn { messages } => {
            state
                .context
                .extend(messages.into_iter().map(StoredMessage::visible));
            state.mark_model_input_ready()?;
            let max_iterations = state.config.settings.turn.max_iterations;
            let iterations = state.require_active()?.iterations;
            if max_iterations.is_some_and(|max_iterations| iterations >= max_iterations) {
                finish_completed_turn(state, Vec::new(), Some(TurnStopReason::IterationLimit))?
            } else {
                let action = continue_active_turn(state)?;
                outcome_for_action(state, action)?
            }
        }
    };
    Ok(outcome.after(continuation))
}

fn handle_context_message(
    state: &mut HarnessState,
    content: Vec<ContentBlock>,
) -> Result<Outcome, CoreError> {
    if matches!(
        state.turn,
        TurnState::Active(_) | TurnState::Compacting { .. }
    ) {
        return Err(CoreError::invalid_command(
            "context can only be injected while the session is idle",
        ));
    }
    let message = StoredMessage::injected(content_to_user_message(content)?);
    state.ensure_system_prompt();
    state.context.push(message);
    // Injecting context clears the previous turn's result: the session is idle
    // again, not sitting on a finished turn.
    state.turn = TurnState::Idle;
    Ok(Outcome::quiet())
}

fn handle_user_message(
    state: &mut HarnessState,
    turn_id: String,
    content: Vec<ContentBlock>,
    mode: UserMessageMode,
) -> Result<Outcome, CoreError> {
    validate_turn_id(&turn_id, "user message turn_id")?;
    let observed_content = content.clone();
    let message = StoredMessage::visible(content_to_user_message(content)?);
    match mode {
        UserMessageMode::Queue => match &mut state.turn {
            TurnState::Idle | TurnState::Terminal { .. } => {
                start_turn(state, turn_id, vec![message], QueuedTurn::Empty)
            }
            TurnState::Compacting { .. } => Err(CoreError::invalid_command(
                "cannot start a turn while compaction is pending",
            )),
            TurnState::Active(active) => {
                active.queued.enqueue(turn_id, message)?;
                Ok(Outcome::quiet())
            }
        },
        UserMessageMode::Steer => {
            require_active_turn_id(state, &turn_id, "steer")?;
            let mut outcome = Outcome::quiet();
            outcome.observe(Observation::TurnSteeringReceived {
                turn_id: turn_id.clone(),
                content: observed_content,
            });
            state.require_active_mut()?.defer_steer(message)?;
            Ok(outcome)
        }
    }
}

fn content_to_user_message(content: Vec<ContentBlock>) -> Result<Message, CoreError> {
    validate_non_empty_content(&content, "user message content")?;
    Ok(Message::user(content))
}

fn outcome_for_action(state: &mut HarnessState, action: Action) -> Result<Outcome, CoreError> {
    let delivers_model_input = matches!(
        &action,
        Action::Completion {
            kind: crate::core::step_protocol::CompletionActionKind::Agent,
            ..
        }
    );
    let precedes_agent_provider_call = delivers_model_input
        || matches!(
            &action,
            Action::Hook {
                call: HookCall::PreLlmCall,
                ..
            }
        );
    if !precedes_agent_provider_call {
        return Ok(Outcome::action(action));
    }
    let mut outcome = Outcome::quiet();
    if delivers_model_input {
        let (turn_id, ready, steering) = {
            let active = state.require_active_mut()?;
            (
                active.turn_id.clone(),
                active.take_model_input_ready(),
                active.take_pending_steer(),
            )
        };
        if !ready {
            if !steering.is_empty() {
                state.require_active_mut()?.pending_steer = steering;
            }
        } else {
            for message in &steering {
                let Message::User { content } = &message.message else {
                    return Err(CoreError::invariant(
                        "pending steering must contain visible user messages",
                    ));
                };
                outcome.observe(Observation::TurnSteered {
                    turn_id: turn_id.clone(),
                    content: content.clone(),
                });
            }
            state.context.extend(steering);

            let notifications = state.notifications.take_ready();
            if let Some(message) = notification_message(&notifications) {
                state.context.push(message);
            }
            for notification in notifications {
                outcome.observe(Observation::NotificationDelivered {
                    turn_id: turn_id.clone(),
                    notification,
                });
            }
        }
    }
    Ok(outcome.after(preflight_agent_action(state, action)?))
}

fn preflight_agent_action(state: &mut HarnessState, action: Action) -> Result<Outcome, CoreError> {
    if !model_input_reaches_compaction_threshold(state)? {
        return Ok(Outcome::action(action));
    }
    let active = state.require_active()?;
    let turn_id = active.turn_id.clone();
    let iterations = active.iterations;
    match start_automatic_compaction(state, &turn_id, iterations)? {
        CompactionStartResolution::Pending(pending) => {
            let action = install_active_phase(state, ActivePhase::AwaitingCompaction { pending })?;
            Ok(Outcome::action(action))
        }
        CompactionStartResolution::Failed(resolution) => {
            apply_compaction_resolution(state, *resolution)
        }
    }
}

fn start_turn(
    state: &mut HarnessState,
    turn_id: String,
    mut messages: Vec<StoredMessage>,
    queued: QueuedTurn,
) -> Result<Outcome, CoreError> {
    if messages.is_empty() {
        return Err(CoreError::invalid_command(
            "a turn must contain at least one user message",
        ));
    }
    let mut turn_messages = match queued {
        QueuedTurn::Empty => Vec::new(),
        QueuedTurn::Pending {
            turn_id: queued_turn_id,
            messages,
        } => {
            require_turn_id("queued turn", &queued_turn_id, &turn_id)?;
            messages
        }
    };
    turn_messages.append(&mut messages);
    let hook_binding_ids = hook_binding_ids(state, HookPoint::PreAgentTurn, None);
    if !hook_binding_ids.is_empty() {
        let user_content = turn_messages
            .into_iter()
            .flat_map(|stored| match stored.message {
                Message::User { content } => content,
                _ => Vec::new(),
            })
            .collect::<Vec<_>>();
        validate_non_empty_content(&user_content, "pre-agent user content")?;
        // The hook can still skip the turn, so remember where to go back to.
        let suspended = suspend_inactive_turn(&state.turn)?;
        let pending = PendingLifecycleHook::pre_agent_turn(
            turn_id.clone(),
            user_content,
            suspended,
            hook_binding_ids,
        )?;
        state.turn = TurnState::Active(ActiveTurn {
            turn_id: turn_id.clone(),
            iterations: 0,
            phase: ActivePhase::AwaitingHook { pending },
            pending_steer: Vec::new(),
            model_input_ready: false,
            queued: QueuedTurn::Empty,
        });
        return Ok(Outcome::action(pending_single_action(state)?));
    }
    commit_turn(state, turn_id, turn_messages)
}

fn suspend_inactive_turn(turn: &TurnState) -> Result<SuspendedTurn, CoreError> {
    match turn {
        TurnState::Idle => Ok(SuspendedTurn::Idle),
        TurnState::Terminal { turn_id, outcome } => Ok(SuspendedTurn::Terminal {
            turn_id: turn_id.clone(),
            outcome: outcome.clone(),
        }),
        TurnState::Active(_) | TurnState::Compacting { .. } => Err(CoreError::invariant(
            "cannot suspend an active or compacting turn for a pre-agent hook",
        )),
    }
}

fn restore_suspended_turn(state: &mut HarnessState, suspended: SuspendedTurn) {
    state.turn = match suspended {
        SuspendedTurn::Idle => TurnState::Idle,
        SuspendedTurn::Terminal { turn_id, outcome } => TurnState::Terminal { turn_id, outcome },
    };
}

fn commit_turn(
    state: &mut HarnessState,
    turn_id: String,
    turn_messages: Vec<StoredMessage>,
) -> Result<Outcome, CoreError> {
    let content = turn_messages
        .iter()
        .flat_map(|stored| match &stored.message {
            Message::User { content } => content.clone(),
            _ => Vec::new(),
        })
        .collect();
    let mut outcome = Outcome::quiet();
    outcome.observe(Observation::TurnStarted {
        turn_id: turn_id.clone(),
        content,
    });
    state.ensure_system_prompt();
    state.context.extend(turn_messages);
    let phase = next_completion(state)?;
    state.turn = TurnState::Active(ActiveTurn {
        turn_id,
        iterations: 0,
        phase,
        pending_steer: Vec::new(),
        model_input_ready: true,
        queued: QueuedTurn::Empty,
    });
    let action = pending_single_action(state)?;
    outcome.absorb(outcome_for_action(state, action)?);
    Ok(outcome)
}

fn interrupt(
    state: &mut HarnessState,
    expected_turn_id: String,
    reason: Option<String>,
) -> Result<Outcome, CoreError> {
    let mut outcome = Outcome::quiet();
    validate_turn_id(&expected_turn_id, "interrupt expected_turn_id")?;
    require_active_turn_id(state, &expected_turn_id, "interrupt")?;
    let abandoned_action_ids = state.pending_action_ids();
    let interruption_message = reason
        .as_deref()
        .filter(|reason| !reason.trim().is_empty())
        .unwrap_or("Interrupted by the runtime before completion.");
    let resolution = state
        .active()
        .ok_or_else(|| {
            CoreError::invalid_command("cannot interrupt a session without an active turn")
        })?
        .phase
        .interrupt(interruption_message)?;
    match resolution {
        InterruptionResolution::Quiet => {}
        InterruptionResolution::DiscardCompletion { action_id } => {
            outcome.observe(Observation::AgentCompletionCandidateDiscarded {
                turn_id: expected_turn_id.clone(),
                action_id,
                cause: CandidateDiscardCause::Interrupt,
            });
        }
        InterruptionResolution::CommitToolMessages { messages } => {
            state
                .context
                .extend(messages.into_iter().map(StoredMessage::visible));
        }
    }
    let reason = reason.filter(|reason| !reason.trim().is_empty());
    outcome.observe(Observation::TurnInterrupted {
        turn_id: expected_turn_id.clone(),
        reason: reason.clone(),
    });
    let turn_id = state.finish_turn(TurnOutcome::Interrupted {
        reason: reason.clone(),
    })?;
    outcome.complete_turn(turn_id, TurnOutcome::Interrupted { reason });
    for action_id in abandoned_action_ids {
        outcome.observe(Observation::ActionAbandoned {
            turn_id: expected_turn_id.clone(),
            action_id,
            cause: ActionAbandonedCause::Interrupt,
        });
    }
    Ok(outcome)
}

fn fail_turn(
    state: &mut HarnessState,
    expected_turn_id: String,
    action_id: String,
    error: ProtocolError,
) -> Result<Outcome, CoreError> {
    validate_turn_id(&expected_turn_id, "fail_turn expected_turn_id")?;
    require_active_turn_id(state, &expected_turn_id, "fail_turn")?;
    if !state
        .pending_action_ids()
        .iter()
        .any(|pending_action_id| pending_action_id == &action_id)
    {
        return Err(CoreError::invalid_correlation(
            &action_id,
            state.pending_action_ids(),
        ));
    }

    let abandoned_action_ids = state.pending_action_ids();
    let resolution = state
        .active()
        .ok_or_else(|| CoreError::invalid_command("cannot fail a session without an active turn"))?
        .phase
        .interrupt(&error.message)?;
    let mut outcome = Outcome::quiet();
    match resolution {
        InterruptionResolution::Quiet => {}
        InterruptionResolution::DiscardCompletion { action_id } => {
            outcome.observe(Observation::AgentCompletionCandidateDiscarded {
                turn_id: expected_turn_id.clone(),
                action_id,
                cause: CandidateDiscardCause::Failure {
                    error: error.clone(),
                },
            });
        }
        InterruptionResolution::CommitToolMessages { messages } => {
            state
                .context
                .extend(messages.into_iter().map(StoredMessage::visible));
        }
    }
    outcome.observe(Observation::TurnFailed {
        turn_id: expected_turn_id.clone(),
        error: error.clone(),
    });
    let turn_id = state.finish_turn(TurnOutcome::Failed {
        error: error.clone(),
    })?;
    outcome.complete_turn(turn_id, TurnOutcome::Failed { error });
    for action_id in abandoned_action_ids {
        outcome.observe(Observation::ActionAbandoned {
            turn_id: expected_turn_id.clone(),
            action_id,
            cause: ActionAbandonedCause::RuntimeFailure,
        });
    }
    Ok(outcome)
}

fn validate_turn_id(turn_id: &str, label: &str) -> Result<(), CoreError> {
    if turn_id.trim().is_empty() {
        return Err(CoreError::invalid_command(format!(
            "{label} must not be empty"
        )));
    }
    Ok(())
}

fn require_turn_id(label: &str, expected: &str, actual: &str) -> Result<(), CoreError> {
    if expected == actual {
        return Ok(());
    }
    Err(CoreError::invalid_command(format!(
        "{label} mismatch: expected {expected:?}, got {actual:?}"
    )))
}

fn require_active_turn_id(
    state: &HarnessState,
    actual: &str,
    command: &str,
) -> Result<(), CoreError> {
    let expected = state.active_turn_id().ok_or_else(|| {
        CoreError::invalid_command(format!("cannot {command} a session without an active turn"))
    })?;
    require_turn_id(&format!("{command} turn"), expected, actual)
}

fn active_turn_id(state: &HarnessState) -> Result<&str, CoreError> {
    Ok(state.require_active()?.turn_id.as_str())
}

fn complete_output(
    state: &mut HarnessState,
    output: Vec<ContentBlock>,
) -> Result<Outcome, CoreError> {
    let iterations = state.require_active()?.iterations;
    if state.has_pending_model_input()
        && state
            .config
            .settings
            .turn
            .max_iterations
            .is_none_or(|max_iterations| iterations < max_iterations)
    {
        let action = continue_active_turn(state)?;
        return outcome_for_action(state, action);
    }
    finish_completed_turn(state, output, None)
}

fn finish_completed_turn(
    state: &mut HarnessState,
    output: Vec<ContentBlock>,
    stop_reason: Option<TurnStopReason>,
) -> Result<Outcome, CoreError> {
    let queued = state.require_active_mut()?.queued.take();
    let turn_id = state.finish_turn(TurnOutcome::Completed {
        output: output.clone(),
    })?;
    let mut outcome = Outcome::quiet();
    outcome.observe(Observation::TurnCompleted {
        turn_id: turn_id.clone(),
        output: output.clone(),
        stop_reason,
    });
    outcome.complete_turn(turn_id, TurnOutcome::Completed { output });
    let Some((queued_turn_id, queued_messages)) = queued else {
        return Ok(outcome);
    };
    let next = start_turn(state, queued_turn_id, queued_messages, QueuedTurn::Empty)?;
    // `absorb` would let the queued turn's (absent) completion win; this turn
    // did finish and the Runtime needs to hear it.
    let completed = outcome.completed.clone();
    let mut outcome = outcome.after(next);
    outcome.completed = completed;
    Ok(outcome)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::testing::*;
    #[test]
    fn queue_preserves_the_pending_action_and_starts_after_completion() {
        let state = started();
        let pending_action_id = awaiting_action_id(&state);
        let queued = advance(state, user_message("next turn", UserMessageMode::Queue)).unwrap();

        assert_eq!(queued.effect, None);
        assert_eq!(awaiting_action_id(&queued.state), pending_action_id);
        assert_eq!(queued_messages(&queued.state).len(), 1);

        let completed = advance_llm(queued.state, assistant_text("first answer"));

        assert_eq!(
            completed.output(),
            Some(vec![ContentBlock::text("first answer".to_string())])
        );
        assert!(matches!(completed.effect, Some(Action::Completion { .. })));
        assert_eq!(
            message_text(completed.state.context.messages().last().unwrap()),
            "next turn"
        );
        assert!(queued_messages(&completed.state).is_empty());
    }

    /// A queued turn belongs to the turn that was running when it was queued.
    ///
    /// Before `TurnState`, `queued_user_messages` and `queued_turn_id` were
    /// session-level and outlived a failed turn. The stale identity then rejected
    /// the next turn with a queued-turn mismatch, wedging the session until a turn
    /// happened to reuse the old id. Scoping the queue to `ActiveTurn` makes the
    /// discard structural.
    #[test]
    fn a_failed_turn_discards_its_queued_follow_up() {
        let state = started();
        let queued = advance(state, user_message("queued work", UserMessageMode::Queue))
            .unwrap()
            .state;
        assert_eq!(queued_messages(&queued).len(), 1);

        let action_id = awaiting_action_id(&queued);
        let failed = advance(
            queued,
            HarnessCommand::CompletionFailed {
                action_id,
                error: ProtocolError {
                    code: "upstream_unavailable".to_string(),
                    message: "the model provider is unavailable".to_string(),
                    retryable: true,
                    details: Value::Null,
                },
            },
        )
        .unwrap()
        .state;

        assert!(matches!(
            turn_outcome(&failed),
            Some(TurnOutcome::Failed { .. })
        ));
        assert!(queued_messages(&failed).is_empty());

        // A differently identified turn is now accepted rather than rejected
        // against the dead turn's queued identity.
        let next = advance(
            failed,
            user_message_for("turn-2", "a fresh request", UserMessageMode::Queue),
        )
        .unwrap();

        assert!(matches!(next.effect, Some(Action::Completion { .. })));
        assert_eq!(next.state.active_turn_id(), Some("turn-2"));
    }

    #[test]
    fn interrupt_closes_an_open_tool_call() {
        let running = advance_llm(
            started(),
            assistant_tool(
                "run_typescript",
                json!({"code": "async function main() { return tools.file_system.read_file({ path: 'artifact.txt' }); }"}),
            ),
        );

        let interrupted = advance(
            running.state,
            HarnessCommand::Interrupt {
                expected_turn_id: "turn-1".to_string(),
                reason: Some("stop now".to_string()),
            },
        )
        .unwrap();

        assert_eq!(interrupted.effect, None);
        assert!(matches!(
            turn_outcome(&interrupted.state),
            Some(TurnOutcome::Interrupted { .. })
        ));
        assert!(
            interrupted
                .state
                .context
                .messages()
                .last()
                .is_some_and(|message| {
                    message_role(&message.message) == Role::Tool
                        && message_text(message).contains("stop now")
                })
        );
    }
}
