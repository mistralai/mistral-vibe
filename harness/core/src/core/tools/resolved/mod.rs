use std::collections::HashSet;

mod assembly;
mod contracts;
mod program;
mod self_tools;

#[cfg(test)]
pub(crate) mod testing;

use self::contracts::{ToolContracts, validate_model_input};
use self::program::ResolvedProgramTools;
use crate::core::config::{HarnessConfig, ToolGroupMetadata};
use crate::core::error::CoreError;
use crate::core::features::programmatic_tool_calling;
use crate::core::features::programmatic_tool_calling::{Settings, TypeScriptTool};
use crate::core::features::skills::{self, SkillDefinition};
use crate::core::features::tool_discovery::{
    self, SearchRequest, ToolDiscovery, ToolDiscoveryResult,
};
use crate::core::features::{file_system, large_output};
use crate::core::step_protocol::ToolDefinition;
use crate::core::tools::external::{
    ExternalTool, ExternalToolCall, RuntimeBuiltinToolName, ToolTarget,
};
use crate::core::wire::tool::ToolCall;
use serde_json::Value;

pub(crate) fn hook_tool_catalog(config: &HarnessConfig) -> Vec<crate::core::hooks::HookToolKey> {
    assembly::hook_tool_catalog(config)
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct ResolvedTools {
    top_level_tools: Vec<ToolDefinition>,
    program: ResolvedProgramTools,
    discovery: ToolDiscovery,
    direct_routes: Vec<(String, ToolTarget)>,
    programmatic_settings: Settings,
    large_output_policy: large_output::Policy,
    skills: Vec<SkillDefinition>,
    contracts: ToolContracts,
}

impl ResolvedTools {
    pub(crate) fn compile(config: &HarnessConfig) -> Result<Self, CoreError> {
        validate_tool_groups(config)?;
        let (program, discovery) = ResolvedProgramTools::compile_validated(config);
        Ok(Self {
            top_level_tools: build_top_level_tools(config),
            program,
            discovery,
            direct_routes: assembly::direct_routes(config),
            programmatic_settings: config.settings.tools.programmatic.clone(),
            large_output_policy: config.settings.tools.large_output.clone(),
            skills: config.skills().cloned().collect(),
            contracts: ToolContracts::compile(config),
        })
    }

    pub(crate) fn top_level_tools(&self) -> &[ToolDefinition] {
        &self.top_level_tools
    }

    pub(crate) fn tool_group_inventory_prompt_section(&self) -> &str {
        self.program.tool_group_inventory_prompt_section()
    }

    pub(crate) fn program_descriptors(&self) -> &[TypeScriptTool] {
        self.program.descriptors()
    }

    #[cfg(test)]
    pub(crate) fn program_target(&self, runtime_name: &str) -> Option<ToolTarget> {
        self.program.target(runtime_name)
    }

    pub(crate) fn resolve_direct_call(&self, call: &ToolCall) -> Result<ExternalTool, CoreError> {
        let target = self
            .direct_routes
            .iter()
            .find(|(route_name, _)| route_name == &call.name)
            .map(|(_, target)| target.clone())
            .ok_or_else(|| {
                CoreError::invalid_command(format!("unsupported top-level tool {:?}", call.name))
            })?;
        if matches!(
            target,
            ToolTarget::RuntimeBuiltin {
                name: RuntimeBuiltinToolName::FileSystemSearchReplace
            }
        ) {
            let input_schema = self
                .top_level_tools
                .iter()
                .find(|tool| tool.name == call.name)
                .map(|tool| &tool.parameters)
                .ok_or_else(|| {
                    CoreError::invariant(format!(
                        "top-level tool {:?} has a route but no definition",
                        call.name
                    ))
                })?;
            validate_model_input(input_schema, &call.arguments)?;
        } else {
            self.contracts.validate_input(&target, &call.arguments)?;
        }
        if matches!(
            target,
            ToolTarget::RuntimeBuiltin { name } if name.is_skill_read()
        ) {
            skills::validate_request(&self.skills, &call.arguments)
                .map_err(CoreError::invalid_command)?;
        }
        Ok(resolve_external_tool(
            target,
            &call.name,
            call.arguments.clone(),
        ))
    }

    #[cfg(test)]
    pub(crate) fn search(&self, request: SearchRequest) -> String {
        self.discovery.search(request)
    }

    pub(crate) fn search_with_summary(&self, request: SearchRequest) -> ToolDiscoveryResult {
        self.discovery.search_with_summary(request)
    }

    pub(crate) fn programmatic_settings(&self) -> &Settings {
        &self.programmatic_settings
    }

    pub(crate) fn large_output_policy(&self) -> &large_output::Policy {
        &self.large_output_policy
    }

    pub(crate) fn resolve_effective_call(
        &self,
        original: &ExternalToolCall,
        arguments: Value,
    ) -> Result<ExternalToolCall, CoreError> {
        self.contracts.resolve_effective_call(original, arguments)
    }

    pub(crate) fn resolve_program_call(
        &self,
        name: &str,
        arguments: Value,
    ) -> Option<ExternalTool> {
        let target = self.program.target(name)?;
        Some(resolve_external_tool(target, name, arguments))
    }

    pub(crate) fn validate_runtime_builtin_output(
        &self,
        name: RuntimeBuiltinToolName,
        value: &Value,
    ) -> Result<(), String> {
        self.contracts.validate_runtime_builtin_output(name, value)
    }
}

fn resolve_external_tool(
    target: ToolTarget,
    invocation_name: &str,
    arguments: Value,
) -> ExternalTool {
    let arguments = match &target {
        ToolTarget::RuntimeBuiltin {
            name: RuntimeBuiltinToolName::FileSystemSearchReplace,
        } => file_system::edit_runtime_arguments(arguments),
        ToolTarget::RuntimeBuiltin { .. } | ToolTarget::Provided { .. } => arguments,
    };
    ExternalTool::from_target(target, invocation_name, arguments)
}

fn validate_tool_groups(config: &HarnessConfig) -> Result<(), CoreError> {
    let mut group_names = HashSet::new();
    let mut qualified_names = HashSet::new();
    let mut direct_names = HashSet::new();
    for group in config.tool_groups() {
        if !tool_discovery::is_identifier(&group.name) {
            return Err(CoreError::invalid_configuration(
                "tool_groups",
                format!(
                    "tool group name {:?} must be a valid TypeScript identifier",
                    group.name
                ),
            ));
        }
        if assembly::is_reserved_group_name(&group.name) {
            return Err(CoreError::invalid_configuration(
                "tool_groups",
                format!("tool group name {:?} is reserved", group.name),
            ));
        }
        if !group_names.insert(group.name.clone()) {
            return Err(CoreError::invalid_configuration(
                "tool_groups",
                format!("duplicate tool group name {:?}", group.name),
            ));
        }
        if let Some(ToolGroupMetadata::Connector { connector_id }) = &group.metadata
            && connector_id.trim().is_empty()
        {
            return Err(CoreError::invalid_configuration(
                "tool_groups",
                format!("tool group {:?} connector_id must not be empty", group.name),
            ));
        }
        let mut function_names = HashSet::new();
        for tool in &group.tools {
            if !tool_discovery::is_identifier(&tool.name) {
                return Err(CoreError::invalid_configuration(
                    "tool_groups",
                    format!(
                        "tool function name {:?} in group {:?} must be a valid TypeScript identifier",
                        tool.name, group.name
                    ),
                ));
            }
            if !function_names.insert(tool.name.clone()) {
                return Err(CoreError::invalid_configuration(
                    "tool_groups",
                    format!("duplicate tool function {:?}.{:?}", group.name, tool.name),
                ));
            }
            let qualified_name = format!("{}.{}", group.name, tool.name);
            if !qualified_names.insert(qualified_name.clone()) {
                return Err(CoreError::invalid_configuration(
                    "tool_groups",
                    format!("duplicate tool function {qualified_name:?}"),
                ));
            }
            if tool.exposure.is_direct() {
                if is_reserved_direct_name(config, &tool.name) {
                    return Err(CoreError::invalid_configuration(
                        "tool_groups",
                        format!(
                            "direct tool name {:?} conflicts with a built-in tool",
                            tool.name
                        ),
                    ));
                }
                if !direct_names.insert(tool.name.clone()) {
                    return Err(CoreError::invalid_configuration(
                        "tool_groups",
                        format!("ambiguous duplicate direct tool name {:?}", tool.name),
                    ));
                }
            }
        }
    }
    Ok(())
}

fn build_top_level_tools(config: &HarnessConfig) -> Vec<ToolDefinition> {
    let mut tools = vec![tool_discovery::direct_tool()];
    tools.extend(programmatic_tool_calling::direct_tools());
    tools.extend(assembly::direct_tools(config));
    tools
}

fn is_reserved_direct_name(config: &HarnessConfig, name: &str) -> bool {
    tool_discovery::is_direct_name(name)
        || programmatic_tool_calling::is_direct_name(name)
        || assembly::is_reserved_direct_name(config.settings.tools.command_environment, name)
}

#[cfg(test)]
pub(crate) fn search_unvalidated_for_test(
    config: &HarnessConfig,
    request: SearchRequest,
) -> String {
    ResolvedProgramTools::compile_validated(config)
        .1
        .search(request)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::config::{
        ProvidedToolDefinition, ProvidedToolExposure, ToolGroupDefinition, ToolGroupMetadata,
    };
    use crate::core::features::programmatic_tool_calling::ProgrammaticName;
    use crate::core::features::tool_discovery::{SearchMode, SearchRequest};
    use crate::core::testing::BackgroundProcessMode;
    use crate::core::testing::CommandEnvironment;
    use crate::core::tools::resolved::testing::{config, qualified_name};
    use serde_json::json;

    #[test]
    fn disabled_command_environment_omits_bash_from_model_catalogs() {
        // Prepare
        let mut config = config();
        config.settings.tools.command_environment = CommandEnvironment::Disabled;

        // Do
        let tools = ResolvedTools::compile(&config).unwrap();
        let direct_names = tools
            .top_level_tools()
            .iter()
            .map(|tool| tool.name.as_str())
            .collect::<Vec<_>>();
        let programmatic_names = tools
            .program_descriptors()
            .iter()
            .map(qualified_name)
            .collect::<Vec<_>>();

        // Assert
        assert!(
            direct_names
                .iter()
                .all(|name| !matches!(*name, "bash" | "git_bash" | "powershell"))
        );
        assert!(
            programmatic_names
                .iter()
                .all(|name| name != "file_system.bash")
        );
    }

    ///
    /// *Prepare*: A configuration exposes one provided calendar tool only through code mode.
    /// *Do*: Compile the resolved tools and inspect execution, prompt, and search views.
    /// *Assert*: Every view identifies the same provided tool and callable TypeScript path.
    ///
    #[test]
    fn program_groups_keep_execution_prompt_and_search_together() {
        // Prepare
        let tool_groups = vec![ToolGroupDefinition {
            name: "calendar".to_string(),
            description: "Calendar tools".to_string(),
            metadata: None,
            icon_url: None,
            tools: vec![ProvidedToolDefinition {
                name: "list_events".to_string(),
                description: "List events".to_string(),
                input_schema: json!({"type": "object"}),
                output_schema: Some(json!({"type": "array"})),
                exposure: ProvidedToolExposure::Programmatic,
            }],
        }];
        let mut config = config();
        config.capabilities.tool_groups = tool_groups;
        let runtime_name = "provided_tool::calendar::list_events";

        // Do
        let tools = ResolvedTools::compile(&config).unwrap();
        let descriptor = tools
            .program_descriptors()
            .iter()
            .find(|tool| qualified_name(tool) == "calendar.list_events")
            .unwrap();
        let search_result = tools.search(SearchRequest {
            mode: SearchMode::Details,
            functions: vec!["calendar.list_events".to_string()],
            ..SearchRequest::default()
        });
        let summary_result = tools.search(SearchRequest {
            connectors: vec!["calendar".to_string()],
            query: Some("list events".to_string()),
            ..SearchRequest::default()
        });

        // Assert
        assert_eq!(descriptor.name, runtime_name);
        assert_eq!(
            tools.program_target(runtime_name),
            Some(ToolTarget::Provided {
                group_name: "calendar".to_string(),
                tool_name: "list_events".to_string(),
            })
        );
        assert_eq!(
            descriptor.programmatic_name,
            ProgrammaticName {
                namespace: "calendar".to_string(),
                name: "list_events".to_string(),
            }
        );
        assert!(
            tools
                .tool_group_inventory_prompt_section()
                .contains("- calendar")
        );
        assert!(
            !tools
                .tool_group_inventory_prompt_section()
                .contains("Calendar tools")
        );
        assert!(summary_result.contains("Calendar tools"));
        assert!(summary_result.contains("calendar.list_events"));
        assert!(search_result.contains("function list_events"));
        assert!(search_result.contains("List events"));
    }

    ///
    /// *Prepare*: A connector is available for discovery but exposes no callable functions yet.
    /// *Do*: Compile the resolved tools and search that connector by name.
    /// *Assert*: The initial prompt lists only its name, while discovery returns its guidance.
    ///
    #[test]
    fn connector_notices_are_named_before_their_guidance_is_discovered() {
        // Prepare
        let mut config = config();
        config.capabilities.tool_groups = vec![ToolGroupDefinition {
            name: "mail".to_string(),
            description: "Connect mail to search and read messages.".to_string(),
            metadata: Some(ToolGroupMetadata::Connector {
                connector_id: "connector-mail".to_string(),
            }),
            icon_url: None,
            tools: Vec::new(),
        }];

        // Do
        let tools = ResolvedTools::compile(&config).unwrap();
        let result = tools.search(SearchRequest {
            connectors: vec!["mail".to_string()],
            query: Some("search messages".to_string()),
            ..SearchRequest::default()
        });

        // Assert
        assert!(
            tools
                .tool_group_inventory_prompt_section()
                .contains("- mail")
        );
        assert!(
            !tools
                .tool_group_inventory_prompt_section()
                .contains("Connect mail to search and read messages.")
        );
        assert!(result.contains("Connect mail to search and read messages."));
        assert!(result.contains("<status>authentication_required</status>"));
    }

    ///
    /// *Prepare*: A provided group exposes only a direct tool in the top-level catalog.
    /// *Do*: Compile the resolved tools and search that group by name.
    /// *Assert*: The initial programmatic index omits it, while explicit discovery preserves the
    /// existing non-callable notice.
    ///
    #[test]
    fn direct_only_groups_are_not_advertised_as_searchable_connectors() {
        // Prepare
        let mut config = config();
        config.capabilities.tool_groups = vec![ToolGroupDefinition {
            name: "clientInteraction".to_string(),
            description: "Request structured participation from the connected client.".to_string(),
            metadata: None,
            icon_url: None,
            tools: vec![ProvidedToolDefinition {
                name: "askUser".to_string(),
                description: "Ask the user to choose one of a bounded set of options.".to_string(),
                input_schema: json!({"type": "object"}),
                output_schema: Some(json!({"type": "object"})),
                exposure: ProvidedToolExposure::Direct,
            }],
        }];

        // Do
        let tools = ResolvedTools::compile(&config).unwrap();
        let result = tools.search(SearchRequest {
            connectors: vec!["clientInteraction".to_string()],
            query: Some("ask the user".to_string()),
            ..SearchRequest::default()
        });

        // Assert
        assert!(
            !tools
                .tool_group_inventory_prompt_section()
                .contains("- clientInteraction")
        );
        assert!(result.contains("<name>clientInteraction</name>"));
        assert!(result.contains("<status>not_callable</status>"));
    }

    ///
    /// *Prepare*: Legal provided groups reuse the built-in `self` and `process` namespaces.
    /// *Do*: Compile the resolved tools and query exact details plus group-description-only searches.
    /// *Assert*: Built-in search descriptions still win while configured icons apply to every
    /// document in the reused namespace.
    ///
    #[test]
    fn reused_builtin_namespaces_preserve_search_metadata() {
        // Prepare
        let programmatic_tool = |name: &str| ProvidedToolDefinition {
            name: name.to_string(),
            description: "Configured operation".to_string(),
            input_schema: json!({"type": "object"}),
            output_schema: Some(json!({"type": "object"})),
            exposure: ProvidedToolExposure::Programmatic,
        };
        let mut config = config();
        config.settings.tools.background_processes = BackgroundProcessMode::Enabled;
        config.capabilities.tool_groups = vec![
            ToolGroupDefinition {
                name: "self".to_string(),
                description: "selfconfiguredonlytoken".to_string(),
                metadata: None,
                icon_url: Some("https://example.test/self.png".to_string()),
                tools: vec![programmatic_tool("configured")],
            },
            ToolGroupDefinition {
                name: "process".to_string(),
                description: "processconfiguredonlytoken".to_string(),
                metadata: None,
                icon_url: Some("https://example.test/process.png".to_string()),
                tools: vec![programmatic_tool("configured")],
            },
        ];

        // Do
        let tools = ResolvedTools::compile(&config).unwrap();
        let details = tools.search(SearchRequest {
            mode: SearchMode::Details,
            functions: vec![
                "self.sleep".to_string(),
                "self.configured".to_string(),
                "process.start".to_string(),
                "process.configured".to_string(),
            ],
            ..SearchRequest::default()
        });
        let self_description_search = tools.search(SearchRequest {
            query: Some("selfconfiguredonlytoken".to_string()),
            ..SearchRequest::default()
        });
        let process_description_search = tools.search(SearchRequest {
            query: Some("processconfiguredonlytoken".to_string()),
            ..SearchRequest::default()
        });

        // Assert
        assert_eq!(
            details
                .matches("connectorIconUrl: 'https://example.test/self.png'")
                .count(),
            2
        );
        assert_eq!(
            details
                .matches("connectorIconUrl: 'https://example.test/process.png'")
                .count(),
            2
        );
        assert!(!self_description_search.contains("self.configured"));
        assert!(!process_description_search.contains("process.configured"));
    }

    ///
    /// *Prepare*: A direct-only provided group reuses the enabled `process` namespace.
    /// *Do*: Load built-in process details and search for the provided group's description.
    /// *Assert*: The configured icon decorates the built-in document while the group remains a
    /// separate non-callable integration notice.
    ///
    #[test]
    fn direct_only_reused_namespace_keeps_builtin_icon_and_notice() {
        // Prepare
        let mut config = config();
        config.settings.tools.background_processes = BackgroundProcessMode::Enabled;
        config.capabilities.tool_groups = vec![ToolGroupDefinition {
            name: "process".to_string(),
            description: "processnoticeonlytoken".to_string(),
            metadata: None,
            icon_url: Some("https://example.test/process-notice.png".to_string()),
            tools: vec![ProvidedToolDefinition {
                name: "process_status".to_string(),
                description: "Read provided process status".to_string(),
                input_schema: json!({"type": "object"}),
                output_schema: Some(json!({"type": "object"})),
                exposure: ProvidedToolExposure::Direct,
            }],
        }];

        // Do
        let tools = ResolvedTools::compile(&config).unwrap();
        let details = tools.search(SearchRequest {
            mode: SearchMode::Details,
            functions: vec!["process.start".to_string()],
            ..SearchRequest::default()
        });
        let notice = tools.search(SearchRequest {
            connectors: vec!["process".to_string()],
            query: Some("processnoticeonlytoken".to_string()),
            ..SearchRequest::default()
        });

        // Assert
        assert!(details.contains("connectorIconUrl: 'https://example.test/process-notice.png'"));
        assert!(notice.contains("<name>process</name>"));
        assert!(notice.contains("<status>not_callable</status>"));
        assert!(!notice.contains("process.process_status"));
    }
}
