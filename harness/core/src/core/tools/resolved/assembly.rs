//! Cross-feature assembly for the tools available in one accepted session
//! configuration.

use std::collections::BTreeSet;

use crate::core::config::{HarnessConfig, ImageDeliveryMode};
use crate::core::features::background_processes;
use crate::core::features::file_system;
use crate::core::features::programmatic_tool_calling::{ProgrammaticName, TypeScriptTool};
use crate::core::features::skills;
use crate::core::features::subagents;
use crate::core::hooks::{HookToolKey, HookToolTarget};
use crate::core::step_protocol::ToolDefinition;
use crate::core::tools::command_environment::CommandEnvironment;
use crate::core::tools::external::ToolTarget;
use crate::core::tools::external::provided_tool_qualified_name;
use crate::core::tools::resolved::self_tools::self_tools;

pub(crate) const SELF_NAMESPACE: &str = "self";

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct ToolBinding {
    pub descriptor: TypeScriptTool,
    pub target: ToolTarget,
}

pub(crate) fn direct_routes(config: &HarnessConfig) -> Vec<(String, ToolTarget)> {
    let mut routes = file_system_specs(config)
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
    if crate::core::features::model_wait::is_offered(&config.settings.tools) {
        routes.push((
            "wait".to_string(),
            ToolTarget::RuntimeBuiltin {
                name: crate::core::tools::external::RuntimeBuiltinToolName::SelfWait,
            },
        ));
    }
    if !config.settings.tools.skills.is_disabled() {
        let skill_name = skills::ToolName::Read;
        routes.push((
            skill_name.direct_name().to_string(),
            ToolTarget::RuntimeBuiltin {
                name: skill_name.runtime_tool(),
            },
        ));
    }
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

/// The file system tool specs the Session exposes, or none when disabled.
fn file_system_specs(config: &HarnessConfig) -> Vec<file_system::ToolSpec> {
    if !config.settings.tools.file_system.is_enabled() {
        return Vec::new();
    }
    file_system::exposed_tools(
        config.settings.tools.command_environment,
        bash_timeout(config),
        config.settings.tools.background_processes.is_enabled(),
        direct_read_file_support(config),
        file_system::Exposure::Direct,
    )
}

pub(super) fn bash_timeout(config: &HarnessConfig) -> file_system::BashTimeout {
    file_system::BashTimeout::from_settings(config.settings.tools.bash_timeout.as_ref())
}

/// A model without image input would only receive `[Image]`, so the direct
/// `read_file` description leaves the `native` image sentence out for it.
fn direct_read_file_support(config: &HarnessConfig) -> file_system::ReadFileSupport {
    let support = config.settings.tools.file_system.read_file();
    let shows_images =
        config.settings.context.effective_image_delivery().agent == ImageDeliveryMode::Native;
    if support.images == file_system::ReadFileImages::Native && !shows_images {
        return file_system::ReadFileSupport {
            images: file_system::ReadFileImages::Unsupported,
            ..support
        };
    }
    support
}

fn session_filesystem_tools(config: &HarnessConfig) -> Vec<ToolBinding> {
    if !config.settings.tools.file_system.is_enabled() {
        return Vec::new();
    }
    filesystem_tools(
        config.settings.tools.command_environment,
        bash_timeout(config),
        config.settings.tools.background_processes.is_enabled(),
        direct_read_file_support(config),
    )
}

fn filesystem_tools(
    environment: CommandEnvironment,
    timeout: file_system::BashTimeout,
    background: bool,
    read_file: file_system::ReadFileSupport,
) -> Vec<ToolBinding> {
    let exposure = file_system::Exposure::Direct;
    file_system::exposed_tools(environment, timeout, background, read_file, exposure)
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
                    description: spec.description,
                    input_schema: spec.input_schema,
                    output_schema: Some(spec.output_schema),
                    runtime_builtin: Some(spec.name.runtime_tool()),
                },
                target: ToolTarget::RuntimeBuiltin {
                    name: spec.name.runtime_tool(),
                },
            }
        })
        .collect()
}

fn subagent_tools(mode: &subagents::Mode, prompts: &subagents::Prompts) -> Vec<ToolBinding> {
    subagents::tools(mode.supports_conversation_forking(), prompts)
        .into_iter()
        .filter(|spec| mode.supports(spec.name))
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
                runtime_builtin: Some(spec.name.runtime_tool()),
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
                runtime_builtin: Some(spec.name.runtime_tool()),
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
            runtime_builtin: Some(spec.name.runtime_tool()),
        },
        target: ToolTarget::RuntimeBuiltin {
            name: spec.name.runtime_tool(),
        },
    }
}

pub(crate) fn builtin_direct_tools(config: &HarnessConfig) -> Vec<ToolDefinition> {
    let mut tools = Vec::new();
    if crate::core::features::model_wait::is_offered(&config.settings.tools) {
        tools.push(crate::core::features::model_wait::tool(
            config.settings.tools.max_wait_ms,
            &config.settings.prompts.wait_description,
        ));
    }
    tools.extend(
        session_filesystem_tools(config)
            .into_iter()
            .map(|tool| ToolDefinition {
                name: tool.descriptor.name,
                description: tool.descriptor.description,
                parameters: tool.descriptor.input_schema,
            }),
    );
    if let Some(spec) = skills::direct_tool(config.settings.tools.skills, config.skills()) {
        tools.push(ToolDefinition {
            name: spec.name.direct_name().to_string(),
            description: spec.description.to_string(),
            parameters: spec.input_schema,
        });
    }
    tools
}

pub(crate) const RESERVED_TOOL_NAMESPACES: [&str; 5] = [
    SELF_NAMESPACE,
    file_system::NAMESPACE,
    skills::NAMESPACE,
    background_processes::NAMESPACE,
    subagents::NAMESPACE,
];

/// Direct names a caller tool cannot take. `skill` stays free while skills are disabled, so a
/// caller can declare its own; a Session that may gain skills later keeps it reserved.
pub(crate) fn is_reserved_direct_name(
    environment: CommandEnvironment,
    skill_mode: skills::Mode,
    name: &str,
) -> bool {
    file_system::is_direct_name(environment, name)
        || background_processes::is_sandbox_name(name)
        || (!skill_mode.is_disabled() && skills::is_direct_name(name))
        || name == "sleep"
        || name == "wait"
}

/// Hookable tool identities used by Runtime hook-selector planning.
///
/// Feature modules remain the source of built-in identities and availability.
pub(crate) fn hook_tool_catalog(config: &HarnessConfig) -> Vec<HookToolKey> {
    let mut tools = session_filesystem_tools(config);
    if config.settings.tools.programmatic.offers_sleep() {
        tools.extend(self_tools(config.settings.tools.programmatic.max_sleep_ms));
    }
    // Present unless skills are disabled, unlike the model-facing list: a hook scoped to
    // `skill` must not disappear from the catalogue just because the project has not
    // declared a skill yet.
    if !config.settings.tools.skills.is_disabled() {
        tools.push(skill_tool());
    }
    if config.settings.tools.background_processes.is_enabled() {
        tools.extend(background_process_tools());
    }
    if config.settings.tools.subagents.is_enabled() {
        tools.extend(subagent_tools(
            &config.settings.tools.subagents,
            &config.settings.prompts.agent_tools,
        ));
    }
    let mut keys = tools
        .into_iter()
        .map(|tool| tool.target.hook_tool_key())
        .collect::<BTreeSet<_>>();
    if crate::core::features::model_wait::is_offered(&config.settings.tools) {
        keys.insert(
            ToolTarget::RuntimeBuiltin {
                name: crate::core::tools::external::RuntimeBuiltinToolName::SelfWait,
            }
            .hook_tool_key(),
        );
    }
    keys.extend(config.tool_groups().flat_map(|group| {
        group.tools.iter().map(|tool| HookToolKey {
            target: HookToolTarget::Provided,
            qualified_name: provided_tool_qualified_name(&group.name, &tool.name),
        })
    }));
    keys.into_iter().collect()
}

/// Every built-in except the file system ones. Subagent tools are listed even
/// when the session has them off; `conversation_forking` follows the session,
/// so a pre-tool hook's result is checked against the spawn schema the model
/// was offered.
pub(super) fn non_filesystem_runtime_builtin_tools(
    max_sleep_ms: u64,
    conversation_forking: bool,
) -> Vec<ToolBinding> {
    let mut tools = subagent_tools(
        &subagents::Mode::enabled().with_conversation_forking(conversation_forking),
        &crate::core::prompt::PromptSettings::default().agent_tools,
    );
    tools.extend(self_tools(max_sleep_ms));
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
        let tools = builtin_direct_tools(&config());

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
    /// *Prepare*: A Unix configuration with background processes disabled, then enabled.
    /// *Do*: Select the model-visible `bash` definition from each.
    /// *Assert*: Only the enabled configuration tells the model to start long work in
    /// the background, in the description and in `timeout_seconds`.
    ///
    #[test]
    fn bash_advises_backgrounding_only_when_background_processes_are_enabled() {
        // Prepare
        let mut config = config();
        config.settings.tools.background_processes = BackgroundProcessMode::Disabled;
        let mut background = config.clone();
        background.settings.tools.background_processes = BackgroundProcessMode::Enabled;

        // Do
        let bash = |config: &HarnessConfig| {
            builtin_direct_tools(config)
                .into_iter()
                .find(|tool| tool.name == "bash")
                .expect("Unix exposes bash")
        };
        let (foreground, background) = (bash(&config), bash(&background));

        // Assert
        for (tool, advised) in [(&foreground, false), (&background, true)] {
            let timeout = tool.parameters["properties"]["timeout_seconds"]["description"]
                .as_str()
                .expect("the timeout is described");
            assert_eq!(tool.description.contains("tools.process.start"), advised);
            assert_eq!(timeout.contains("tools.process.start"), advised);
        }
    }

    ///
    /// *Prepare*: Sessions that declare extracted document text with native images, and the
    /// text found in images alone.
    /// *Do*: Compile their tools and select the direct `read_file` definition and the
    /// `tools.file_system.read_file` declaration.
    /// *Assert*: Each description adds one sentence per declared value, documents first, and
    /// the declaration a program calls leaves out the native image sentence.
    ///
    #[test]
    fn declared_read_file_support_extends_the_read_file_descriptions() {
        // Prepare
        let with_read_file = |read_file: file_system::ReadFileSupport| {
            let mut config = config();
            config.settings.tools.file_system = file_system::Mode::Enabled { read_file };
            config
        };
        let documents_and_native_images = with_read_file(file_system::ReadFileSupport {
            documents: file_system::ReadFileDocuments::ExtractedText,
            images: file_system::ReadFileImages::Native,
        });
        let image_text = with_read_file(file_system::ReadFileSupport {
            images: file_system::ReadFileImages::ExtractedText,
            ..Default::default()
        });

        // Do
        let descriptions = |config: &HarnessConfig| {
            let tools = crate::core::tools::resolved::ResolvedTools::compile(config)
                .expect("the configuration compiles");
            let direct = tools
                .top_level_tools()
                .iter()
                .find(|tool| tool.name == "read_file")
                .expect("Core exposes read_file directly")
                .description
                .clone();
            let programmatic = tools
                .program_descriptors()
                .iter()
                .find(|tool| {
                    tool.programmatic_name.namespace == file_system::NAMESPACE
                        && tool.programmatic_name.name == "read_file"
                })
                .expect("Core declares tools.file_system.read_file")
                .description
                .clone();
            (direct, programmatic)
        };
        let documents_and_native_images = descriptions(&documents_and_native_images);
        let image_text = descriptions(&image_text);

        // Assert
        let text = "Read a text file with optional zero-indexed line offset and limit. When `was_truncated` is true, read on from `next_offset`. An offset past the end of the file is an error. A directory reads as a listing of its entries, one per line with `/` after each subdirectory, at most 500 at a time.";
        let documents = "PDF, Word, PowerPoint, and email files return their extracted text.";
        assert_eq!(
            documents_and_native_images,
            (
                format!("{text} {documents} An image file returns the image itself."),
                format!("{text} {documents}"),
            )
        );
        let image_text_description =
            format!("{text} An image file returns only the text found in the image.");
        assert_eq!(
            image_text,
            (image_text_description.clone(), image_text_description)
        );
    }

    #[test]
    fn builtin_catalog_should_be_covered_by_reserved_tool_namespaces() {
        let bindings = filesystem_tools(
            CommandEnvironment::Unix,
            file_system::BashTimeout::default(),
            true,
            file_system::ReadFileSupport::default(),
        )
        .into_iter()
        .chain(self_tools(30_000))
        .chain(std::iter::once(skill_tool()))
        .chain(background_process_tools())
        .chain(subagent_tools(
            &subagents::Mode::enabled(),
            &crate::core::prompt::PromptSettings::default().agent_tools,
        ))
        .collect::<Vec<_>>();
        let mut stable_ids = BTreeSet::new();
        let mut namespaces = BTreeSet::new();
        for binding in &bindings {
            let stable_id = binding.target.hook_tool_key().qualified_name;
            assert!(
                stable_ids.insert(stable_id.clone()),
                "duplicate built-in ID {stable_id}"
            );
            namespaces.insert(stable_id.split('.').next().unwrap().to_string());
            namespaces.insert(binding.descriptor.programmatic_name.namespace.clone());
        }

        assert_eq!(
            namespaces,
            RESERVED_TOOL_NAMESPACES
                .into_iter()
                .map(str::to_string)
                .collect()
        );
    }

    ///
    /// *Prepare*: Disable optional process and subagent tools in an otherwise empty configuration.
    /// *Do*: Build the Core hook-tool catalog, then enable both optional features and wait, and rebuild it.
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
        config.settings.tools.subagents = SubagentMode::enabled();
        config.settings.tools.model_wait =
            serde_json::from_value(serde_json::json!({"mode": "enabled"})).unwrap();
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
                    qualified_name: "file_system.edit".to_string(),
                },
                HookToolKey {
                    target: HookToolTarget::Filesystem,
                    qualified_name: "file_system.read_file".to_string(),
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
            "agents.list",
            "agents.spawn",
            "agents.send_message",
            "agents.interrupt",
            "self.wait",
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
        let names = file_system::runtime_contracts(file_system::BashTimeout::default())
            .into_iter()
            .map(|contract| contract.name)
            .chain(
                non_filesystem_runtime_builtin_tools(30_000, false)
                    .into_iter()
                    .map(|tool| match tool.target {
                        ToolTarget::RuntimeBuiltin { name } => name,
                        ToolTarget::Provided { .. } => {
                            unreachable!("resolved built-in tools contain no provided tools")
                        }
                    }),
            )
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
