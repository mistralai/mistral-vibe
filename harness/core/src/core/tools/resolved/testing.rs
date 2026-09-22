//! Shared builders for resolved-tool and discovery tests.

pub(crate) use serde_json::json;

use crate::core::features::programmatic_tool_calling::TypeScriptTool;
pub(crate) use crate::core::features::tool_discovery::*;
pub(crate) use crate::core::testing::*;

pub(crate) fn programmatic_group<N>(
    name: &str,
    description: &str,
    tools: impl IntoIterator<Item = (N, String)>,
) -> ToolGroupDefinition
where
    N: Into<String>,
{
    ToolGroupDefinition {
        name: name.to_string(),
        description: description.to_string(),
        metadata: None,
        icon_url: None,
        tools: tools
            .into_iter()
            .map(|(name, description)| ProvidedToolDefinition {
                name: name.into(),
                description,
                input_schema: json!({
                    "type": "object",
                    "properties": {},
                    "additionalProperties": false
                }),
                output_schema: Some(json!({
                    "type": "object",
                    "properties": {},
                    "additionalProperties": false
                })),
                exposure: crate::core::config::ProvidedToolExposure::Programmatic,
            })
            .collect(),
    }
}

pub(crate) fn connector_notice(
    name: &str,
    description: &str,
    connector_id: &str,
) -> ToolGroupDefinition {
    ToolGroupDefinition {
        name: name.to_string(),
        description: description.to_string(),
        metadata: Some(ToolGroupMetadata::Connector {
            connector_id: connector_id.to_string(),
        }),
        icon_url: None,
        tools: Vec::new(),
    }
}

pub(crate) fn target(config: &HarnessConfig, runtime_name: &str) -> Option<ToolTarget> {
    crate::core::tools::resolved::ResolvedTools::compile(config)
        .expect("test resolved-tool configuration is valid")
        .program_target(runtime_name)
}

pub(crate) fn qualified_name(tool: &TypeScriptTool) -> String {
    format!(
        "{}.{}",
        tool.programmatic_name.namespace, tool.programmatic_name.name
    )
}

pub(crate) fn search(config: &HarnessConfig, request: SearchRequest) -> String {
    crate::core::tools::resolved::search_unvalidated_for_test(config, request)
}
