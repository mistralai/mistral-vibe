use std::collections::BTreeMap;
use std::collections::HashSet;

use serde::Deserialize;
use serde::Serialize;

use crate::core::config::{HarnessConfig, ToolGroupDefinition};
use crate::core::features::skills::SkillDefinition;
use crate::core::features::subagents;
use crate::core::hooks::HookPoint;
use crate::core::hooks::HookToolKey;

const MAX_PLUGINS: usize = 128;
const MAX_KNOWLEDGE_FOLDERS: usize = 100;
const MAX_HOOK_BINDINGS: usize = 16_384;
const MAX_HOOK_TOOL_KEYS: usize = 4_096;

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
pub(crate) struct HarnessCapabilitySet {
    #[serde(default)]
    pub tool_groups: Vec<ToolGroupDefinition>,
    #[serde(default)]
    pub skills: Vec<SkillDefinition>,
    #[serde(default)]
    pub knowledge_folders: Vec<KnowledgeFolderDefinition>,
    #[serde(default)]
    pub agent_types: Vec<subagents::AgentTypeDefinition>,
    #[serde(default)]
    pub hook_bindings: Vec<HookBinding>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub(crate) struct HookBinding {
    pub id: String,
    pub point: HookPoint,
    pub order: u32,
    pub selector: HookSelector,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum HookSelector {
    Always,
    ToolKeys { tool_keys: Vec<HookToolKey> },
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct HookBindingIndex {
    always: BTreeMap<HookPoint, Vec<IndexedHookBinding>>,
    by_tool: BTreeMap<(HookPoint, HookToolKey), Vec<IndexedHookBinding>>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct IndexedHookBinding {
    order: u32,
    id: String,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum KnowledgeFolderAccess {
    ReadOnly,
    ReadWrite,
}

impl KnowledgeFolderAccess {
    fn as_str(self) -> &'static str {
        match self {
            Self::ReadOnly => "read_only",
            Self::ReadWrite => "read_write",
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub(crate) struct KnowledgeFolderDefinition {
    pub name: String,
    pub description: String,
    pub path: String,
    pub access: KnowledgeFolderAccess,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub(crate) struct PluginContextDefinition {
    pub name: String,
    pub description: String,
    pub path: String,
    #[serde(default)]
    pub capabilities: HarnessCapabilitySet,
}

impl HarnessConfig {
    pub(crate) fn capability_sets(&self) -> impl Iterator<Item = &HarnessCapabilitySet> {
        std::iter::once(&self.capabilities)
            .chain(self.plugins.iter().map(|plugin| &plugin.capabilities))
    }

    pub(crate) fn tool_groups(&self) -> impl Iterator<Item = &ToolGroupDefinition> {
        self.capability_sets()
            .flat_map(|capabilities| capabilities.tool_groups.iter())
    }

    pub(crate) fn skills(&self) -> impl Iterator<Item = &SkillDefinition> {
        self.capability_sets()
            .flat_map(|capabilities| capabilities.skills.iter())
    }

    pub(crate) fn agent_types(&self) -> impl Iterator<Item = &subagents::AgentTypeDefinition> {
        self.capability_sets()
            .flat_map(|capabilities| capabilities.agent_types.iter())
    }
}

impl HookBindingIndex {
    pub(crate) fn from_config(config: &HarnessConfig) -> Self {
        let mut bindings = config
            .capability_sets()
            .flat_map(|capabilities| capabilities.hook_bindings.iter())
            .collect::<Vec<_>>();
        bindings.sort_by_key(|binding| binding.order);

        let mut index = Self::default();
        for binding in bindings {
            let indexed = IndexedHookBinding {
                order: binding.order,
                id: binding.id.clone(),
            };
            match &binding.selector {
                HookSelector::Always => {
                    index.always.entry(binding.point).or_default().push(indexed)
                }
                HookSelector::ToolKeys { tool_keys } => {
                    for tool_key in tool_keys {
                        index
                            .by_tool
                            .entry((binding.point, tool_key.clone()))
                            .or_default()
                            .push(indexed.clone());
                    }
                }
            }
        }
        index
    }

    pub(crate) fn select(&self, point: HookPoint, tool_key: Option<&HookToolKey>) -> Vec<String> {
        let always = self
            .always
            .get(&point)
            .map(Vec::as_slice)
            .unwrap_or_default();
        let exact = tool_key
            .and_then(|key| self.by_tool.get(&(point, key.clone())))
            .map(Vec::as_slice)
            .unwrap_or_default();
        merge_binding_ids(always, exact)
    }
}

fn merge_binding_ids(always: &[IndexedHookBinding], exact: &[IndexedHookBinding]) -> Vec<String> {
    let mut selected = Vec::with_capacity(always.len() + exact.len());
    let mut always_index = 0;
    let mut exact_index = 0;
    while always_index < always.len() && exact_index < exact.len() {
        if always[always_index].order < exact[exact_index].order {
            selected.push(always[always_index].id.clone());
            always_index += 1;
        } else {
            selected.push(exact[exact_index].id.clone());
            exact_index += 1;
        }
    }
    selected.extend(
        always[always_index..]
            .iter()
            .map(|binding| binding.id.clone()),
    );
    selected.extend(
        exact[exact_index..]
            .iter()
            .map(|binding| binding.id.clone()),
    );
    selected
}

pub(crate) fn validate(config: &HarnessConfig) -> Result<(), String> {
    if config.plugins.len() > MAX_PLUGINS {
        return Err(format!(
            "configuration contains more than {MAX_PLUGINS} plugins"
        ));
    }

    let mut plugin_names = HashSet::new();
    let mut plugin_paths = HashSet::new();
    for plugin in &config.plugins {
        validate_text(&plugin.name, "plugin name")?;
        validate_text(&plugin.description, "plugin description")?;
        validate_text(&plugin.path, "plugin path")?;
        if !plugin_names.insert(plugin.name.as_str()) {
            return Err(format!("duplicate plugin name {:?}", plugin.name));
        }
        if !plugin_paths.insert(plugin.path.as_str()) {
            return Err(format!("duplicate plugin path {:?}", plugin.path));
        }
    }

    let knowledge_folders = config
        .capability_sets()
        .flat_map(|capabilities| capabilities.knowledge_folders.iter())
        .collect::<Vec<_>>();
    if knowledge_folders.len() > MAX_KNOWLEDGE_FOLDERS {
        return Err(format!(
            "configuration contains more than {MAX_KNOWLEDGE_FOLDERS} knowledge folders"
        ));
    }
    let mut folder_names = HashSet::new();
    let mut folder_paths = HashSet::new();
    for folder in knowledge_folders {
        validate_text(&folder.name, "knowledge folder name")?;
        validate_text(&folder.description, "knowledge folder description")?;
        validate_text(&folder.path, "knowledge folder path")?;
        if !folder_names.insert(folder.name.as_str()) {
            return Err(format!("duplicate knowledge folder name {:?}", folder.name));
        }
        if !folder_paths.insert(folder.path.as_str()) {
            return Err(format!("duplicate knowledge folder path {:?}", folder.path));
        }
    }

    subagents::validate_agent_types(config.agent_types())?;
    validate_hook_bindings(config)?;
    Ok(())
}

fn validate_hook_bindings(config: &HarnessConfig) -> Result<(), String> {
    let bindings = config
        .capability_sets()
        .flat_map(|capabilities| capabilities.hook_bindings.iter())
        .collect::<Vec<_>>();
    if bindings.len() > MAX_HOOK_BINDINGS {
        return Err(format!(
            "configuration contains more than {MAX_HOOK_BINDINGS} hook bindings"
        ));
    }

    let mut ids = HashSet::new();
    let mut orders = HashSet::new();
    for binding in bindings {
        validate_text(&binding.id, "hook binding id")?;
        if !ids.insert(binding.id.as_str()) {
            return Err(format!("duplicate hook binding id {:?}", binding.id));
        }
        if !orders.insert(binding.order) {
            return Err(format!("duplicate hook binding order {}", binding.order));
        }
        match (&binding.point, &binding.selector) {
            (
                HookPoint::PreAgentTurn
                | HookPoint::PreLlmCall
                | HookPoint::PostLlmCall
                | HookPoint::PostAgentTurn,
                HookSelector::ToolKeys { .. },
            ) => {
                return Err(format!(
                    "lifecycle hook binding {:?} must use an always selector",
                    binding.id
                ));
            }
            (_, HookSelector::Always) => {}
            (_, HookSelector::ToolKeys { tool_keys }) => {
                if tool_keys.is_empty() {
                    return Err(format!(
                        "tool hook binding {:?} must select at least one tool",
                        binding.id
                    ));
                }
                if tool_keys.len() > MAX_HOOK_TOOL_KEYS {
                    return Err(format!(
                        "hook binding {:?} selects more than {MAX_HOOK_TOOL_KEYS} tools",
                        binding.id
                    ));
                }
                let mut unique = HashSet::new();
                for key in tool_keys {
                    validate_text(&key.qualified_name, "hook tool qualified name")?;
                    if !unique.insert(key) {
                        return Err(format!(
                            "hook binding {:?} contains duplicate tool key {:?}",
                            binding.id, key
                        ));
                    }
                }
            }
        }
    }
    Ok(())
}

pub(crate) fn render_sections(config: &HarnessConfig) -> Vec<String> {
    let mut sections = Vec::new();
    let knowledge = knowledge_folders(config);
    if !knowledge.is_empty() {
        sections.push(render_knowledge(knowledge));
    }
    if config.settings.tools.subagents.is_enabled()
        && let Some(agent_types) = subagents::render_agent_types(config.agent_types())
    {
        sections.push(agent_types);
    }
    if let Some(section) = render_plugins(&config.plugins) {
        sections.push(section);
    }
    sections
}

#[derive(Clone, Copy)]
enum CapabilitySource<'a> {
    Root,
    Plugin(&'a str),
}

fn knowledge_folders(
    config: &HarnessConfig,
) -> Vec<(&KnowledgeFolderDefinition, CapabilitySource<'_>)> {
    config
        .capabilities
        .knowledge_folders
        .iter()
        .map(|folder| (folder, CapabilitySource::Root))
        .chain(config.plugins.iter().flat_map(|plugin| {
            plugin
                .capabilities
                .knowledge_folders
                .iter()
                .map(|folder| (folder, CapabilitySource::Plugin(&plugin.name)))
        }))
        .collect()
}

fn render_knowledge(
    mut folders: Vec<(&KnowledgeFolderDefinition, CapabilitySource<'_>)>,
) -> String {
    folders.sort_by(|(left, _), (right, _)| {
        left.name
            .cmp(&right.name)
            .then_with(|| left.path.cmp(&right.path))
    });
    let entries = folders
        .into_iter()
        .map(|(folder, source)| {
            let source = match source {
                CapabilitySource::Root => r#"    <source>
      <type>standalone</type>
    </source>"#
                    .to_string(),
                CapabilitySource::Plugin(plugin_name) => format!(
                    r#"    <source>
      <type>plugin</type>
      <plugin-name>{}</plugin-name>
    </source>"#,
                    escape_xml(plugin_name)
                ),
            };
            format!(
                r#"  <knowledge-folder>
    <name>{}</name>
    <description>{}</description>
    <path>{}</path>
    <access>{}</access>
{source}
  </knowledge-folder>"#,
                escape_xml(&folder.name),
                escape_xml(&folder.description),
                escape_xml(&folder.path),
                folder.access.as_str(),
            )
        })
        .collect::<Vec<_>>()
        .join("\n");
    format!(
        r#"## Available knowledge folders

Use the descriptions below to identify relevant knowledge folders. Before answering, read the listed `KNOWLEDGE.md` for each relevant folder. Respect each folder's access mode and any product-specific rules.

<available-knowledge-folders>
{entries}
</available-knowledge-folders>"#
    )
}

fn render_plugins(plugins: &[PluginContextDefinition]) -> Option<String> {
    if plugins.is_empty() {
        return None;
    }
    let mut plugins = plugins.iter().collect::<Vec<_>>();
    plugins.sort_by(|left, right| left.name.cmp(&right.name));
    let entries = plugins
        .into_iter()
        .map(|plugin| {
            let namespaces = plugin
                .capabilities
                .tool_groups
                .iter()
                .filter(|group| {
                    group
                        .tools
                        .iter()
                        .any(|tool| tool.exposure.is_programmatic())
                })
                .map(|group| {
                    format!(
                        r#"
    <tool-namespace>{}</tool-namespace>"#,
                        escape_xml(&group.name)
                    )
                })
                .collect::<String>();
            format!(
                r#"  <plugin>
    <name>{}</name>
    <description>{}</description>
    <path>{}</path>{namespaces}
  </plugin>"#,
                escape_xml(&plugin.name),
                escape_xml(&plugin.description),
                escape_xml(&plugin.path),
            )
        })
        .collect::<Vec<_>>()
        .join("\n");
    Some(format!(
        r#"## Available plugins

Plugins are mounted capability packages. Their descriptions are routing context only; load a listed skill for detailed behavioral guidance. When a user names a plugin, use its listed skills or knowledge folders, or discover functions in its tool namespace. Do not assume that the plugin provides capabilities that are not exposed through those surfaces.

<available-plugins>
{entries}
</available-plugins>"#
    ))
}

fn validate_text(value: &str, field: &str) -> Result<(), String> {
    if value.trim().is_empty() {
        return Err(format!("{field} must not be empty"));
    }
    Ok(())
}

fn escape_xml(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn plugin() -> PluginContextDefinition {
        PluginContextDefinition {
            name: "finance".to_string(),
            description: "Finance <tools>".to_string(),
            path: "/plugins/finance".to_string(),
            capabilities: HarnessCapabilitySet {
                knowledge_folders: vec![KnowledgeFolderDefinition {
                    name: "finance:policy".to_string(),
                    description: "Policy <index>".to_string(),
                    path: "/plugins/finance/knowledge/policy/KNOWLEDGE.md".to_string(),
                    access: KnowledgeFolderAccess::ReadWrite,
                }],
                agent_types: vec![subagents::AgentTypeDefinition {
                    name: "finance:researcher".to_string(),
                    description: "Research reports.".to_string(),
                    path: "/plugins/finance/agents/researcher.toml".to_string(),
                }],
                ..HarnessCapabilitySet::default()
            },
        }
    }

    #[test]
    fn renders_and_escapes_capabilities_in_deterministic_sections() {
        let mut config = crate::core::testing::config();
        config.plugins = vec![plugin()];
        let sections = render_sections(&config);
        assert_eq!(sections.len(), 3);
        assert!(sections[0].contains("Policy &lt;index&gt;"));
        assert!(sections[0].contains("<plugin-name>finance</plugin-name>"));
        assert!(sections[1].contains("finance:researcher"));
        assert!(sections[2].contains("Finance &lt;tools&gt;"));
    }

    #[test]
    fn rejects_duplicate_knowledge_across_root_and_plugin_capabilities() {
        let mut config = crate::core::testing::config();
        config.capabilities.knowledge_folders = vec![KnowledgeFolderDefinition {
            name: "finance:policy".to_string(),
            description: "Policy index".to_string(),
            path: "/knowledge/policy/KNOWLEDGE.md".to_string(),
            access: KnowledgeFolderAccess::ReadOnly,
        }];
        config.plugins = vec![plugin()];
        let error = validate(&config).unwrap_err();
        assert!(error.contains("duplicate knowledge folder name"));
    }
}
