use std::collections::{HashMap, HashSet};

mod assembly;
mod contracts;
mod program;
mod self_tools;

#[cfg(test)]
pub(crate) mod testing;

#[cfg(test)]
pub(crate) use self::assembly::RESERVED_TOOL_NAMESPACES;
use self::contracts::{ToolContracts, validate_model_input};
use self::program::ResolvedProgramTools;
use crate::core::config::{HarnessConfig, ToolGroupMetadata};
use crate::core::error::CoreError;
use crate::core::features::large_output;
use crate::core::features::programmatic_tool_calling;
use crate::core::features::programmatic_tool_calling::{Settings, TypeScriptTool};
use crate::core::features::skills::{self, SkillDefinition};
use crate::core::features::tool_discovery::{
    self, SearchRequest, ToolDiscovery, ToolDiscoveryResult,
};
use crate::core::step_protocol::ToolDefinition;
use crate::core::tools::external::{
    ExternalTool, ExternalToolCall, RuntimeBuiltinToolName, ToolTarget,
    provided_tool_qualified_name,
};
use crate::core::wire::tool::{ToolCall, echoed_tool_name};
use serde_json::Value;

pub(crate) fn hook_tool_catalog(config: &HarnessConfig) -> Vec<crate::core::hooks::HookToolKey> {
    assembly::hook_tool_catalog(config)
}

/// The key of the tool that a top-level model call named `name` reaches, by the
/// same routes `ResolvedTools::resolve_direct_call` uses, or `None` when no
/// top-level tool has that name.
pub(crate) fn direct_tool_key(
    config: &HarnessConfig,
    name: &str,
) -> Option<crate::core::hooks::HookToolKey> {
    assembly::direct_routes(config)
        .into_iter()
        .find(|(route_name, _)| route_name == name)
        .map(|(_, target)| target.hook_tool_key())
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
        if config.settings.tools.skills.is_disabled() && config.skills().next().is_some() {
            return Err(CoreError::invalid_configuration(
                "settings.tools.skills",
                "skills require settings.tools.skills to be enabled",
            ));
        }
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
                CoreError::invalid_command(format!(
                    "unsupported top-level tool {:?}",
                    echoed_tool_name(&call.name)
                ))
            })?;
        if let ToolTarget::RuntimeBuiltin {
            name: name @ RuntimeBuiltinToolName::SelfWait,
        } = target
        {
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
            validate_model_input(Some(name), &call.name, input_schema, &call.arguments)?;
        } else {
            self.contracts
                .validate_input(&target, &call.name, &call.arguments)?;
        }
        if matches!(
            target,
            ToolTarget::RuntimeBuiltin { name } if name.is_skill_read()
        ) {
            skills::validate_request(&self.skills, &call.arguments)
                .map_err(CoreError::invalid_command)?;
        }
        Ok(self
            .contracts
            .external_tool(target, &call.name, call.arguments.clone()))
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

    /// Whether `run_typescript` and `search_tool_functions` are model tools.
    pub(crate) fn programmatic_enabled(&self) -> bool {
        self.programmatic_settings.is_enabled()
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
        Some(self.contracts.external_tool(target, name, arguments))
    }

    pub(crate) fn validate_runtime_builtin_output(
        &self,
        name: RuntimeBuiltinToolName,
        value: &Value,
    ) -> Result<(), String> {
        self.contracts.validate_runtime_builtin_output(name, value)
    }
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
        if assembly::RESERVED_TOOL_NAMESPACES.contains(&group.name.as_str()) {
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
        if !config.settings.tools.programmatic.is_enabled() && group.metadata.is_some() {
            return Err(CoreError::invalid_configuration(
                "tool_groups",
                format!(
                    "connector tool group {:?} requires programmatic tool calling",
                    group.name
                ),
            ));
        }
        for tool in &group.tools {
            if !config.settings.tools.programmatic.is_enabled() && !tool.exposure.is_direct() {
                return Err(CoreError::invalid_configuration(
                    "tool_groups",
                    format!(
                        "programmatic-only tool {:?} requires programmatic tool calling",
                        provided_tool_qualified_name(&group.name, &tool.name)
                    ),
                ));
            }
            if !tool_discovery::is_identifier(&tool.name) {
                return Err(CoreError::invalid_configuration(
                    "tool_groups",
                    format!(
                        "tool function name {:?} in group {:?} must be a valid TypeScript identifier",
                        tool.name, group.name
                    ),
                ));
            }
            let qualified_name = provided_tool_qualified_name(&group.name, &tool.name);
            if type_is_not_object(&tool.input_schema) {
                return Err(CoreError::invalid_configuration(
                    "tool_groups",
                    format!(
                        "tool {qualified_name:?}: input schema type must be \"object\", got {}",
                        tool.input_schema
                    ),
                ));
            }
            if !qualified_names.insert(qualified_name.clone()) {
                return Err(CoreError::invalid_configuration(
                    "tool_groups",
                    format!("duplicate stable tool ID {qualified_name:?}"),
                ));
            }
            if let Some(direct_name) = &tool.direct_name {
                if !tool.exposure.is_direct() {
                    return Err(CoreError::invalid_configuration(
                        "tool_groups",
                        format!(
                            "programmatic-only tool {qualified_name:?} must not set direct_name"
                        ),
                    ));
                }
                if !tool_discovery::is_identifier(direct_name) {
                    return Err(CoreError::invalid_configuration(
                        "tool_groups",
                        format!(
                            "direct tool name {direct_name:?} in group {:?} must be a valid TypeScript identifier",
                            group.name
                        ),
                    ));
                }
            }
            if tool.exposure.is_direct() {
                let direct_name = tool.direct_function_name();
                if is_reserved_direct_name(config, direct_name) {
                    return Err(CoreError::invalid_configuration(
                        "tool_groups",
                        format!("direct tool name {direct_name:?} conflicts with a built-in tool"),
                    ));
                }
                if !direct_names.insert(direct_name.to_string()) {
                    return Err(CoreError::invalid_configuration(
                        "tool_groups",
                        format!("ambiguous duplicate direct tool name {direct_name:?}"),
                    ));
                }
            }
        }
    }
    Ok(())
}

/// A tool call's arguments are an object, and the MCP specification requires a
/// tool's `inputSchema` to have `"type": "object"`. So a tool whose input schema
/// sets top-level types other than `object` is refused: by its `type`, by the
/// branches of an `anyOf` or `oneOf` (also nested), or by a local `$ref` (`#/…`),
/// which is read in place of the keywords next to it. `{"type": "string"}`,
/// `{"type": ["object", "null"]}` and `{"anyOf": [{"type": "string"}, {"type":
/// "object"}]}` are refused. A schema that sets no type, such as `{}`, passes,
/// and so does a `$ref` that does not resolve in the schema.
/// The Runtimes drop a tool that an MCP server or a connector lists with such a
/// schema by the same rule; `fixtures/tool-input-schema-types.json` holds the
/// cases they all check.
fn type_is_not_object(schema: &Value) -> bool {
    TopLevelTypes::new(schema)
        .of(schema)
        .is_some_and(|types| types != ["object"])
}

/// The types a schema allows at its top level, sorted, or `None` when it sets
/// none. Each local `$ref` target is read once, so a schema that refers to the
/// same target many times, as an `anyOf` of two `$ref` per level does, takes
/// time in its size, not exponential in its depth. A `$ref` to a target that is
/// being read is a cycle, and sets no type. The branches of an `anyOf` or
/// `oneOf` are read until the first one that sets no type.
struct TopLevelTypes<'a> {
    /// The whole schema, against which a local `$ref` resolves.
    root: &'a Value,
    /// The pointers of the `$ref` targets being read, the root first.
    reading: Vec<&'a str>,
    /// The types of each `$ref` target already read.
    read: HashMap<&'a str, Option<Vec<String>>>,
}

impl<'a> TopLevelTypes<'a> {
    /// A `$ref` chain longer than this sets no type.
    const MAX_REF_DEPTH: usize = 32;

    fn new(root: &'a Value) -> Self {
        Self {
            root,
            reading: vec![""],
            read: HashMap::new(),
        }
    }

    fn of(&mut self, schema: &'a Value) -> Option<Vec<String>> {
        let schema = schema.as_object()?;
        if let Some(reference) = schema.get("$ref") {
            return self.of_ref(reference);
        }
        let mut types = match schema.get("type") {
            Some(Value::String(name)) => Some(vec![name.clone()]),
            Some(Value::Array(names)) if !names.is_empty() => Some(
                names
                    .iter()
                    .map(|name| name.as_str().unwrap_or_default().to_string())
                    .collect(),
            ),
            _ => None,
        };
        for keyword in ["anyOf", "oneOf"] {
            let Some(branches) = schema.get(keyword).and_then(Value::as_array) else {
                continue;
            };
            if branches.is_empty() {
                continue;
            }
            let mut union: Vec<String> = Vec::new();
            let mut all_typed = true;
            for branch in branches {
                let Some(found) = self.of(branch) else {
                    all_typed = false;
                    break;
                };
                union.extend(found);
            }
            if !all_typed {
                continue;
            }
            types = Some(match types {
                Some(types) => types
                    .into_iter()
                    .filter(|name| union.contains(name))
                    .collect(),
                None => union,
            });
        }
        types.map(|mut types| {
            types.sort();
            types.dedup();
            types
        })
    }

    fn of_ref(&mut self, reference: &'a Value) -> Option<Vec<String>> {
        let pointer = reference.as_str()?.strip_prefix('#')?;
        if self.reading.contains(&pointer) || self.reading.len() > Self::MAX_REF_DEPTH {
            return None;
        }
        if let Some(types) = self.read.get(pointer) {
            return types.clone();
        }
        let types = match self.root.pointer(pointer) {
            Some(target) => {
                self.reading.push(pointer);
                let types = self.of(target);
                self.reading.pop();
                types
            }
            None => None,
        };
        self.read.insert(pointer, types.clone());
        types
    }
}

fn build_top_level_tools(config: &HarnessConfig) -> Vec<ToolDefinition> {
    // Caller direct tools come first: placed after `run_typescript`, models such as
    // `mistral-large-4` treat them as program functions and never call them directly.
    let mut tools: Vec<ToolDefinition> = config
        .tool_groups()
        .flat_map(|group| &group.tools)
        .filter(|tool| tool.exposure.is_direct())
        .map(|tool| ToolDefinition {
            name: tool.direct_function_name().to_string(),
            description: tool.description.clone(),
            parameters: tool.input_schema.clone(),
        })
        .collect();
    if config.settings.tools.programmatic.is_enabled() {
        tools.push(tool_discovery::direct_tool(
            &config.settings.prompts.discovery_description,
        ));
        tools.extend(programmatic_tool_calling::direct_tools(
            &config.settings.prompts.programs.description,
        ));
    }
    tools.extend(assembly::builtin_direct_tools(config));
    tools
}

fn is_reserved_direct_name(config: &HarnessConfig, name: &str) -> bool {
    tool_discovery::is_direct_name(name)
        || programmatic_tool_calling::is_direct_name(name)
        || assembly::is_reserved_direct_name(
            config.settings.tools.command_environment,
            config.settings.tools.skills,
            name,
        )
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
    use crate::core::hooks::{HookToolKey, HookToolTarget};
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
                direct_name: None,
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
                direct_name: None,
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
    /// *Prepare*: A direct-only caller tool with a tool-list name, a caller tool that is also a
    /// program function, the file system tools and `wait`.
    /// *Do*: Compile the resolved tools.
    /// *Assert*: The prompt names, in tool-list order, only the top-level tools that
    /// `run_typescript` cannot call: the direct-only caller tool by its tool-list name, then
    /// `wait`.
    ///
    #[test]
    fn the_prompt_names_every_top_level_tool_that_programs_cannot_call() {
        // Prepare
        let mut config = config();
        config.settings.tools.model_wait = crate::core::features::model_wait::Mode::Enabled {
            external_input: true,
        };
        config.capabilities.tool_groups = vec![
            direct_named_group(
                "inventory",
                ProvidedToolExposure::Direct,
                Some("inventory_delete_item"),
            ),
            direct_named_group(
                "archive",
                ProvidedToolExposure::DirectAndProgrammatic,
                Some("archive_delete_item"),
            ),
        ];

        // Do
        let tools = ResolvedTools::compile(&config).unwrap();

        // Assert
        assert!(tools.tool_group_inventory_prompt_section().ends_with(
            "\n\nHere are tools that are NOT available in `run_typescript` and \
             `search_tool_functions`, call them directly: `inventory_delete_item`, `wait`"
        ));
    }

    fn direct_named_group(
        group_name: &str,
        exposure: ProvidedToolExposure,
        direct_name: Option<&str>,
    ) -> ToolGroupDefinition {
        ToolGroupDefinition {
            name: group_name.to_string(),
            description: String::new(),
            metadata: None,
            icon_url: None,
            tools: vec![ProvidedToolDefinition {
                name: "delete_item".to_string(),
                description: "Delete an item".to_string(),
                input_schema: json!({"type": "object"}),
                output_schema: None,
                exposure,
                direct_name: direct_name.map(str::to_string),
            }],
        }
    }

    ///
    /// *Prepare*: Two groups each expose a direct tool named `delete_item` with distinct direct names.
    /// *Do*: Compile the resolved tools.
    /// *Assert*: The top-level catalog lists the direct names, and each routes to its own stable ID.
    ///
    #[test]
    fn direct_names_keep_same_named_tools_from_different_groups_distinct() {
        // Prepare
        let mut config = config();
        config.capabilities.tool_groups = vec![
            direct_named_group(
                "inventory",
                ProvidedToolExposure::Direct,
                Some("inventory_delete_item"),
            ),
            direct_named_group(
                "archive",
                ProvidedToolExposure::DirectAndProgrammatic,
                Some("archive_delete_item"),
            ),
        ];

        // Do
        let tools = ResolvedTools::compile(&config).unwrap();
        let call = |name: &str| ToolCall {
            id: format!("{name}-call"),
            name: name.to_string(),
            arguments: json!({}),
            argument_error: None,
        };

        // Assert
        let names = tools
            .top_level_tools()
            .iter()
            .map(|tool| tool.name.as_str())
            .collect::<Vec<_>>();
        assert!(names.contains(&"inventory_delete_item"));
        assert!(names.contains(&"archive_delete_item"));
        assert!(!names.contains(&"delete_item"));
        for (direct_name, group_name) in [
            ("inventory_delete_item", "inventory"),
            ("archive_delete_item", "archive"),
        ] {
            let external = tools.resolve_direct_call(&call(direct_name)).unwrap();
            assert_eq!(
                external.hook_tool_key().qualified_name,
                format!("{group_name}.delete_item")
            );
        }
    }

    ///
    /// *Prepare*: Tool groups set `direct_name` on a programmatic-only tool, reuse one direct name, or use an invalid one.
    /// *Do*: Compile the resolved tools for each.
    /// *Assert*: Each configuration is rejected.
    ///
    #[test]
    fn rejects_invalid_direct_names() {
        let cases = [
            vec![direct_named_group(
                "inventory",
                ProvidedToolExposure::Programmatic,
                Some("inventory_delete_item"),
            )],
            vec![
                direct_named_group("inventory", ProvidedToolExposure::Direct, Some("delete")),
                direct_named_group("archive", ProvidedToolExposure::Direct, Some("delete")),
            ],
            vec![direct_named_group(
                "inventory",
                ProvidedToolExposure::Direct,
                Some("inventory.delete_item"),
            )],
        ];
        for tool_groups in cases {
            // Prepare
            let mut config = config();
            config.capabilities.tool_groups = tool_groups;

            // Do
            let result = ResolvedTools::compile(&config);

            // Assert
            assert!(result.is_err(), "{:?}", config.capabilities.tool_groups);
        }
    }

    ///
    /// *Prepare*: A config with the built-in direct tools, `wait`, a provided group that
    /// exposes one tool directly, and one whose direct tool has its own direct name.
    /// *Do*: Look up the tool behind each model-facing name.
    /// *Assert*: Each name reaches its full tool ID; a name no top-level tool has reaches none.
    ///
    #[test]
    fn direct_tool_key_names_the_tool_a_model_call_reaches() {
        // Prepare
        let mut config = config();
        config.settings.tools.model_wait =
            serde_json::from_value(json!({"mode": "enabled"})).unwrap();
        config.capabilities.tool_groups = vec![ToolGroupDefinition {
            name: "clientInteraction".to_string(),
            description: "Request structured participation from the connected client.".to_string(),
            metadata: None,
            icon_url: None,
            tools: vec![ProvidedToolDefinition {
                name: "askUser".to_string(),
                description: "Ask the user to choose one of a bounded set of options.".to_string(),
                input_schema: json!({"type": "object"}),
                output_schema: None,
                exposure: ProvidedToolExposure::Direct,
                direct_name: None,
            }],
        }];
        config.capabilities.tool_groups.push(direct_named_group(
            "inventory",
            ProvidedToolExposure::Direct,
            Some("inventory_delete_item"),
        ));

        // Do
        let keys = [
            "read_file",
            "wait",
            "askUser",
            "inventory_delete_item",
            "delete_item",
            "no_such_tool",
        ]
        .map(|name| direct_tool_key(&config, name));

        // Assert
        assert_eq!(
            keys,
            [
                Some(HookToolKey {
                    target: HookToolTarget::Filesystem,
                    qualified_name: "file_system.read_file".to_string(),
                }),
                Some(HookToolKey {
                    target: HookToolTarget::SelfTool,
                    qualified_name: "self.wait".to_string(),
                }),
                Some(HookToolKey {
                    target: HookToolTarget::Provided,
                    qualified_name: "clientInteraction.askUser".to_string(),
                }),
                Some(HookToolKey {
                    target: HookToolTarget::Provided,
                    qualified_name: "inventory.delete_item".to_string(),
                }),
                None,
                None,
            ]
        );
    }
}
