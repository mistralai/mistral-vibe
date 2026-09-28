use serde_json::Value;

use crate::core::config::{
    HarnessConfig, ProvidedToolDefinition, ToolGroupDefinition, ToolGroupMetadata,
};
use crate::core::features::background_processes;
use crate::core::features::file_system;
use crate::core::features::programmatic_tool_calling::{
    ProgrammaticName, TypeScriptTool, tool_group_inventory_prompt,
};
use crate::core::features::subagents;
use crate::core::features::tool_discovery::{DiscoveryTool, ToolDiscovery};
use crate::core::tools::command_environment::CommandEnvironment;
use crate::core::tools::external::ToolTarget;
use crate::core::tools::resolved::assembly::{SELF_NAMESPACE, ToolBinding};
use crate::core::tools::resolved::self_tools::self_tools;

const SELF_SEARCH_DESCRIPTION: &str = "Tools for bounded agent self-coordination, including sleeping while background work continues.";

#[derive(Clone, Debug, PartialEq)]
pub(super) struct ResolvedProgramTools {
    descriptors: Vec<TypeScriptTool>,
    routes: Vec<(String, ToolTarget)>,
    tool_group_inventory_prompt_section: String,
}

impl ResolvedProgramTools {
    pub(super) fn compile_validated(config: &HarnessConfig) -> (Self, ToolDiscovery) {
        let accepted = AcceptedProgramGroups::compose(config);
        let tool_group_inventory_prompt_section = render_tool_group_inventory_prompt(&accepted);
        let tool_count = accepted
            .callable
            .iter()
            .map(|group| group.tools.len())
            .sum::<usize>();
        let mut descriptors = Vec::with_capacity(tool_count);
        let mut routes = Vec::with_capacity(tool_count);
        let mut discovery = ToolDiscovery::with_capacity(tool_count + accepted.notices.len());

        for group in accepted.callable {
            let CallableProgramGroup {
                namespace,
                search_description,
                connector_icon_url,
                tools,
                ..
            } = group;
            for tool in tools {
                let (descriptor, target) = tool.resolve(&namespace);
                discovery.add_tool(DiscoveryTool {
                    namespace: &namespace,
                    name: &descriptor.programmatic_name.name,
                    description: &descriptor.description,
                    input_schema: &descriptor.input_schema,
                    output_schema: descriptor.output_schema.as_ref(),
                    integration_description: &search_description,
                    connector_icon_url: connector_icon_url.as_ref(),
                });
                routes.push((descriptor.name.clone(), target));
                descriptors.push(descriptor);
            }
        }
        for notice in accepted.notices {
            discovery.add_integration_notice(
                notice.integration_name,
                notice.integration_description,
                notice.connector_id,
            );
        }

        (
            Self {
                descriptors,
                routes,
                tool_group_inventory_prompt_section,
            },
            discovery,
        )
    }

    pub(super) fn descriptors(&self) -> &[TypeScriptTool] {
        &self.descriptors
    }

    pub(super) fn target(&self, runtime_name: &str) -> Option<ToolTarget> {
        self.routes
            .iter()
            .find(|(name, _)| name == runtime_name)
            .map(|(_, target)| target.clone())
    }

    pub(super) fn tool_group_inventory_prompt_section(&self) -> &str {
        &self.tool_group_inventory_prompt_section
    }
}

#[derive(Clone, Debug, PartialEq)]
struct AcceptedProgramGroups {
    callable: Vec<CallableProgramGroup>,
    notices: Vec<IntegrationNotice>,
    listed_notice_names: Vec<String>,
}

impl AcceptedProgramGroups {
    fn compose(config: &HarnessConfig) -> Self {
        let provided_groups = config.tool_groups().collect::<Vec<_>>();
        let command_environment = config.settings.tools.command_environment;
        let mut callable = vec![
            CallableProgramGroup {
                namespace: file_system::NAMESPACE.to_string(),
                prompt: PromptContribution::Listed,
                search_description: file_system::search_description(command_environment),
                connector_icon_url: configured_icon(&provided_groups, file_system::NAMESPACE),
                tools: filesystem_program_tools(command_environment),
            },
            CallableProgramGroup {
                namespace: SELF_NAMESPACE.to_string(),
                prompt: PromptContribution::Listed,
                search_description: SELF_SEARCH_DESCRIPTION.to_string(),
                connector_icon_url: configured_icon(&provided_groups, SELF_NAMESPACE),
                tools: self_tools()
                    .into_iter()
                    .map(ProgramTool::from_tool_binding)
                    .collect(),
            },
        ];
        if config.settings.tools.background_processes.is_enabled() {
            callable.push(CallableProgramGroup {
                namespace: background_processes::NAMESPACE.to_string(),
                prompt: PromptContribution::Listed,
                search_description: background_processes::SEARCH_DESCRIPTION.to_string(),
                connector_icon_url: configured_icon(
                    &provided_groups,
                    background_processes::NAMESPACE,
                ),
                tools: background_process_program_tools(),
            });
        }
        if config.settings.tools.subagents.is_enabled() {
            callable.push(CallableProgramGroup {
                namespace: subagents::NAMESPACE.to_string(),
                prompt: PromptContribution::DedicatedSection,
                search_description: subagents::SEARCH_DESCRIPTION.to_string(),
                connector_icon_url: configured_icon(&provided_groups, subagents::NAMESPACE),
                tools: subagent_program_tools(),
            });
        }

        let mut notices = Vec::new();
        let mut listed_notice_names = Vec::new();
        for group in provided_groups {
            let tools = group
                .tools
                .iter()
                .filter(|tool| tool.exposure.is_programmatic())
                .map(|tool| provided_program_tool(group, tool))
                .collect::<Vec<_>>();
            if !tools.is_empty() {
                callable.push(CallableProgramGroup {
                    namespace: group.name.clone(),
                    prompt: PromptContribution::Listed,
                    search_description: search_description(
                        command_environment,
                        &group.name,
                        &group.description,
                    ),
                    connector_icon_url: group.icon_url.clone(),
                    tools,
                });
                continue;
            }
            let connector_id = group.metadata.as_ref().map(|metadata| match metadata {
                ToolGroupMetadata::Connector { connector_id } => connector_id.clone(),
            });
            if !group.description.trim().is_empty() || connector_id.is_some() {
                if !group.tools.iter().any(|tool| tool.exposure.is_direct()) {
                    listed_notice_names.push(group.name.clone());
                }
                notices.push(IntegrationNotice {
                    integration_name: group.name.clone(),
                    integration_description: group.description.clone(),
                    connector_id,
                });
            }
        }

        Self {
            callable,
            notices,
            listed_notice_names,
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
struct CallableProgramGroup {
    namespace: String,
    prompt: PromptContribution,
    search_description: String,
    connector_icon_url: Option<String>,
    tools: Vec<ProgramTool>,
}

#[derive(Clone, Debug, PartialEq)]
enum PromptContribution {
    Listed,
    DedicatedSection,
}

#[derive(Clone, Debug, PartialEq)]
struct IntegrationNotice {
    integration_name: String,
    integration_description: String,
    connector_id: Option<String>,
}

#[derive(Clone, Debug, PartialEq)]
struct ProgramTool {
    runtime_name: String,
    function_name: String,
    description: String,
    input_schema: Value,
    output_schema: Option<Value>,
    target: ToolTarget,
}

impl ProgramTool {
    fn from_tool_binding(tool: ToolBinding) -> Self {
        let TypeScriptTool {
            name,
            programmatic_name,
            description,
            input_schema,
            output_schema,
        } = tool.descriptor;
        Self {
            runtime_name: name,
            function_name: programmatic_name.name,
            description,
            input_schema,
            output_schema,
            target: tool.target,
        }
    }

    fn resolve(self, namespace: &str) -> (TypeScriptTool, ToolTarget) {
        (
            TypeScriptTool {
                name: self.runtime_name,
                programmatic_name: ProgrammaticName {
                    namespace: namespace.to_string(),
                    name: self.function_name,
                },
                description: self.description,
                input_schema: self.input_schema,
                output_schema: self.output_schema,
            },
            self.target,
        )
    }
}

fn filesystem_program_tools(environment: CommandEnvironment) -> Vec<ProgramTool> {
    file_system::tools(environment)
        .into_iter()
        .map(|spec| {
            let function_name = spec.direct_name.to_string();
            ProgramTool {
                runtime_name: function_name.clone(),
                function_name,
                description: spec.description.to_string(),
                input_schema: spec.input_schema,
                output_schema: Some(spec.output_schema),
                target: ToolTarget::RuntimeBuiltin {
                    name: spec.name.runtime_tool(),
                },
            }
        })
        .collect()
}

fn background_process_program_tools() -> Vec<ProgramTool> {
    background_processes::tools()
        .into_iter()
        .map(|spec| ProgramTool {
            runtime_name: spec.name.sandbox_name().to_string(),
            function_name: spec.name.function_name().to_string(),
            description: spec.description.to_string(),
            input_schema: spec.input_schema,
            output_schema: Some(spec.output_schema),
            target: ToolTarget::RuntimeBuiltin {
                name: spec.name.runtime_tool(),
            },
        })
        .collect()
}

fn subagent_program_tools() -> Vec<ProgramTool> {
    subagents::tools()
        .into_iter()
        .map(|spec| ProgramTool {
            runtime_name: spec.name.sandbox_name().to_string(),
            function_name: spec.name.function_name().to_string(),
            description: spec.description.to_string(),
            input_schema: spec.input_schema,
            output_schema: Some(spec.output_schema),
            target: ToolTarget::RuntimeBuiltin {
                name: spec.name.runtime_tool(),
            },
        })
        .collect()
}

fn provided_program_tool(
    group: &ToolGroupDefinition,
    tool: &ProvidedToolDefinition,
) -> ProgramTool {
    ProgramTool {
        runtime_name: group.runtime_name(tool),
        function_name: tool.name.clone(),
        description: tool.description.clone(),
        input_schema: tool.input_schema.clone(),
        output_schema: tool.output_schema.clone(),
        target: ToolTarget::Provided {
            group_name: group.name.clone(),
            tool_name: tool.name.clone(),
        },
    }
}

fn configured_icon(groups: &[&ToolGroupDefinition], namespace: &str) -> Option<String> {
    groups
        .iter()
        .find(|group| group.name == namespace)
        .and_then(|group| group.icon_url.clone())
}

fn search_description(
    environment: CommandEnvironment,
    namespace: &str,
    provided_description: &str,
) -> String {
    match namespace {
        file_system::NAMESPACE => return file_system::search_description(environment),
        background_processes::NAMESPACE => {
            return background_processes::SEARCH_DESCRIPTION.to_string();
        }
        SELF_NAMESPACE => SELF_SEARCH_DESCRIPTION,
        subagents::NAMESPACE => subagents::SEARCH_DESCRIPTION,
        _ => provided_description,
    }
    .to_string()
}

fn render_tool_group_inventory_prompt(groups: &AcceptedProgramGroups) -> String {
    tool_group_inventory_prompt(
        groups
            .callable
            .iter()
            .filter_map(|group| match &group.prompt {
                PromptContribution::Listed => Some(group.namespace.as_str()),
                PromptContribution::DedicatedSection => None,
            })
            .chain(groups.listed_notice_names.iter().map(String::as_str)),
    )
}
