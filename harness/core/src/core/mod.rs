//! Harness Core: one session interface over the Harness Step Protocol.
//!
//! `HarnessSession` is the crate-visible seam. Lifecycle state, reducers,
//! checkpoint conversion, resolved-tool construction, and code-mode evaluation are
//! private implementation modules behind it.

mod action_id;
mod capabilities;
mod checkpoint;
pub(crate) mod config;
mod error;
mod features;
mod helpers;
mod hooks;
mod model_context;
mod model_input_budget;
mod model_input_projection;
mod prompt;
mod router;
mod session;
mod state;
mod step_protocol;
mod tools;
mod turn;
mod wire;

#[cfg(test)]
mod contract;
#[cfg(test)]
pub(crate) mod testing;
pub(crate) use checkpoint::Checkpoint;
#[allow(unused_imports)]
pub(crate) use checkpoint::decode_checkpoint;
pub(crate) use config::HarnessConfig;
#[cfg(test)]
pub(crate) use features::large_output::Policy as LargeOutputPolicy;
pub(crate) use session::HarnessSession;
pub(crate) use step_protocol::{HarnessApplyResult, HarnessInput, Inspection};
pub(crate) use wire::content::ContentBlock;
pub(crate) use wire::message::Message;

#[cfg(feature = "benchmark")]
pub(crate) use features::tool_discovery::run_benchmark as run_tool_discovery_benchmark;

// Internal-state vocabulary remains available only to white-box tests. The
// production interface above stays small.
#[cfg(test)]
#[allow(unused_imports)]
pub(crate) use testing::{
    AcceptedToolResult, Action, ActionAbandonedCause, ActivePhase, ActiveTurn,
    AgentCompletionAcceptance, AssistantPart, AssistantRole, AssistantSemanticPart,
    BackgroundProcessMode, CandidateDiscardCause, CandidateMessage, CommandEnvironment,
    CommandState, CompactionBudget, CompactionPolicy, CompactionTrigger, CompletionActionKind,
    CompletionCandidate, CompletionFinishReason, CompletionHookOutput, CompletionPurpose,
    CompletionResult, CompletionResultPart, CompletionResultSemanticPart, ContextSettings,
    ExternalTool, ExternalToolCall, HarnessConfigurationChange, HarnessSettings, HookBinding,
    HookCall, HookPoint, HookResult, HookSelector, HookToolCall, HookToolKey, HookToolTarget,
    ImageDeliveryMode, ImageDeliverySettings, ModelInputUpdate, ModelMessageUpdate,
    ModelToolCatalogUpdate, Notification, NotificationLevel, NotificationSource,
    PendingActionInspection, PendingCompletionPurpose, PendingLifecycleHook, PreAgentTurnOutput,
    PreToolCallOutput, ProcessTerminalStatus, ProgramExecution, ProgrammaticToolSettings,
    ProtocolError, ProvidedToolCall, ProvidedToolDefinition, ProvidedToolExposure, QueuedTurn,
    ReasoningContent, RuntimeBuiltinToolCall, RuntimeBuiltinToolName, SessionStatus,
    SkillDefinition, StateInspection, StoredMessageSource, StructuredContent, SubagentMode,
    TokenUsage, ToolArguments, ToolBatch, ToolCall, ToolContext, ToolDefinition, ToolExecution,
    ToolExecutionState, ToolGroupDefinition, ToolGroupMetadata, ToolOrigin, ToolOutcome,
    ToolResult, ToolSettings, ToolTarget, Turn, TurnOutcome, TurnSettings, UserMessageMode,
};

use crate::core::config::HarnessConfigUpdate;
use crate::core::error::CoreError;
use crate::core::features::compaction::prompt_message;
use crate::core::features::notifications::NotificationState;
use crate::core::features::skills;
use crate::core::features::subagents;
use crate::core::model_context::ModelContext;
use crate::core::model_input_budget::estimate_model_input_tokens;
use crate::core::prompt::{build_system_prompt, configuration_update_changes_system_prompt};
use crate::core::router::reduce;
use crate::core::state::{HarnessState, TurnState};
use crate::core::step_protocol::DeterminismContext;
use crate::core::step_protocol::HarnessCommand;
use crate::core::step_protocol::{Outcome, Transition, TurnCompletion};
use crate::core::tools::resolved::ResolvedTools;
use crate::core::wire::message::StoredMessage;

pub(crate) fn hook_tool_catalog(config: &HarnessConfig) -> Vec<hooks::HookToolKey> {
    tools::resolved::hook_tool_catalog(config)
}

pub(crate) fn initial_state(config: HarnessConfig) -> Result<HarnessState, CoreError> {
    initial_state_with_history(config, Vec::new())
}

pub(crate) fn initial_state_with_history(
    config: HarnessConfig,
    initial_history: Vec<Message>,
) -> Result<HarnessState, CoreError> {
    if config.task_id.trim().is_empty() {
        return Err(CoreError::invalid_configuration(
            "task_id",
            "task_id must not be empty",
        ));
    }
    config::validate_settings(&config.settings)?;
    config::validate_skill_catalog_fingerprint(config.skill_catalog_fingerprint.as_deref())?;
    let resolved_tools = ResolvedTools::compile(&config)?;
    validate_compaction_budget(&config, &resolved_tools)?;
    skills::validate(config.skills())
        .map_err(|detail| CoreError::invalid_configuration("skills", detail))?;
    capabilities::validate(&config)
        .map_err(|detail| CoreError::invalid_configuration("capabilities", detail))?;
    let messages = import_initial_history(
        &config,
        resolved_tools.tool_group_inventory_prompt_section(),
        initial_history,
    )?;
    let hook_binding_index = capabilities::HookBindingIndex::from_config(&config);
    Ok(HarnessState {
        config,
        resolved_tools,
        hook_binding_index,
        context: ModelContext::new(messages),
        compaction_count: 0,
        last_reported_context_tokens: None,
        turn: TurnState::Idle,
        last_finished_turn_id: None,
        notifications: NotificationState::default(),
        tool_catalog: Default::default(),
    })
}

pub(crate) fn import_initial_history(
    config: &HarnessConfig,
    tool_group_inventory_prompt_section: &str,
    initial_history: Vec<Message>,
) -> Result<Vec<StoredMessage>, CoreError> {
    if initial_history.is_empty() {
        return Ok(Vec::new());
    }
    let mut messages = vec![StoredMessage::generated_system(Message::system_text(
        build_system_prompt(config, tool_group_inventory_prompt_section),
    ))];
    for message in initial_history {
        message.validate()?;
        messages.push(StoredMessage::visible(message));
    }
    Ok(messages)
}

pub(crate) fn reconfigure_in_place(
    state: &mut HarnessState,
    update: HarnessConfigUpdate,
) -> Result<(), CoreError> {
    let rebuild_system_prompt = configuration_update_changes_system_prompt(&state.config, &update);
    // Build and validate the whole next configuration before touching state so
    // this operation remains locally atomic as well as session-atomic.
    let (next_config, next_resolved_tools) = planned_config(&state.config, update)?;
    let tool_catalog_changed = tool_catalog_source_changed(&state.config, &next_config);
    let agent_image_delivery_changed = state.config.settings.context.image_delivery.agent
        != next_config.settings.context.image_delivery.agent;
    state.config = next_config;
    state.resolved_tools = next_resolved_tools;
    state.hook_binding_index = capabilities::HookBindingIndex::from_config(&state.config);
    if tool_catalog_changed {
        state.mark_tool_catalog_changed();
    }
    if rebuild_system_prompt && !state.context.is_empty() {
        let system_prompt = build_system_prompt(
            &state.config,
            state.resolved_tools.tool_group_inventory_prompt_section(),
        );
        let messages = patched_system_prompt(state.context.messages(), system_prompt)?;
        state.context.replace_messages(messages);
    } else if agent_image_delivery_changed && !state.context.is_empty() {
        state.context.mark_projection_replacement();
    }
    Ok(())
}

fn patched_system_prompt(
    messages: &[StoredMessage],
    system_prompt: String,
) -> Result<Vec<StoredMessage>, CoreError> {
    let mut messages = messages.to_vec();
    if let Some(index) = messages.iter().position(StoredMessage::is_generated_system)
        && index != 0
    {
        return Err(CoreError::invariant(
            "generated system prompt must be the first system message",
        ));
    }
    match messages.first_mut() {
        Some(stored) if stored.is_generated_system() => {
            let Message::System { content } = &mut stored.message else {
                return Err(CoreError::invariant(
                    "generated system message has a non-system role",
                ));
            };
            match content.first_mut() {
                Some(ContentBlock::Text(content)) => content.text = system_prompt,
                _ => content.insert(0, ContentBlock::text(system_prompt)),
            }
        }
        Some(_) | None => messages.insert(
            0,
            StoredMessage::generated_system(Message::system_text(system_prompt)),
        ),
    }
    Ok(messages)
}

fn tool_catalog_source_changed(current: &HarnessConfig, next: &HarnessConfig) -> bool {
    current.settings.tools.command_environment != next.settings.tools.command_environment
        || current.capabilities.tool_groups != next.capabilities.tool_groups
        || current.capabilities.skills != next.capabilities.skills
        || current.plugins.len() != next.plugins.len()
        || current
            .plugins
            .iter()
            .zip(&next.plugins)
            .any(|(current, next)| {
                current.capabilities.tool_groups != next.capabilities.tool_groups
                    || current.capabilities.skills != next.capabilities.skills
            })
}

/// Applies an update to a configuration and validates the result.
///
/// Pure: it either returns a fully valid configuration or an error, so the
/// caller can swap it in with no intermediate state to undo.
fn planned_config(
    current: &HarnessConfig,
    update: HarnessConfigUpdate,
) -> Result<(HarnessConfig, ResolvedTools), CoreError> {
    let mut next = current.clone();
    if let Some(settings) = update.settings {
        subagents::validate_reconfiguration(
            current.settings.tools.subagents,
            settings.tools.subagents,
        )
        .map_err(|detail| CoreError::invalid_configuration("settings.tools.subagents", detail))?;
        next.settings = settings;
    }
    if let Some(capabilities) = update.capabilities {
        next.capabilities = capabilities;
    }
    if let Some(skill_catalog_fingerprint) = update.skill_catalog_fingerprint {
        next.skill_catalog_fingerprint = Some(skill_catalog_fingerprint);
    }
    if let Some(plugins) = update.plugins {
        next.plugins = plugins;
    }
    if let Some(system_instructions) = update.system_instructions {
        next.system_instructions = system_instructions;
    }

    config::validate_settings(&next.settings)?;
    config::validate_skill_catalog_fingerprint(next.skill_catalog_fingerprint.as_deref())?;
    let resolved_tools = ResolvedTools::compile(&next)?;
    validate_compaction_budget(&next, &resolved_tools)?;
    skills::validate(next.skills())
        .map_err(|detail| CoreError::invalid_configuration("skills", detail))?;
    capabilities::validate(&next)
        .map_err(|detail| CoreError::invalid_configuration("capabilities", detail))?;
    Ok((next, resolved_tools))
}

fn validate_compaction_budget(
    config: &HarnessConfig,
    resolved_tools: &ResolvedTools,
) -> Result<(), CoreError> {
    let Some(token_threshold) = config.settings.context.compaction.token_threshold() else {
        return Ok(());
    };
    let messages = vec![
        StoredMessage::generated_system(Message::system_text(build_system_prompt(
            config,
            resolved_tools.tool_group_inventory_prompt_section(),
        ))),
        prompt_message(""),
    ];
    if estimate_model_input_tokens(
        &messages,
        resolved_tools.top_level_tools(),
        config.settings.context.image_delivery.compaction,
    )? >= token_threshold
    {
        return Err(CoreError::invalid_configuration(
            "settings.context.compaction.token_threshold",
            "token threshold cannot fit the generated system prompt, tool catalog, and compaction prompt",
        ));
    }
    Ok(())
}

pub(crate) fn advance_in_place(
    state: &mut HarnessState,
    command: HarnessCommand,
    determinism: DeterminismContext,
) -> Result<Transition, CoreError> {
    let Outcome {
        actions,
        observations,
        completed,
    } = reduce(state, command, determinism)?;
    state.commit_model_input_changes()?;
    // A completion produced by this command wins over the one recorded in
    // state: `complete_output` can finish a turn and immediately start a queued
    // one, leaving state active while the transition still reports the turn
    // that just ended.
    let completed = completed.or_else(|| terminal_completion(&state.turn));
    Ok(Transition {
        completed,
        effects: actions,
        observations,
    })
}

/// The terminal result already recorded in state, for commands that did not end
/// a turn themselves but are reported while a finished turn is still current.
fn terminal_completion(turn: &TurnState) -> Option<TurnCompletion> {
    match turn {
        TurnState::Terminal { turn_id, outcome } => Some(TurnCompletion {
            turn_id: turn_id.clone(),
            outcome: outcome.clone(),
        }),
        TurnState::Idle | TurnState::Compacting { .. } | TurnState::Active(_) => None,
    }
}

pub(crate) fn require_action_id(expected: &str, actual: &str) -> Result<(), CoreError> {
    if expected == actual {
        return Ok(());
    }
    Err(CoreError::invalid_command(format!(
        "action result mismatch: expected {expected:?}, got {actual:?}"
    )))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::testing::*;
    use crate::core::wire::content::ResourceContents;

    #[test]
    fn initial_history_survives_checkpoint_restore_without_generated_prompt_duplication() {
        let config = config();
        let initial_history = vec![
            Message::system_text("Imported system context"),
            Message::user(vec![
                ContentBlock::text("Find the document"),
                ContentBlock::image("aW1hZ2U=", "image/png"),
            ]),
            Message::assistant(vec![
                AssistantPart::Semantic(AssistantSemanticPart::Reasoning {
                    content: vec![
                        ReasoningContent::Text {
                            text: "Need the file".to_string(),
                        },
                        ReasoningContent::Summary {
                            text: "Inspect the requested file".to_string(),
                        },
                    ],
                    meta: None,
                }),
                AssistantPart::Content(ContentBlock::text("I will inspect it.")),
                AssistantPart::Semantic(AssistantSemanticPart::ToolCall {
                    id: "lookup-1".to_string(),
                    name: "lookup".to_string(),
                    arguments: ToolArguments::Json {
                        raw: r#"{"path":"/workspace/doc.md"}"#.to_string(),
                        value: json!({"path": "/workspace/doc.md"}),
                    },
                    meta: None,
                }),
            ]),
            Message::tool_success(
                "lookup-1".to_string(),
                "lookup".to_string(),
                vec![ContentBlock::resource(
                    ResourceContents::TextResourceContents {
                        uri: "file:///workspace/doc.md".to_string(),
                        mime_type: Some("text/markdown".to_string()),
                        text: "Document contents".to_string(),
                        meta: None,
                    },
                )],
            ),
        ];

        let session =
            crate::core::HarnessSession::create_with_history(config.clone(), initial_history)
                .unwrap();
        let checkpoint = session.checkpoint().unwrap();
        let checkpoint_value = serde_json::to_value(&checkpoint).unwrap();
        assert!(checkpoint_value.get("configuration").is_none());
        let messages: Vec<StoredMessage> =
            serde_json::from_value(checkpoint_value["context"]["messages"].clone()).unwrap();
        assert_eq!(
            messages
                .iter()
                .map(|stored| message_role(&stored.message))
                .collect::<Vec<_>>(),
            [Role::System, Role::User, Role::Assistant, Role::Tool]
        );
        assert_eq!(message_content(&messages[0].message).len(), 1);
        assert_eq!(message_text(&messages[0]), "Imported system context");
        assert!(matches!(
            message_content(&messages[1].message)[1],
            ContentBlock::Image(_)
        ));
        assert_eq!(messages[2].message.tool_calls()[0].id, "lookup-1");
        let Message::Tool {
            tool_call_id,
            content,
            ..
        } = &messages[3].message
        else {
            panic!("expected tool message")
        };
        assert_eq!(tool_call_id, "lookup-1");
        assert!(matches!(content[0], ContentBlock::Resource(_)));

        let encoded = serde_json::to_string(&checkpoint).unwrap();
        let restored = crate::core::HarnessSession::restore(
            config,
            crate::core::decode_checkpoint(&encoded).unwrap(),
            0,
        )
        .unwrap();
        assert_eq!(restored.checkpoint().unwrap(), checkpoint);
    }

    #[test]
    fn initial_history_preserves_system_messages_at_any_position() {
        let config = config();
        let initial_history = vec![
            Message::user_text("Existing turn"),
            Message::system_text("Late system prompt"),
        ];
        let state = initial_state_with_history(config, initial_history).unwrap();
        assert_eq!(
            message_role(&state.context.messages()[1].message),
            Role::User
        );
        assert_eq!(
            message_role(&state.context.messages()[2].message),
            Role::System
        );
    }

    #[test]
    fn reconfigure_updates_runtime_limits_and_existing_system_message() {
        let mut settings = config().settings;
        settings.turn.max_iterations = Some(2);
        settings.context.compaction = CompactionPolicy::Automatic {
            token_threshold: 40_000,
        };
        settings.tools.programmatic.max_effects = 64;
        settings.tools.programmatic.max_operations = 512;
        let state = reconfigure(
            started(),
            HarnessConfigUpdate {
                system_instructions: Some("updated system".to_string()),
                settings: Some(settings),
                capabilities: None,
                skill_catalog_fingerprint: None,
                plugins: None,
            },
        )
        .unwrap();

        assert_eq!(state.config.system_instructions, "updated system");
        assert_eq!(state.config.settings.turn.max_iterations, Some(2));
        assert_eq!(
            state.config.settings.context.compaction,
            CompactionPolicy::Automatic {
                token_threshold: 40_000
            }
        );
        assert_eq!(state.config.settings.tools.programmatic.max_effects, 64);
        assert_eq!(state.config.settings.tools.programmatic.max_operations, 512);
        assert_eq!(
            message_role(&state.context.messages()[0].message),
            Role::System
        );
        assert!(message_text(&state.context.messages()[0]).contains("updated system"));
    }

    #[test]
    fn reconfigure_rebuilds_the_hook_binding_index() {
        let mut initial = config();
        add_tool_hook(
            &mut initial,
            "write-hook",
            HookPoint::PreToolCall,
            0,
            HookToolTarget::Filesystem,
            "file_system.write_file",
        );
        let state = initial_state(initial).unwrap();
        let write_key = HookToolKey {
            target: HookToolTarget::Filesystem,
            qualified_name: "file_system.write_file".to_string(),
        };
        let read_key = HookToolKey {
            target: HookToolTarget::Filesystem,
            qualified_name: "file_system.read_file".to_string(),
        };
        assert_eq!(
            state
                .hook_binding_index
                .select(HookPoint::PreToolCall, Some(&write_key)),
            ["write-hook"]
        );

        let mut replacement = config();
        add_tool_hook(
            &mut replacement,
            "read-hook",
            HookPoint::PreToolCall,
            0,
            HookToolTarget::Filesystem,
            "file_system.read_file",
        );
        let state = reconfigure(
            state,
            HarnessConfigUpdate {
                capabilities: Some(replacement.capabilities),
                ..HarnessConfigUpdate::default()
            },
        )
        .unwrap();

        assert!(
            state
                .hook_binding_index
                .select(HookPoint::PreToolCall, Some(&write_key))
                .is_empty()
        );
        assert_eq!(
            state
                .hook_binding_index
                .select(HookPoint::PreToolCall, Some(&read_key)),
            ["read-hook"]
        );
    }

    #[test]
    fn reconfigure_does_not_make_subagents_mutable() {
        let state = started();
        let mut settings = state.config.settings.clone();
        settings.tools.subagents = SubagentMode::Disabled;

        let error = reconfigure(
            state,
            HarnessConfigUpdate {
                settings: Some(settings),
                ..HarnessConfigUpdate::default()
            },
        )
        .unwrap_err();

        assert_eq!(
            error.detail(),
            "settings.tools.subagents cannot be reconfigured after session creation"
        );
    }

    #[test]
    fn reconfigure_rejects_an_operation_limit_below_the_effect_limit_without_mutation() {
        let state = started();
        let before = state.clone();
        let mut settings = before.config.settings.clone();
        settings.tools.programmatic.max_effects = 64;
        settings.tools.programmatic.max_operations = 63;

        let error = reconfigure(
            state,
            HarnessConfigUpdate {
                settings: Some(settings),
                ..HarnessConfigUpdate::default()
            },
        )
        .unwrap_err();

        assert_eq!(
            error.detail(),
            "settings.tools.programmatic.max_operations must be greater than or equal to settings.tools.programmatic.max_effects"
        );
        assert_eq!(before.config.settings.tools.programmatic.max_effects, 128);
        assert_eq!(
            before.config.settings.tools.programmatic.max_operations,
            1024
        );
    }
}
