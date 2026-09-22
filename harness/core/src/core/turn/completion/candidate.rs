use crate::core::error::CoreError;
use std::collections::BTreeSet;

use crate::core::features::programmatic_tool_calling::is_private_wrapper;
use crate::core::hooks::{CompletionHookOutput, HookPoint, PreLlmCallOutput};
use crate::core::step_protocol::DeterminismContext;
use crate::core::tools::context::ToolContext;
use crate::core::tools::execution::batch::{ToolBatchTransition, start_tool_batch};
use crate::core::wire::completion::{
    AgentCompletionAcceptance, CompletionCandidate, CompletionFinishReason, CompletionResult,
    CompletionResultPart, CompletionResultSemanticPart, validate_token_usage,
};
use crate::core::wire::content::ContentBlock;
use crate::core::wire::content::content_blocks_to_text;
use crate::core::wire::content::validate_non_empty_content;
use crate::core::wire::message::{
    AssistantPart, AssistantRole, AssistantSemanticPart, CandidateMessage, Message, StoredMessage,
    ToolArguments,
};
use crate::core::wire::tool::ProtocolError;

use super::{AgentCompletionPlan, agent_completion_after_pre_llm_hook};
use crate::core::turn::lifecycle_hooks::{
    CompletionHookResolution, CompletionHookStage, PendingLifecycleHook,
};

pub(in crate::core::turn) struct CandidateContext<'a> {
    turn: CandidateTurn,
    hooks: CandidateHooks,
    tools: ToolContext<'a>,
    determinism: DeterminismContext,
}

impl<'a> CandidateContext<'a> {
    pub(in crate::core::turn) fn new(
        turn: CandidateTurn,
        hooks: CandidateHooks,
        tools: ToolContext<'a>,
        determinism: DeterminismContext,
    ) -> Self {
        Self {
            turn,
            hooks,
            tools,
            determinism,
        }
    }
}

pub(in crate::core::turn) struct CandidateTurn {
    iterations: u32,
    max_iterations: Option<u32>,
}

impl CandidateTurn {
    pub(in crate::core::turn) fn new(iterations: u32, max_iterations: Option<u32>) -> Self {
        Self {
            iterations,
            max_iterations,
        }
    }
}

pub(in crate::core::turn) struct CandidateHooks {
    post_llm_binding_ids: Vec<String>,
    post_agent_binding_ids: Vec<String>,
}

impl CandidateHooks {
    pub(in crate::core::turn) fn new(
        post_llm_binding_ids: Vec<String>,
        post_agent_binding_ids: Vec<String>,
    ) -> Self {
        Self {
            post_llm_binding_ids,
            post_agent_binding_ids,
        }
    }
}

pub(in crate::core::turn) enum CompletionEvent {
    Succeeded {
        action_id: String,
        result: CompletionResult,
    },
    Failed {
        action_id: String,
        error: ProtocolError,
    },
    Hook(CompletionHookResolution),
}

pub(in crate::core::turn) struct CompletionTransition {
    pub(in crate::core::turn) resolution: CompletionResolution,
}

pub(in crate::core::turn) enum CompletionResolution {
    Wait(Box<CompletionWait>),
    Retry {
        action_id: String,
        next_iteration: u32,
        feedback: StoredMessage,
    },
    Finish(CompletionFinish),
    Accept(AcceptedCompletion),
}

pub(in crate::core::turn) enum CompletionWait {
    Completion(AgentCompletionPlan),
    LifecycleHook(Box<PendingLifecycleHook>),
}

pub(in crate::core::turn) enum CompletionFinish {
    Skipped {
        action_id: String,
    },
    Rejected {
        action_id: String,
        next_iteration: u32,
        reason: String,
    },
    Failed {
        action_id: String,
        error: ProtocolError,
    },
}

pub(in crate::core::turn) struct AcceptedCompletion {
    pub(in crate::core::turn) action_id: String,
    pub(in crate::core::turn) next_iteration: u32,
    /// Provider-reported context size (total tokens) for this completion,
    /// when the provider reported usage. Feeds the automatic compaction
    /// trigger so it reacts to the real context size, not only the estimate.
    pub(in crate::core::turn) context_tokens: Option<u64>,
    pub(in crate::core::turn) message: StoredMessage,
    pub(in crate::core::turn) observable_candidate: Option<CompletionCandidate>,
    pub(in crate::core::turn) continuation: AcceptedCompletionContinuation,
}

pub(in crate::core::turn) enum AcceptedCompletionContinuation {
    Complete { output: Vec<ContentBlock> },
    ToolBatch(Box<ToolBatchTransition>),
}

pub(in crate::core::turn) fn resolve_completion(
    context: CandidateContext<'_>,
    event: CompletionEvent,
) -> Result<CompletionTransition, CoreError> {
    match event {
        CompletionEvent::Succeeded { action_id, result } => {
            let candidate = parse_completion_result(result)?;
            let resolution = if context.hooks.post_llm_binding_ids.is_empty() {
                resolve_after_post_llm(&context, action_id, candidate)?
            } else {
                wait_for_hook(
                    action_id,
                    candidate,
                    HookPoint::PostLlmCall,
                    context.hooks.post_llm_binding_ids.clone(),
                )?
            };
            Ok(CompletionTransition { resolution })
        }
        CompletionEvent::Failed { action_id, error } => Ok(CompletionTransition {
            resolution: CompletionResolution::Finish(CompletionFinish::Failed { action_id, error }),
        }),
        CompletionEvent::Hook(resolution) => Ok(CompletionTransition {
            resolution: resolve_completion_hook(&context, resolution)?,
        }),
    }
}

fn resolve_completion_hook(
    context: &CandidateContext<'_>,
    resolution: CompletionHookResolution,
) -> Result<CompletionResolution, CoreError> {
    match resolution {
        CompletionHookResolution::PreLlm {
            completion_action_id,
            output,
        } => match output {
            PreLlmCallOutput::Continue => Ok(CompletionResolution::Wait(Box::new(
                CompletionWait::Completion(agent_completion_after_pre_llm_hook(
                    completion_action_id,
                )),
            ))),
            PreLlmCallOutput::Skip { .. } => {
                Ok(CompletionResolution::Finish(CompletionFinish::Skipped {
                    action_id: completion_action_id,
                }))
            }
        },
        CompletionHookResolution::Candidate {
            stage,
            completion_action_id,
            mut candidate,
            output,
        } => match output {
            CompletionHookOutput::Accept { acceptance } => {
                apply_acceptance(&mut candidate, acceptance)?;
                match stage {
                    CompletionHookStage::PostLlm => {
                        resolve_after_post_llm(context, completion_action_id, candidate)
                    }
                    CompletionHookStage::PostAgent => {
                        resolve_accepted(context, completion_action_id, candidate)
                    }
                }
            }
            CompletionHookOutput::Retry { feedback } => {
                resolve_retry(context, completion_action_id, feedback)
            }
            CompletionHookOutput::Reject { reason } => {
                resolve_rejection(context, completion_action_id, reason)
            }
        },
        CompletionHookResolution::Failed {
            completion_action_id,
            error,
        } => Ok(CompletionResolution::Finish(CompletionFinish::Failed {
            action_id: completion_action_id,
            error,
        })),
    }
}

fn resolve_after_post_llm(
    context: &CandidateContext<'_>,
    action_id: String,
    candidate: CompletionCandidate,
) -> Result<CompletionResolution, CoreError> {
    if candidate.message.tool_calls().is_empty() && !context.hooks.post_agent_binding_ids.is_empty()
    {
        return wait_for_hook(
            action_id,
            candidate,
            HookPoint::PostAgentTurn,
            context.hooks.post_agent_binding_ids.clone(),
        );
    }
    resolve_accepted(context, action_id, candidate)
}

fn wait_for_hook(
    action_id: String,
    candidate: CompletionCandidate,
    point: HookPoint,
    hook_binding_ids: Vec<String>,
) -> Result<CompletionResolution, CoreError> {
    Ok(CompletionResolution::Wait(Box::new(
        CompletionWait::LifecycleHook(Box::new(PendingLifecycleHook::completion_candidate(
            action_id,
            candidate,
            hook_binding_ids,
            point,
        )?)),
    )))
}

fn resolve_retry(
    context: &CandidateContext<'_>,
    action_id: String,
    feedback: Vec<ContentBlock>,
) -> Result<CompletionResolution, CoreError> {
    validate_non_empty_content(&feedback, "user message content")?;
    let feedback = StoredMessage::injected(Message::user(feedback));
    let next_iteration = context.turn.iterations + 1;
    if context
        .turn
        .max_iterations
        .is_some_and(|max_iterations| next_iteration >= max_iterations)
    {
        return Err(CoreError::invalid_command(
            "completion retry would exceed the maximum completion iterations",
        ));
    }
    Ok(CompletionResolution::Retry {
        action_id,
        next_iteration,
        feedback,
    })
}

fn resolve_rejection(
    context: &CandidateContext<'_>,
    action_id: String,
    reason: Vec<ContentBlock>,
) -> Result<CompletionResolution, CoreError> {
    let reason = content_blocks_to_text(&reason, "completion rejection reason")?;
    Ok(CompletionResolution::Finish(CompletionFinish::Rejected {
        action_id,
        next_iteration: context.turn.iterations + 1,
        reason,
    }))
}

fn resolve_accepted(
    context: &CandidateContext<'_>,
    action_id: String,
    candidate: CompletionCandidate,
) -> Result<CompletionResolution, CoreError> {
    let next_iteration = context.turn.iterations + 1;
    if context
        .turn
        .max_iterations
        .is_some_and(|max_iterations| next_iteration > max_iterations)
    {
        return Err(CoreError::invalid_command(
            "maximum completion iterations exceeded",
        ));
    }
    let context_tokens = candidate.usage.as_ref().map(|usage| usage.total_tokens);
    let message = Message::assistant(candidate.message.content.clone());
    message.validate()?;
    let tool_calls = candidate.message.tool_calls();
    let observable_candidate = observable_candidate(&candidate);
    // The final iteration's tool call runs like any other. The turn stops after
    // the tool result lands, when the loop would otherwise start a completion it
    // has no budget for (see `apply_tool_batch_transition`), so the pending tool
    // is never stranded and the user is never left waiting on a silent turn.
    let continuation = if tool_calls.is_empty() {
        AcceptedCompletionContinuation::Complete {
            output: candidate.message.content_blocks(),
        }
    } else {
        AcceptedCompletionContinuation::ToolBatch(Box::new(start_tool_batch(
            context.tools,
            tool_calls,
            context.determinism,
        )?))
    };
    Ok(CompletionResolution::Accept(AcceptedCompletion {
        action_id,
        next_iteration,
        context_tokens,
        message: StoredMessage::visible(message),
        observable_candidate,
        continuation,
    }))
}

fn apply_acceptance(
    candidate: &mut CompletionCandidate,
    acceptance: AgentCompletionAcceptance,
) -> Result<(), CoreError> {
    if let AgentCompletionAcceptance::ReplaceAssistantContent { content } = acceptance {
        validate_non_empty_content(&content, "replacement assistant content")?;
        candidate.message.replace_content(content);
    }
    Ok(())
}

pub(in crate::core::turn) fn parse_completion_result(
    result: CompletionResult,
) -> Result<CompletionCandidate, CoreError> {
    validate_token_usage(result.usage.as_ref()).map_err(CoreError::invalid_command)?;
    if result.parts.is_empty() {
        return Err(CoreError::invalid_command(
            "completion result must contain at least one part",
        ));
    }

    let mut tool_call_ids = BTreeSet::new();
    let mut parts = Vec::with_capacity(result.parts.len());
    for part in result.parts {
        let part = match part {
            CompletionResultPart::Content(block) => AssistantPart::Content(block),
            CompletionResultPart::Semantic(CompletionResultSemanticPart::Reasoning {
                content,
                meta,
            }) => {
                if content.is_empty() {
                    return Err(CoreError::invalid_command(
                        "completion reasoning must contain at least one item",
                    ));
                }
                AssistantPart::Semantic(AssistantSemanticPart::Reasoning { content, meta })
            }
            CompletionResultPart::Semantic(CompletionResultSemanticPart::ToolCall {
                id,
                name,
                arguments_json,
                meta,
            }) => {
                if id.trim().is_empty() || name.trim().is_empty() {
                    return Err(CoreError::invalid_command(
                        "tool call IDs and names must not be empty",
                    ));
                }
                if !tool_call_ids.insert(id.clone()) {
                    return Err(CoreError::invalid_command(format!(
                        "duplicate tool call ID {id:?}"
                    )));
                }
                let arguments = match serde_json::from_str(&arguments_json) {
                    Ok(value) => ToolArguments::Json {
                        raw: arguments_json,
                        value,
                    },
                    Err(error) => ToolArguments::InvalidJson {
                        raw: arguments_json,
                        error: error.to_string(),
                    },
                };
                AssistantPart::Semantic(AssistantSemanticPart::ToolCall {
                    id,
                    name,
                    arguments,
                    meta,
                })
            }
        };
        parts.push(part);
    }

    let message = CandidateMessage {
        role: AssistantRole::Assistant,
        content: parts,
    };
    Message::assistant(message.content.clone()).validate()?;
    let has_tool_call = !message.tool_calls().is_empty();
    match result.finish_reason {
        CompletionFinishReason::ToolCall if !has_tool_call => {
            return Err(CoreError::invalid_command(
                "tool_call finish requires at least one tool call",
            ));
        }
        CompletionFinishReason::ToolCall => {}
        _ if has_tool_call => {
            return Err(CoreError::invalid_command(
                "only tool_call finish may contain a tool call",
            ));
        }
        _ => {}
    }

    Ok(CompletionCandidate {
        message,
        finish_reason: result.finish_reason,
        usage: result.usage,
    })
}

fn observable_candidate(candidate: &CompletionCandidate) -> Option<CompletionCandidate> {
    let mut observable = candidate.clone();
    observable.message.content.retain(|part| {
        !matches!(
            part,
            AssistantPart::Semantic(AssistantSemanticPart::ToolCall { name, .. })
                if is_private_wrapper(name)
        )
    });
    (!observable.message.content.is_empty()).then_some(observable)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::testing::*;

    fn text_result(text: &str) -> CompletionResult {
        CompletionResult {
            parts: vec![CompletionResultPart::Content(ContentBlock::text(text))],
            finish_reason: CompletionFinishReason::Stop,
            usage: None,
        }
    }

    #[test]
    fn completion_retry_appends_feedback_without_storing_the_rejected_draft() {
        let step = advance_llm_retry(started(), "add evidence").unwrap();

        let Some(Action::Completion {
            iteration,
            model_input:
                ModelInputUpdate {
                    messages: ModelMessageUpdate::Replace { messages, .. },
                    ..
                },
            ..
        }) = step.effect
        else {
            panic!("expected a completion effect");
        };
        assert_eq!(iteration, 1);
        assert_eq!(iterations(&step.state), 1);
        assert_eq!(
            messages,
            step.state
                .context
                .messages()
                .iter()
                .map(|stored| stored.message.clone())
                .collect::<Vec<_>>()
        );
        assert_eq!(message_role(messages.last().unwrap()), Role::User);
        assert_eq!(message_text(messages.last().unwrap()), "add evidence");
        assert!(
            !messages
                .iter()
                .any(|message| message_text(message) == "rejected draft")
        );
    }

    #[test]
    fn completion_rejection_stores_only_the_rejection_state() {
        let mut state = started();
        add_always_hook(&mut state.config, HookPoint::PostLlmCall);
        rebuild_hook_binding_index(&mut state);
        let messages = state.context.messages().to_vec();
        let action_id = awaiting_action_id(&state);
        let state = advance(
            state,
            HarnessCommand::CompletionSucceeded {
                action_id: action_id.clone(),
                result: text_result("draft"),
            },
        )
        .unwrap()
        .state;
        let hook_action_id = awaiting_action_id(&state);
        let step = advance(
            state,
            HarnessCommand::HookCompleted {
                action_id: hook_action_id,
                result: HookResult::PostLlmCall(CompletionHookOutput::Reject {
                    reason: vec![ContentBlock::text("unsafe")],
                }),
            },
        )
        .unwrap();

        assert_eq!(step.effect, None);
        assert_eq!(step.effects, Vec::new());
        // The rejected draft is not committed: context is untouched, and the turn
        // ends carrying only why it declined.
        assert_eq!(step.state.context.messages(), messages);
        assert_eq!(step.output(), Some(Vec::new()));
        assert_eq!(
            turn_outcome(&step.state),
            Some(&TurnOutcome::Rejected {
                reason: "unsafe".to_string()
            })
        );
    }

    #[test]
    fn completion_retry_rejects_the_final_iteration_transactionally() {
        let mut config = config();
        config.settings.turn.max_iterations = Some(1);
        let mut state = started_with(config);
        add_always_hook(&mut state.config, HookPoint::PostLlmCall);
        rebuild_hook_binding_index(&mut state);
        let action_id = awaiting_action_id(&state);
        state = advance(
            state,
            HarnessCommand::CompletionSucceeded {
                action_id: action_id.clone(),
                result: text_result("draft"),
            },
        )
        .unwrap()
        .state;
        let before = state.clone();
        let hook_action_id = awaiting_action_id(&state);
        let error = advance_in_place(
            &mut state,
            HarnessCommand::HookCompleted {
                action_id: hook_action_id,
                result: HookResult::PostLlmCall(CompletionHookOutput::Retry {
                    feedback: vec![ContentBlock::text("try again".to_string())],
                }),
            },
            test_determinism(),
        )
        .unwrap_err();

        assert_eq!(
            error.detail(),
            "completion retry would exceed the maximum completion iterations"
        );
        assert_eq!(state, before);
    }

    #[test]
    fn final_iteration_tool_call_dispatches_the_tool() {
        let mut config = config();
        config.settings.turn.max_iterations = Some(1);
        let state = advance(
            initial_state(config).unwrap(),
            user_message("work", UserMessageMode::Queue),
        )
        .unwrap()
        .state;

        let step = advance_llm(
            state,
            assistant_tool("read_file", json!({"path": "notes.txt"})),
        );

        // The final iteration's tool call runs like any other rather than being
        // rejected: the turn is still active with a tool Action to dispatch.
        assert!(step.effect.is_some());
        assert_eq!(turn_outcome(&step.state), None);
        assert_eq!(iterations(&step.state), 1);
        assert_eq!(
            message_role(&step.state.context.messages().last().unwrap().message),
            Role::Assistant
        );
    }

    #[test]
    fn exceeded_iteration_limit_completes_with_rejection() {
        let mut config = config();
        config.settings.turn.max_iterations = Some(1);
        let mut state = started_with(config);
        state.require_active_mut().unwrap().iterations = 1;

        let before = state.clone();
        let action_id = awaiting_action_id(&state);
        let error = advance(
            state,
            HarnessCommand::CompletionSucceeded {
                action_id,
                result: text_result("too late"),
            },
        )
        .unwrap_err();

        assert_eq!(error.detail(), "maximum completion iterations exceeded");
        assert_eq!(iterations(&before), 1);
    }
}
