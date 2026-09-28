use crate::core::error::CoreError;
pub(crate) mod batch;
mod direct;

use serde_json::json;

use crate::core::features::large_output::PendingWrite;
use crate::core::features::programmatic_tool_calling::ProgramExecution;
use crate::core::hooks::HookCall;
use crate::core::step_protocol::Action;
use crate::core::tools::external::ExternalToolCall;
use crate::core::wire::message::Message;
use crate::core::wire::tool::{ToolCall, ToolResult};

#[allow(clippy::large_enum_variant)]
#[derive(Clone, Debug, PartialEq)]
pub(crate) enum ToolExecutionState {
    DirectAwaitingPreHook {
        hook_action_id: String,
        hook_binding_ids: Vec<String>,
        call: ExternalToolCall,
    },
    DirectPending {
        call: ExternalToolCall,
    },
    DirectAwaitingPostHook {
        hook_action_id: String,
        hook_binding_ids: Vec<String>,
        call: ExternalToolCall,
        result: ToolResult,
    },
    ProgramPending {
        execution: ProgramExecution,
    },
    AwaitingLargeOutputWrite {
        action_id: String,
        source: LargeOutputSource,
        pending: PendingWrite,
    },
    Completed {
        message: Message,
    },
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) enum LargeOutputSource {
    Direct { call: ExternalToolCall },
    RunTypescript,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct ToolExecution {
    pub(crate) call: ToolCall,
    pub(crate) state: ToolExecutionState,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct ToolBatch {
    pub(crate) executions: Vec<ToolExecution>,
}

impl ToolBatch {
    pub(crate) fn pending_actions(&self, turn_id: &str) -> Vec<Action> {
        self.executions
            .iter()
            .flat_map(|execution| match &execution.state {
                ToolExecutionState::DirectAwaitingPreHook {
                    hook_action_id,
                    hook_binding_ids,
                    call,
                } => vec![Action::Hook {
                    effect_id: hook_action_id.clone(),
                    turn_id: turn_id.to_string(),
                    hook_binding_ids: hook_binding_ids.clone(),
                    call: HookCall::PreToolCall {
                        tool_call: call.into(),
                    },
                }],
                ToolExecutionState::DirectPending { call } => {
                    vec![Action::external_tool(call, turn_id)]
                }
                ToolExecutionState::DirectAwaitingPostHook {
                    hook_action_id,
                    hook_binding_ids,
                    call,
                    result,
                } => vec![Action::Hook {
                    effect_id: hook_action_id.clone(),
                    turn_id: turn_id.to_string(),
                    hook_binding_ids: hook_binding_ids.clone(),
                    call: HookCall::PostToolCall {
                        tool_call: call.into(),
                        tool_result: result.clone(),
                    },
                }],
                ToolExecutionState::ProgramPending { execution } => {
                    execution.pending_actions(turn_id)
                }
                ToolExecutionState::AwaitingLargeOutputWrite {
                    action_id, pending, ..
                } => vec![Action::filesystem_write(
                    action_id.clone(),
                    turn_id,
                    pending.relative_path().to_string(),
                    pending.serialized().to_string(),
                )],
                ToolExecutionState::Completed { .. } => Vec::new(),
            })
            .collect()
    }

    pub(crate) fn interrupted_messages(
        &self,
        interruption_message: &str,
    ) -> Result<Vec<Message>, CoreError> {
        self.executions
            .iter()
            .map(|execution| match &execution.state {
                ToolExecutionState::Completed { message } => Ok(message.clone()),
                ToolExecutionState::DirectAwaitingPreHook { .. }
                | ToolExecutionState::DirectPending { .. }
                | ToolExecutionState::DirectAwaitingPostHook { .. }
                | ToolExecutionState::ProgramPending { .. }
                | ToolExecutionState::AwaitingLargeOutputWrite { .. } => {
                    Ok(Message::tool_failure_text(
                        execution.call.id.clone(),
                        execution.call.name.clone(),
                        serde_json::to_string(&json!({
                            "result": {
                                "type": "error",
                                "error": {"name": "Interrupted", "message": interruption_message}
                            }
                        }))
                        .map_err(|error| CoreError::invariant(error.to_string()))?,
                    ))
                }
            })
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::features::tool_discovery::{SearchMode, SearchRequest};
    use crate::core::testing::*;
    use crate::core::tools::resolved;
    use crate::core::tools::result::{model_tool_result_content, model_tool_result_message};
    use crate::core::wire::content::{
        Annotations, ContentBlock, EmbeddedResource, ImageContent, MetaObject, Resource,
        ResourceContents, Role as ContentRole, text_content,
    };
    use crate::core::wire::tool::{ProtocolError, StructuredContent};
    use serde_json::Value;

    #[test]
    fn successful_tool_results_use_content_for_model() {
        let content = vec![ContentBlock::text("human-readable fallback".to_string())];
        let structured_content = StructuredContent::present(json!({"answer": 42}));
        let result = ToolResult::Success {
            content: content.clone(),
            structured_content,
            meta: None,
        };

        assert_eq!(model_tool_result_content(&result), content);
    }

    ///
    /// *Prepare*: A tool result contains assistant-visible and user-only blocks with result,
    /// block, and embedded-resource metadata.
    /// *Do*: Project the complete result into the model-facing tool message.
    /// *Assert*: User-only content and every `_meta`/annotation field are absent from the model
    /// copy while the original result remains unchanged.
    ///
    #[test]
    fn model_projection_filters_audience_and_strips_metadata() {
        // Prepare
        let annotations = |audience| {
            Some(
                serde_json::from_value::<Annotations>(json!({
                    "audience": audience,
                    "priority": 0.5,
                    "lastModified": "2026-08-07T00:00:00Z"
                }))
                .expect("annotation fixture is valid MCP"),
            )
        };
        let metadata = || {
            Some(MetaObject(
                json!({"private": true})
                    .as_object()
                    .expect("metadata fixture is an object")
                    .clone(),
            ))
        };
        let content = vec![
            ContentBlock::Image(
                ImageContent::new("user-image", "image/png")
                    .with_annotations(
                        annotations(vec![ContentRole::User]).expect("annotations exist"),
                    )
                    .with_meta(metadata().expect("metadata exists")),
            ),
            ContentBlock::Resource(
                EmbeddedResource::new(ResourceContents::TextResourceContents {
                    uri: "resource://assistant".to_string(),
                    mime_type: Some("text/plain".to_string()),
                    text: "assistant resource".to_string(),
                    meta: Some(MetaObject(
                        json!({"nested": true})
                            .as_object()
                            .expect("nested metadata fixture is an object")
                            .clone(),
                    )),
                })
                .with_annotations(
                    annotations(vec![ContentRole::Assistant]).expect("annotations exist"),
                )
                .with_meta(metadata().expect("metadata exists")),
            ),
        ];
        let result = ToolResult::Success {
            content: content.clone(),
            structured_content: StructuredContent::Absent,
            meta: Some(
                json!({"result": true})
                    .as_object()
                    .expect("result metadata fixture is an object")
                    .clone(),
            ),
        };

        // Do
        let message =
            model_tool_result_message("call-1".to_string(), "example".to_string(), &result);

        // Assert
        assert_eq!(
            message_content(&message),
            vec![ContentBlock::Resource(EmbeddedResource::new(
                ResourceContents::TextResourceContents {
                    uri: "resource://assistant".to_string(),
                    mime_type: Some("text/plain".to_string()),
                    text: "assistant resource".to_string(),
                    meta: None,
                },
            ))]
        );
        assert!(matches!(message, Message::Tool { meta: None, .. }));
        assert_eq!(
            result,
            ToolResult::Success {
                content,
                structured_content: StructuredContent::Absent,
                meta: Some(
                    json!({"result": true})
                        .as_object()
                        .expect("result metadata fixture is an object")
                        .clone(),
                ),
            }
        );
    }

    #[test]
    fn explicit_null_structured_content_uses_model_content_fallback() {
        let content = text_content(r#"{"answer":42}"#);
        let structured_content = StructuredContent::present(Value::Null);
        let result = ToolResult::Success {
            content: content.clone(),
            structured_content,
            meta: None,
        };

        assert_eq!(model_tool_result_content(&result), content);
    }

    #[test]
    fn direct_results_fall_back_to_structured_content_when_content_is_empty() {
        let result = ToolResult::Success {
            content: Vec::new(),
            structured_content: StructuredContent::present(json!({"answer": 42})),
            meta: None,
        };

        assert_eq!(
            model_tool_result_content(&result),
            text_content(r#"{"answer":42}"#)
        );
    }

    #[test]
    fn content_only_results_preserve_model_blocks() {
        let content = vec![
            ContentBlock::text(r#"{"answer":42}"#.to_string()),
            ContentBlock::resource_link(Resource::new("result://42", "answer")),
        ];
        let result = ToolResult::Success {
            content: content.clone(),
            structured_content: StructuredContent::Absent,
            meta: None,
        };

        assert_eq!(model_tool_result_content(&result), content);
    }

    #[test]
    fn failed_tool_results_keep_model_feedback_ahead_of_structured_error_data() {
        let result = ToolResult::Failure {
            content: text_content("retry with a future date"),
            structured_content: StructuredContent::present(json!({
                "field": "departure_date",
                "reason": "must be in the future"
            })),
            meta: None,
            error: ProtocolError {
                code: "invalid_date".to_string(),
                message: "invalid departure date".to_string(),
                retryable: false,
                details: Value::Null,
            },
        };

        assert_eq!(
            model_tool_result_content(&result),
            text_content("retry with a future date")
        );
    }

    #[test]
    fn model_context_exposes_control_and_direct_filesystem_tools() {
        let step = advance(
            initial_state(config()).unwrap(),
            user_message("work", UserMessageMode::Queue),
        )
        .unwrap();

        let Some(Action::Completion {
            model_input:
                ModelInputUpdate {
                    tool_catalog: ModelToolCatalogUpdate::Replace { tools, .. },
                    ..
                },
            ..
        }) = step.effect
        else {
            panic!("expected a completion effect");
        };
        assert_eq!(
            tools.into_iter().map(|tool| tool.name).collect::<Vec<_>>(),
            [
                "search_tool_functions",
                "run_typescript",
                "read_file",
                "write_file",
                "edit",
                "bash",
            ]
        );
    }

    #[test]
    fn direct_filesystem_tools_keep_bare_names_and_filesystem_routes() {
        let calls = [
            (
                "read_file",
                "file_system.read_file",
                json!({"path": "example.txt"}),
                json!({"path": "example.txt"}),
            ),
            (
                "write_file",
                "file_system.write_file",
                json!({"path": "example.txt", "content": "example"}),
                json!({"path": "example.txt", "content": "example"}),
            ),
            (
                "edit",
                "file_system.search_replace",
                json!({
                    "file_path": "example.txt",
                    "old_string": "old",
                    "new_string": "new",
                }),
                json!({
                    "file_path": "example.txt",
                    "content": [{
                        "old_str": "old",
                        "new_str": "new",
                        "replace_all": false,
                    }],
                }),
            ),
            (
                "bash",
                "file_system.bash",
                json!({"command": "pwd"}),
                json!({"command": "pwd"}),
            ),
        ];
        for (tool_name, runtime_name, model_arguments, runtime_arguments) in calls {
            let step = advance_llm(started(), assistant_tool(tool_name, model_arguments));
            assert!(matches!(
                step.effect,
                Some(Action::RuntimeBuiltinTool { call, .. })
                    if call.name.as_str() == runtime_name
                        && call.arguments == runtime_arguments
            ));
        }
    }

    #[test]
    fn provided_tool_exposure_controls_top_level_and_programmatic_catalogs() {
        assert!(
            serde_json::from_value::<ProvidedToolDefinition>(json!({
                "name": "missing_exposure",
                "input_schema": {"type": "object"}
            }))
            .is_err()
        );

        let mut config = config();
        config.capabilities.tool_groups.push(ToolGroupDefinition {
            name: "workspace".to_string(),
            description: "Workspace operations".to_string(),
            metadata: None,
            icon_url: None,
            tools: vec![
                ProvidedToolDefinition {
                    name: "programmatic_only".to_string(),
                    description: "Programmatic".to_string(),
                    input_schema: json!({"type": "object"}),
                    output_schema: None,
                    exposure: ProvidedToolExposure::Programmatic,
                },
                ProvidedToolDefinition {
                    name: "direct_only".to_string(),
                    description: "Direct".to_string(),
                    input_schema: json!({"type": "object"}),
                    output_schema: None,
                    exposure: ProvidedToolExposure::Direct,
                },
                ProvidedToolDefinition {
                    name: "both".to_string(),
                    description: "Both".to_string(),
                    input_schema: json!({"type": "object"}),
                    output_schema: None,
                    exposure: ProvidedToolExposure::DirectAndProgrammatic,
                },
            ],
        });

        let step = advance(
            initial_state(config.clone()).unwrap(),
            user_message("work", UserMessageMode::Queue),
        )
        .unwrap();
        let Some(Action::Completion {
            model_input:
                ModelInputUpdate {
                    tool_catalog: ModelToolCatalogUpdate::Replace { tools, .. },
                    ..
                },
            ..
        }) = step.effect
        else {
            panic!("expected a completion effect");
        };
        assert_eq!(
            tools.into_iter().map(|tool| tool.name).collect::<Vec<_>>(),
            [
                "search_tool_functions",
                "run_typescript",
                "read_file",
                "write_file",
                "edit",
                "bash",
                "direct_only",
                "both"
            ]
        );

        let listed = resolved::search_unvalidated_for_test(
            &config,
            SearchRequest {
                mode: SearchMode::AllConnectorCapabilities,
                connectors: vec!["workspace".to_string()],
                ..SearchRequest::default()
            },
        );
        assert!(listed.contains("workspace.programmatic_only"));
        assert!(listed.contains("workspace.both"));
        assert!(!listed.contains("workspace.direct_only"));
        assert!(
            crate::core::tools::resolved::testing::target(
                &config,
                "provided_tool::workspace::direct_only",
            )
            .is_none()
        );
    }

    #[test]
    fn direct_provided_tools_route_with_their_group_identity() {
        let mut config = config();
        config.capabilities.tool_groups.push(ToolGroupDefinition {
            name: "workspace".to_string(),
            description: String::new(),
            metadata: None,
            icon_url: None,
            tools: vec![
                ProvidedToolDefinition {
                    name: "direct_only".to_string(),
                    description: String::new(),
                    input_schema: json!({"type": "object"}),
                    output_schema: None,
                    exposure: ProvidedToolExposure::Direct,
                },
                ProvidedToolDefinition {
                    name: "both".to_string(),
                    description: String::new(),
                    input_schema: json!({"type": "object"}),
                    output_schema: None,
                    exposure: ProvidedToolExposure::DirectAndProgrammatic,
                },
            ],
        });

        for tool_name in ["direct_only", "both"] {
            let step = advance_llm(
                started_with(config.clone()),
                assistant_tool(tool_name, json!({"value": 1})),
            );
            assert!(matches!(
                step.effect,
                Some(Action::ProvidedTool { call, .. })
                    if call.group_name == "workspace" && call.tool_name == tool_name
            ));
        }

        let step = advance_llm(
            started_with(config.clone()),
            assistant_tool(
                "run_typescript",
                json!({"code": "async function main() { return tools.workspace.both({}); }"}),
            ),
        );
        assert!(matches!(
            step.effect,
            Some(Action::ProvidedTool { call, .. })
                if call.group_name == "workspace" && call.tool_name == "both"
        ));

        let step = advance_llm(
            started_with(config),
            assistant_tool(
                "run_typescript",
                json!({"code": "async function main() { return tools.file_system.bash({command: 'pwd'}); }"}),
            ),
        );
        assert!(matches!(
            step.effect,
            Some(Action::RuntimeBuiltinTool { call, .. })
                if call.name == RuntimeBuiltinToolName::FileSystemBash
        ));
    }

    #[test]
    fn externally_defined_names_preserve_case() {
        let mut config = config();
        config.capabilities.tool_groups.push(ToolGroupDefinition {
            name: "UserLibrary".to_string(),
            description: "Read user libraries.".to_string(),
            metadata: None,
            icon_url: None,
            tools: vec![ProvidedToolDefinition {
                name: "listDocuments".to_string(),
                description: "List documents.".to_string(),
                input_schema: json!({"type": "object"}),
                output_schema: None,
                exposure: ProvidedToolExposure::Programmatic,
            }],
        });
        config.capabilities.skills.push(SkillDefinition {
            name: "userLibrary".to_string(),
            description: "Use document libraries.".to_string(),
            path: "/skills/userLibrary/SKILL.md".to_string(),
        });

        let step = advance(
            initial_state(config).unwrap(),
            user_message("find a document", UserMessageMode::Queue),
        )
        .unwrap();

        let system_prompt = message_text(&step.state.context.messages()[0]);
        assert!(system_prompt.contains("- UserLibrary"));
        assert!(!system_prompt.contains("Read user libraries."));
        assert!(system_prompt.contains("<name>userLibrary</name>"));
        let Some(Action::Completion {
            model_input:
                ModelInputUpdate {
                    tool_catalog: ModelToolCatalogUpdate::Replace { tools, .. },
                    ..
                },
            ..
        }) = step.effect
        else {
            panic!("expected a completion effect");
        };
        let skill_tool = tools
            .iter()
            .find(|tool| tool.name == "skill")
            .expect("skill tool should be exposed");
        assert!(
            skill_tool.parameters["properties"]["name"]
                .get("enum")
                .is_none()
        );
    }

    #[test]
    fn runtime_qualified_skill_aliases_are_generic_catalog_names() {
        let mut config = config();
        config.capabilities.skills.push(SkillDefinition {
            name: "acme_tools:prepare-report".to_string(),
            description: "Prepare a report.".to_string(),
            path: r"C:\plugins\acme.tools\skills\prepare-report\SKILL.md".to_string(),
        });

        let step = advance(
            initial_state(config).unwrap(),
            user_message("prepare a report", UserMessageMode::Queue),
        )
        .unwrap();

        let system_prompt = message_text(&step.state.context.messages()[0]);
        assert!(system_prompt.contains("<name>acme_tools:prepare-report</name>"));
        let Some(Action::Completion {
            model_input:
                ModelInputUpdate {
                    tool_catalog: ModelToolCatalogUpdate::Replace { tools, .. },
                    ..
                },
            ..
        }) = step.effect
        else {
            panic!("expected a completion effect");
        };
        let skill_tool = tools
            .iter()
            .find(|tool| tool.name == "skill")
            .expect("skill tool should be exposed");
        assert!(
            skill_tool.parameters["properties"]["name"]
                .get("enum")
                .is_none()
        );
    }
}
