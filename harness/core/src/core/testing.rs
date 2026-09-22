//! Shared builders for Core tests. Every helper drives the shipped
//! `advance_in_place` path so tests exercise production code, not a parallel
//! test-only entry point.
//!
//! Each module's `#[cfg(test)] mod tests` pulls this in as a prelude alongside
//! `super::*`, so a test module needs two imports: its own module and this one.
//! This is the only place test-only types and helpers live; production modules
//! carry no `#[cfg(test)]` items of their own.

#![allow(unused_imports)]

pub(crate) use serde_json::Value;
pub(crate) use serde_json::json;

pub(crate) use crate::core::action_id::{
    compaction as compaction_action_id, completion as completion_action_id,
    hook as derived_hook_action_id,
};
pub(crate) use crate::core::capabilities::{HookBinding, HookSelector};
pub(crate) use crate::core::config::{
    ContextSettings, HarnessConfig, HarnessConfigUpdate, HarnessSettings, ImageDeliveryMode,
    ImageDeliverySettings, ProvidedToolDefinition, ProvidedToolExposure, ToolGroupDefinition,
    ToolGroupMetadata, ToolSettings, TurnSettings,
};
pub(crate) use crate::core::error::CoreError;
pub(crate) use crate::core::features::background_processes::Mode as BackgroundProcessMode;
pub(crate) use crate::core::features::compaction::{
    CompactionBudget, CompactionPolicy, CompactionTrigger,
};
pub(crate) use crate::core::features::large_output::Policy as LargeOutputPolicy;
pub(crate) use crate::core::features::notifications::{
    AsyncToolNotificationStatus, Notification, NotificationLevel, NotificationSource,
    NotificationState, ProcessTerminalStatus, SubagentNotificationStatus,
};
pub(crate) use crate::core::features::programmatic_tool_calling::ProgramExecution;
pub(crate) use crate::core::features::programmatic_tool_calling::Settings as ProgrammaticToolSettings;
pub(crate) use crate::core::features::skills::SkillDefinition;
pub(crate) use crate::core::features::subagents::Mode as SubagentMode;
pub(crate) use crate::core::hooks::{
    CompletionHookOutput, HookCall, HookPoint, HookResult, PreAgentTurnOutput, PreToolCallOutput,
};
pub(crate) use crate::core::hooks::{HookToolKey, HookToolTarget};
pub(crate) use crate::core::model_context::ModelContext;
pub(crate) use crate::core::state::{
    ActivePhase, ActiveTurn, HarnessState, QueuedTurn, StateInspection, TurnState,
};
use crate::core::step_protocol::DeterminismContext;
pub(crate) use crate::core::step_protocol::{
    AcceptedToolResult, ActionAbandonedCause, CandidateDiscardCause, Observation, Outcome,
    Transition, TurnCompletion,
};
pub(crate) use crate::core::step_protocol::{
    Action, CompletionActionKind, CompletionPurpose, ModelInputUpdate, ModelMessageUpdate,
    ModelToolCatalogUpdate, ToolDefinition,
};
pub(crate) use crate::core::step_protocol::{
    CommandState, HarnessCommand, HarnessConfigurationChange, PendingActionInspection,
    PendingCompletionPurpose, SessionStatus, Turn,
};
pub(crate) use crate::core::tools::command_environment::CommandEnvironment;
pub(crate) use crate::core::tools::context::ToolContext;
pub(crate) use crate::core::tools::execution::{ToolBatch, ToolExecution, ToolExecutionState};
pub(crate) use crate::core::tools::external::{
    ExternalTool, ExternalToolCall, HookToolCall, ProvidedToolCall, RuntimeBuiltinToolCall,
    RuntimeBuiltinToolName, ToolOrigin, ToolTarget,
};
use crate::core::tools::resolved;
pub(crate) use crate::core::turn::PendingLifecycleHook;
pub(crate) use crate::core::turn::TurnOutcome;
pub(crate) use crate::core::wire::completion::{
    AgentCompletionAcceptance, CompletionCandidate, CompletionFinishReason, CompletionResult,
    CompletionResultPart, CompletionResultSemanticPart, TokenUsage,
};
pub(crate) use crate::core::wire::content::ContentBlock;
pub(crate) use crate::core::wire::content::content_blocks_to_text;
pub(crate) use crate::core::wire::content::text_content;
pub(crate) use crate::core::wire::message::{
    AssistantPart, AssistantRole, AssistantSemanticPart, CandidateMessage, Message,
    ReasoningContent, StoredMessage, StoredMessageSource, ToolArguments, ToolOutcome,
};
pub(crate) use crate::core::wire::tool::{ProtocolError, StructuredContent, ToolCall, ToolResult};
pub(crate) use crate::core::wire::user_message::UserMessageMode;
pub(crate) use crate::core::{advance_in_place, initial_state, reconfigure_in_place};

/// Owned equivalent of one `advance_in_place` call. Production code mutates
/// state in place; tests read a whole state value afterwards, so this bundles
/// the two.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct Step {
    pub state: HarnessState,
    pub completed: Option<TurnCompletion>,
    pub effect: Option<Action>,
    pub effects: Vec<Action>,
    pub accepted_tool_result: Option<AcceptedToolResult>,
}

pub(crate) fn test_determinism() -> DeterminismContext {
    DeterminismContext {
        time_unix_ms: 1_700_000_000_000,
        random_seed: 0x1234_5678,
    }
}

/// Drives the shipped `advance_in_place` under a fixed determinism context and
/// resolves the completion context the way `HarnessSession` would, so tests can
/// assert against a whole state value without a parallel Core entry point.
pub(crate) fn advance(mut state: HarnessState, command: HarnessCommand) -> Result<Step, CoreError> {
    let mut transition = advance_in_place(&mut state, command, test_determinism())?;
    for effect in &mut transition.effects {
        if let Action::Completion { model_input, .. } = effect {
            model_input.messages = ModelMessageUpdate::Replace {
                revision: 0,
                messages: state
                    .context
                    .messages()
                    .iter()
                    .map(|stored| stored.message.clone())
                    .collect(),
            };
            model_input.tool_catalog = ModelToolCatalogUpdate::Replace {
                revision: 0,
                tools: state.resolved_tools.top_level_tools().to_vec(),
            };
        }
    }
    let accepted_tool_result =
        transition
            .observations
            .iter()
            .find_map(|observation| match observation {
                Observation::ToolResultCommitted {
                    turn_id,
                    action_id,
                    call_id,
                    result,
                } => Some(AcceptedToolResult {
                    turn_id: turn_id.clone(),
                    action_id: action_id.clone(),
                    operation_id: call_id.clone(),
                    result: result.clone(),
                }),
                _ => None,
            });
    let effects = transition.effects;
    let effect = effects.first().cloned();
    Ok(Step {
        completed: transition.completed,
        state,
        effect,
        effects,
        accepted_tool_result,
    })
}

impl Step {
    /// The output the finished turn produced, mirroring what the Runtime would
    /// report as a completed turn.
    pub(crate) fn output(&self) -> Option<Vec<ContentBlock>> {
        match &self.completed.as_ref()?.outcome {
            TurnOutcome::Completed { output } => Some(output.clone()),
            TurnOutcome::Rejected { .. } => Some(Vec::new()),
            TurnOutcome::Failed { .. } | TurnOutcome::Interrupted { .. } => None,
        }
    }

    /// Why the turn produced no answer, for the paths that record a reason.
    pub(crate) fn rejection_reason(&self) -> Option<&str> {
        match &self.completed.as_ref()?.outcome {
            TurnOutcome::Rejected { reason } => Some(reason),
            TurnOutcome::Interrupted { reason } => reason.as_deref(),
            TurnOutcome::Completed { .. } | TurnOutcome::Failed { .. } => None,
        }
    }
}

/// The turn's terminal outcome, or `None` while it is still running.
pub(crate) fn turn_outcome(state: &HarnessState) -> Option<&TurnOutcome> {
    match &state.turn {
        TurnState::Terminal { outcome, .. } => Some(outcome),
        TurnState::Idle | TurnState::Compacting { .. } | TurnState::Active(_) => None,
    }
}

/// How many completions the active turn has committed.
pub(crate) fn iterations(state: &HarnessState) -> u32 {
    state.active().map_or(0, |active| active.iterations)
}

pub(crate) fn queued_messages(state: &HarnessState) -> &[StoredMessage] {
    match state.active().map(|active| &active.queued) {
        Some(QueuedTurn::Pending { messages, .. }) => messages,
        Some(QueuedTurn::Empty) | None => &[],
    }
}

pub(crate) fn reconfigure(
    mut state: HarnessState,
    update: HarnessConfigUpdate,
) -> Result<HarnessState, CoreError> {
    reconfigure_in_place(&mut state, update)?;
    Ok(state)
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) enum Role {
    System,
    User,
    Assistant,
    Tool,
}

/// Lets assertions read a `Message` out of either a bare message or a stored
/// one, so the production types do not carry a test-only `AsRef` impl.
pub(crate) trait AsMessage {
    fn as_message(&self) -> &Message;
}

impl AsMessage for Message {
    fn as_message(&self) -> &Message {
        self
    }
}

impl AsMessage for StoredMessage {
    fn as_message(&self) -> &Message {
        &self.message
    }
}

pub(crate) fn assistant_text(content: impl Into<String>) -> Message {
    Message::assistant(
        text_content(content)
            .into_iter()
            .map(AssistantPart::Content)
            .collect(),
    )
}

pub(crate) fn message_role(message: &Message) -> Role {
    match message {
        Message::System { .. } => Role::System,
        Message::User { .. } => Role::User,
        Message::Assistant { .. } => Role::Assistant,
        Message::Tool { .. } => Role::Tool,
    }
}

pub(crate) fn message_content(message: &Message) -> Vec<ContentBlock> {
    match message {
        Message::System { content } | Message::User { content } | Message::Tool { content, .. } => {
            content.clone()
        }
        Message::Assistant { content } => content
            .iter()
            .filter_map(|part| match part {
                AssistantPart::Content(block) => Some(block.clone()),
                AssistantPart::Semantic(_) => None,
            })
            .collect(),
    }
}

pub(crate) fn config() -> HarnessConfig {
    HarnessConfig {
        task_id: "task".to_string(),
        system_instructions: "system".to_string(),
        settings: HarnessSettings {
            turn: TurnSettings {
                max_iterations: Some(8),
            },
            context: ContextSettings {
                compaction: CompactionPolicy::Disabled,
                image_delivery: ImageDeliverySettings::default(),
            },
            tools: ToolSettings {
                programmatic: ProgrammaticToolSettings {
                    max_effects: 128,
                    max_operations: 1024,
                },
                large_output: crate::core::features::large_output::Policy::Disabled,
                subagents: SubagentMode::Enabled,
                background_processes: BackgroundProcessMode::Disabled,
                command_environment: CommandEnvironment::Unix,
            },
        },
        capabilities: Default::default(),
        skill_catalog_fingerprint: None,
        plugins: Vec::new(),
    }
}

pub(crate) fn add_always_hook(config: &mut HarnessConfig, point: HookPoint) {
    let order = config
        .capability_sets()
        .flat_map(|capabilities| capabilities.hook_bindings.iter())
        .count() as u32;
    config.capabilities.hook_bindings.push(HookBinding {
        id: format!("test-hook-{order}-{point:?}"),
        point,
        order,
        selector: HookSelector::Always,
    });
}

pub(crate) fn add_tool_hook(
    config: &mut HarnessConfig,
    id: &str,
    point: HookPoint,
    order: u32,
    target: HookToolTarget,
    qualified_name: &str,
) {
    config.capabilities.hook_bindings.push(HookBinding {
        id: id.to_string(),
        point,
        order,
        selector: HookSelector::ToolKeys {
            tool_keys: vec![HookToolKey {
                target,
                qualified_name: qualified_name.to_string(),
            }],
        },
    });
}

pub(crate) fn rebuild_hook_binding_index(state: &mut HarnessState) {
    state.hook_binding_index =
        crate::core::capabilities::HookBindingIndex::from_config(&state.config);
}

pub(crate) fn assistant_tool(name: &str, arguments: Value) -> Message {
    assistant_tools(vec![("call-1", name, arguments)])
}

pub(crate) fn assistant_tools(calls: Vec<(&str, &str, Value)>) -> Message {
    Message::assistant(
        calls
            .into_iter()
            .map(|(id, name, value)| {
                let raw = serde_json::to_string(&value).unwrap();
                AssistantPart::Semantic(AssistantSemanticPart::ToolCall {
                    id: id.to_string(),
                    name: name.to_string(),
                    arguments: ToolArguments::Json { raw, value },
                    meta: None,
                })
            })
            .collect(),
    )
}

pub(crate) fn user_message(text: &str, mode: UserMessageMode) -> HarnessCommand {
    user_message_for("turn-1", text, mode)
}

pub(crate) fn user_message_for(turn_id: &str, text: &str, mode: UserMessageMode) -> HarnessCommand {
    HarnessCommand::UserMessage {
        turn_id: turn_id.to_string(),
        content: vec![ContentBlock::text(text)],
        mode,
    }
}

pub(crate) fn awaiting_action_id(state: &HarnessState) -> String {
    let Some(active) = state.active() else {
        panic!("expected a pending action, got {:?}", state.turn)
    };
    match &active.phase {
        ActivePhase::AwaitingCompletion { action_id } => action_id.clone(),
        ActivePhase::AwaitingCompaction { pending } => pending.action_id().to_string(),
        ActivePhase::AwaitingHook { pending } => pending.action_id().to_string(),
        ActivePhase::AwaitingToolBatch { batch, .. } => batch
            .executions
            .iter()
            .find_map(|execution| match &execution.state {
                ToolExecutionState::DirectAwaitingPreHook { hook_action_id, .. }
                | ToolExecutionState::DirectAwaitingPostHook { hook_action_id, .. } => {
                    Some(hook_action_id.clone())
                }
                ToolExecutionState::DirectPending { call } => Some(call.action_id.clone()),
                ToolExecutionState::ProgramPending { execution } => execution
                    .pending_actions(&active.turn_id)
                    .first()
                    .map(|action| action.action_id().to_string()),
                ToolExecutionState::AwaitingLargeOutputWrite { action_id, .. } => {
                    Some(action_id.clone())
                }
                ToolExecutionState::Completed { .. } => None,
            })
            .unwrap_or_else(|| panic!("expected a pending action in {batch:?}")),
    }
}

pub(crate) fn advance_llm(state: HarnessState, message: Message) -> Step {
    let action_id = awaiting_action_id(&state);
    let Message::Assistant { content } = message else {
        panic!("advance_llm requires an assistant message")
    };
    let has_tool_call = content.iter().any(|part| {
        matches!(
            part,
            AssistantPart::Semantic(AssistantSemanticPart::ToolCall { .. })
        )
    });
    let parts = content
        .into_iter()
        .map(|part| match part {
            AssistantPart::Content(block) => CompletionResultPart::Content(block),
            AssistantPart::Semantic(AssistantSemanticPart::Reasoning { content, meta }) => {
                CompletionResultPart::Semantic(CompletionResultSemanticPart::Reasoning {
                    content,
                    meta,
                })
            }
            AssistantPart::Semantic(AssistantSemanticPart::ToolCall {
                id,
                name,
                arguments,
                meta,
            }) => {
                let arguments_json = match arguments {
                    ToolArguments::Json { raw, .. } | ToolArguments::InvalidJson { raw, .. } => raw,
                };
                CompletionResultPart::Semantic(CompletionResultSemanticPart::ToolCall {
                    id,
                    name,
                    arguments_json,
                    meta,
                })
            }
        })
        .collect();
    let finish_reason = if has_tool_call {
        CompletionFinishReason::ToolCall
    } else {
        CompletionFinishReason::Stop
    };
    advance(
        state,
        HarnessCommand::CompletionSucceeded {
            action_id,
            result: CompletionResult {
                parts,
                finish_reason,
                usage: None,
            },
        },
    )
    .unwrap()
}

pub(crate) fn advance_llm_retry(
    mut state: HarnessState,
    feedback: &str,
) -> Result<Step, CoreError> {
    add_always_hook(&mut state.config, HookPoint::PostLlmCall);
    rebuild_hook_binding_index(&mut state);
    let action_id = awaiting_action_id(&state);
    let state = advance(
        state,
        HarnessCommand::CompletionSucceeded {
            action_id: action_id.clone(),
            result: CompletionResult {
                parts: vec![CompletionResultPart::Content(ContentBlock::text(
                    "rejected draft".to_string(),
                ))],
                finish_reason: CompletionFinishReason::Stop,
                usage: None,
            },
        },
    )?
    .state;
    let hook_action_id = awaiting_action_id(&state);
    advance(
        state,
        HarnessCommand::HookCompleted {
            action_id: hook_action_id,
            result: HookResult::PostLlmCall(CompletionHookOutput::Retry {
                feedback: vec![ContentBlock::text(feedback.to_string())],
            }),
        },
    )
}

pub(crate) fn advance_tool(
    state: HarnessState,
    action_id: String,
    output: Value,
    error: Option<String>,
) -> Step {
    let call_id = state
        .pending_tool_call_id(&action_id)
        .expect("advance_tool requires a pending external tool call")
        .to_string();
    let command = match error {
        Some(message) => HarnessCommand::ToolFailed {
            action_id,
            call_id,
            result: ToolResult::Failure {
                content: Vec::new(),
                structured_content: StructuredContent::Absent,
                meta: None,
                error: ProtocolError {
                    code: "tool_failed".to_string(),
                    message,
                    retryable: false,
                    details: Value::Null,
                },
            },
        },
        None => HarnessCommand::ToolSucceeded {
            action_id,
            call_id,
            result: ToolResult::Success {
                content: Vec::new(),
                structured_content: StructuredContent::present(output),
                meta: None,
            },
        },
    };
    advance(state, command).unwrap()
}

pub(crate) fn started() -> HarnessState {
    started_with(config())
}

pub(crate) fn started_with(config: HarnessConfig) -> HarnessState {
    let state = initial_state(config).unwrap();
    advance(state, user_message("work", UserMessageMode::Queue))
        .unwrap()
        .state
}

pub(crate) fn message_text(message: &impl AsMessage) -> String {
    content_blocks_to_text(&message_content(message.as_message()), "test message")
        .unwrap_or_default()
}
