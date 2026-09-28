//! Cross-feature assembly for the tools available in one accepted session
//! configuration.

use std::collections::BTreeSet;

use crate::core::config::HarnessConfig;
use crate::core::features::background_processes;
use crate::core::features::file_system;
use crate::core::features::programmatic_tool_calling::{ProgrammaticName, TypeScriptTool};
use crate::core::features::skills;
use crate::core::features::subagents;
use crate::core::hooks::{HookToolKey, HookToolTarget};
use crate::core::step_protocol::ToolDefinition;
use crate::core::tools::command_environment::CommandEnvironment;
use crate::core::tools::external::ToolTarget;
use crate::core::tools::resolved::self_tools::self_tools;

pub(crate) const SELF_NAMESPACE: &str = "self";

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct ToolBinding {
    pub descriptor: TypeScriptTool,
    pub target: ToolTarget,
}

pub(crate) fn direct_routes(config: &HarnessConfig) -> Vec<(String, ToolTarget)> {
    let mut routes = file_system::tools(config.settings.tools.command_environment)
        .into_iter()
        .map(|spec| {
            (
                spec.direct_name.to_string(),
                ToolTarget::RuntimeBuiltin {
                    name: spec.name.runtime_tool(),
                },
            )
        })
        .collect::<Vec<_>>();
    let skill_name = skills::ToolName::Read;
    routes.push((
        skill_name.direct_name().to_string(),
        ToolTarget::RuntimeBuiltin {
            name: skill_name.runtime_tool(),
        },
    ));
    routes.extend(config.tool_groups().flat_map(|group| {
        group
            .tools
            .iter()
            .filter(|tool| tool.exposure.is_direct())
            .map(|tool| {
                (
                    tool.name.clone(),
                    ToolTarget::Provided {
                        group_name: group.name.clone(),
                        tool_name: tool.name.clone(),
                    },
                )
            })
    }));
    routes
}

fn filesystem_tools(environment: CommandEnvironment) -> Vec<ToolBinding> {
    file_system::tools(environment)
        .into_iter()
        .map(|spec| {
            let name = spec.direct_name;
            ToolBinding {
                descriptor: TypeScriptTool {
                    name: name.to_string(),
                    programmatic_name: ProgrammaticName {
                        namespace: file_system::NAMESPACE.to_string(),
                        name: name.to_string(),
                    },
                    description: spec.description.to_string(),
                    input_schema: spec.input_schema,
                    output_schema: Some(spec.output_schema),
                },
                target: ToolTarget::RuntimeBuiltin {
                    name: spec.name.runtime_tool(),
                },
            }
        })
        .collect()
}

fn subagent_tools() -> Vec<ToolBinding> {
    subagents::tools()
        .into_iter()
        .map(|spec| ToolBinding {
            descriptor: TypeScriptTool {
                name: spec.name.sandbox_name().to_string(),
                programmatic_name: ProgrammaticName {
                    namespace: subagents::NAMESPACE.to_string(),
                    name: spec.name.function_name().to_string(),
                },
                description: spec.description.to_string(),
                input_schema: spec.input_schema,
                output_schema: Some(spec.output_schema),
            },
            target: ToolTarget::RuntimeBuiltin {
                name: spec.name.runtime_tool(),
            },
        })
        .collect()
}

fn background_process_tools() -> Vec<ToolBinding> {
    background_processes::tools()
        .into_iter()
        .map(|spec| ToolBinding {
            descriptor: TypeScriptTool {
                name: spec.name.sandbox_name().to_string(),
                programmatic_name: ProgrammaticName {
                    namespace: background_processes::NAMESPACE.to_string(),
                    name: spec.name.function_name().to_string(),
                },
                description: spec.description.to_string(),
                input_schema: spec.input_schema,
                output_schema: Some(spec.output_schema),
            },
            target: ToolTarget::RuntimeBuiltin {
                name: spec.name.runtime_tool(),
            },
        })
        .collect()
}

fn skill_tool() -> ToolBinding {
    let spec = skills::tool();
    ToolBinding {
        descriptor: TypeScriptTool {
            name: spec.name.direct_name().to_string(),
            programmatic_name: ProgrammaticName {
                namespace: skills::NAMESPACE.to_string(),
                name: spec.name.function_name().to_string(),
            },
            description: spec.description.to_string(),
            input_schema: spec.input_schema,
            output_schema: Some(spec.output_schema),
        },
        target: ToolTarget::RuntimeBuiltin {
            name: spec.name.runtime_tool(),
        },
    }
}

pub(crate) fn direct_tools(config: &HarnessConfig) -> Vec<ToolDefinition> {
    let mut tools = Vec::new();
    tools.extend(
        filesystem_tools(config.settings.tools.command_environment)
            .into_iter()
            .map(|tool| ToolDefinition {
                name: tool.descriptor.name,
                description: tool.descriptor.description,
                parameters: tool.descriptor.input_schema,
            }),
    );
    if let Some(spec) = skills::direct_tool(config.skills()) {
        tools.push(ToolDefinition {
            name: spec.name.direct_name().to_string(),
            description: spec.description.to_string(),
            parameters: spec.input_schema,
        });
    }
    tools.extend(config.tool_groups().flat_map(|group| {
        group
            .tools
            .iter()
            .filter(|tool| tool.exposure.is_direct())
            .map(|tool| ToolDefinition {
                name: tool.name.clone(),
                description: tool.description.clone(),
                parameters: tool.input_schema.clone(),
            })
    }));
    tools
}

pub(crate) fn is_reserved_group_name(name: &str) -> bool {
    name == file_system::NAMESPACE || name == subagents::NAMESPACE
}

pub(crate) fn is_reserved_direct_name(environment: CommandEnvironment, name: &str) -> bool {
    file_system::is_direct_name(environment, name)
        || background_processes::is_sandbox_name(name)
        || skills::is_direct_name(name)
        || name == "sleep"
}

/// Hookable tool identities used by Runtime hook-selector planning.
///
/// Feature modules remain the source of built-in identities and availability.
pub(crate) fn hook_tool_catalog(config: &HarnessConfig) -> Vec<HookToolKey> {
    let mut tools = filesystem_tools(config.settings.tools.command_environment);
    tools.extend(self_tools());
    // Unconditional, unlike the model-facing list: skills have no enable/disable setting,
    // and a hook scoped to `skill` must not disappear from the catalogue just because the
    // project has not declared a skill yet.
    tools.push(skill_tool());
    if config.settings.tools.background_processes.is_enabled() {
        tools.extend(background_process_tools());
    }
    if config.settings.tools.subagents.is_enabled() {
        tools.extend(subagent_tools());
    }
    let mut keys = tools
        .into_iter()
        .map(|tool| tool.target.hook_tool_key())
        .collect::<BTreeSet<_>>();
    keys.extend(config.tool_groups().flat_map(|group| {
        group.tools.iter().map(|tool| HookToolKey {
            target: HookToolTarget::Provided,
            qualified_name: format!("{}.{}", group.name, tool.name),
        })
    }));
    keys.into_iter().collect()
}

pub(super) fn non_filesystem_runtime_builtin_tools() -> Vec<ToolBinding> {
    let mut tools = subagent_tools();
    tools.extend(self_tools());
    tools.push(skill_tool());
    tools.extend(background_process_tools());
    tools
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::capabilities::{HarnessCapabilitySet, PluginContextDefinition};
    use crate::core::features::tool_discovery::{SearchMode, SearchRequest};
    use crate::core::tools::external::RuntimeBuiltinToolName;
    use crate::core::tools::resolved::testing::*;
    use serde_json::json;

    ///
    /// *Prepare*: Build the Core-owned top-level tool catalog.
    /// *Do*: Select the model-visible `edit` definition.
    /// *Assert*: The exact structured example and every argument's search semantics reach the
    /// model-facing definition.
    ///
    #[test]
    fn edit_direct_tool_exposes_exact_argument_guidance() {
        // Prepare
        let tools = direct_tools(&config());

        // Do
        let tool = tools
            .iter()
            .find(|tool| tool.name == "edit")
            .expect("Core exposes edit directly");

        // Assert
        assert_eq!(
            tool.description,
            r#"Exact string replacement in a file.

Read the current file first. `old_string` must match exactly, including whitespace, indentation, and line endings. With `replace_all=false` (the default), `old_string` must occur exactly once; set `replace_all=true` only to replace every occurrence. If an edit fails, re-read the file before retrying.

Example:
{"file_path":"src/config.ts","old_string":"const mode = \"old\";","new_string":"const mode = \"new\";","replace_all":false}"#
        );
        assert_eq!(
            tool.parameters["required"],
            json!(["file_path", "old_string", "new_string"])
        );
        assert_eq!(
            tool.parameters["properties"]["file_path"]["description"],
            "Path to the existing text file to edit. Use `file_path`; `path` is not accepted."
        );
        assert_eq!(
            tool.parameters["properties"]["old_string"]["description"],
            "Exact text to find, including whitespace, indentation, and line endings. It must occur exactly once unless `replace_all` is true."
        );
        assert_eq!(
            tool.parameters["properties"]["new_string"]["description"],
            "Exact replacement text for `old_string`. It may be empty to delete the matched text, but it must differ from `old_string`."
        );
        assert_eq!(
            tool.parameters["properties"]["replace_all"]["description"],
            "When false (the default), replace the single `old_string` match. When true, replace every occurrence of `old_string`."
        );
        assert!(tool.parameters["properties"].get("content").is_none());
    }

    ///
    /// *Prepare*: Disable optional process and subagent tools in an otherwise empty configuration.
    /// *Do*: Build the Core hook-tool catalog, then enable both optional features and rebuild it.
    /// *Assert*: Always-available tools remain stable and optional tools follow their settings.
    ///
    #[test]
    fn hook_tool_catalog_uses_core_feature_availability() {
        // Prepare
        let mut config = config();
        config.settings.tools.background_processes = BackgroundProcessMode::Disabled;
        config.settings.tools.subagents = SubagentMode::Disabled;

        // Do
        let baseline = hook_tool_catalog(&config);
        config.settings.tools.background_processes = BackgroundProcessMode::Enabled;
        config.settings.tools.subagents = SubagentMode::Enabled;
        let enabled = hook_tool_catalog(&config);

        // Assert
        assert_eq!(
            baseline,
            vec![
                HookToolKey {
                    target: HookToolTarget::SelfTool,
                    qualified_name: "self.sleep".to_string(),
                },
                HookToolKey {
                    target: HookToolTarget::Filesystem,
                    qualified_name: "file_system.bash".to_string(),
                },
                HookToolKey {
                    target: HookToolTarget::Filesystem,
                    qualified_name: "file_system.read_file".to_string(),
                },
                HookToolKey {
                    target: HookToolTarget::Filesystem,
                    qualified_name: "file_system.search_replace".to_string(),
                },
                HookToolKey {
                    target: HookToolTarget::Filesystem,
                    qualified_name: "file_system.write_file".to_string(),
                },
                HookToolKey {
                    target: HookToolTarget::Skill,
                    qualified_name: "skill.read".to_string(),
                },
            ]
        );
        for expected in [
            "process.start",
            "process.output",
            "process.write",
            "process.list",
            "process.stop",
            "subagent.list",
            "subagent.spawn",
            "subagent.wait",
            "subagent.send_message",
            "subagent.interrupt",
            "subagent.stop",
        ] {
            assert!(enabled.iter().any(|key| key.qualified_name == expected));
        }
    }

    ///
    /// *Prepare*: Configure one root-provided tool and one plugin-provided tool.
    /// *Do*: Build the Core hook-tool catalog.
    /// *Assert*: Both provided tools appear as deterministically ordered exact identities.
    ///
    #[test]
    fn hook_tool_catalog_includes_root_and_plugin_provided_tools() {
        // Prepare
        let mut config = config();
        config.capabilities.tool_groups = vec![programmatic_group(
            "calendar",
            "Calendar tools",
            [("list_events", "List events".to_string())],
        )];
        config.plugins = vec![PluginContextDefinition {
            name: "productivity".to_string(),
            description: "Productivity plugin".to_string(),
            path: "/plugins/productivity".to_string(),
            capabilities: HarnessCapabilitySet {
                tool_groups: vec![programmatic_group(
                    "productivity",
                    "Productivity tools",
                    [("lookup", "Look up data".to_string())],
                )],
                ..HarnessCapabilitySet::default()
            },
        }];

        // Do
        let provided = hook_tool_catalog(&config)
            .into_iter()
            .filter(|key| key.target == HookToolTarget::Provided)
            .collect::<Vec<_>>();

        // Assert
        assert_eq!(
            provided,
            vec![
                HookToolKey {
                    target: HookToolTarget::Provided,
                    qualified_name: "calendar.list_events".to_string(),
                },
                HookToolKey {
                    target: HookToolTarget::Provided,
                    qualified_name: "productivity.lookup".to_string(),
                },
            ]
        );
    }

    #[test]
    fn runtime_builtin_names_round_trip_through_their_live_wire_encoding() {
        // Prepare
        let names = file_system::runtime_contracts()
            .into_iter()
            .map(|contract| contract.name)
            .chain(non_filesystem_runtime_builtin_tools().into_iter().map(
                |tool| match tool.target {
                    ToolTarget::RuntimeBuiltin { name } => name,
                    ToolTarget::Provided { .. } => {
                        unreachable!("resolved built-in tools contain no provided tools")
                    }
                },
            ))
            .collect::<Vec<_>>();

        // Do
        let round_tripped = names
            .iter()
            .map(|name| {
                let encoded = serde_json::to_string(name).unwrap();
                serde_json::from_str::<RuntimeBuiltinToolName>(&encoded).unwrap()
            })
            .collect::<Vec<_>>();

        // Assert
        assert_eq!(round_tripped, names);
    }

    #[test]
    fn details_return_exact_filesystem_declarations() {
        let config = config();
        let result = search(
            &config,
            SearchRequest {
                mode: SearchMode::Details,
                functions: vec![
                    "file_system.read_file".to_string(),
                    "file_system.edit".to_string(),
                ],
                ..SearchRequest::default()
            },
        );

        assert_eq!(result.matches("// Result ").count(), 2);
        assert!(result.contains("function read_file"));
        assert!(result.contains("function edit"));
        assert!(!result.contains("function search_replace"));
        assert!(result.contains("offset?: number"));
        assert!(result.contains("old_string: string"));
        assert!(!result.contains("old_str: string"));
        assert!(result.contains("type ReadFileReturned = {"));
        assert!(result.contains("Promise<ReadFileReturned>"));
    }
}
