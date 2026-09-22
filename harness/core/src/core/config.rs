use crate::core::error::CoreError;
use serde::Deserialize;
use serde::Serialize;
use serde_json::Value;

use crate::core::capabilities::HarnessCapabilitySet;
use crate::core::capabilities::PluginContextDefinition;
use crate::core::features::background_processes;
use crate::core::features::compaction::CompactionPolicy;
use crate::core::features::large_output;
use crate::core::features::programmatic_tool_calling;
use crate::core::features::subagents;
use crate::core::tools::command_environment::CommandEnvironment;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub(crate) struct ProvidedToolDefinition {
    pub name: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub input_schema: Value,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub output_schema: Option<Value>,
    pub exposure: ProvidedToolExposure,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum ProvidedToolExposure {
    Programmatic,
    Direct,
    DirectAndProgrammatic,
}

impl ProvidedToolExposure {
    pub(crate) fn is_programmatic(self) -> bool {
        matches!(self, Self::Programmatic | Self::DirectAndProgrammatic)
    }

    pub(crate) fn is_direct(self) -> bool {
        matches!(self, Self::Direct | Self::DirectAndProgrammatic)
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum ToolGroupMetadata {
    Connector { connector_id: String },
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub(crate) struct ToolGroupDefinition {
    pub name: String,
    #[serde(default)]
    pub description: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub metadata: Option<ToolGroupMetadata>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub icon_url: Option<String>,
    #[serde(default)]
    pub tools: Vec<ProvidedToolDefinition>,
}

impl ToolGroupDefinition {
    pub(crate) fn runtime_name_for(group_name: &str, tool_name: &str) -> String {
        crate::core::tools::external::provided_tool_runtime_name(group_name, tool_name)
    }

    pub(crate) fn runtime_name(&self, tool: &ProvidedToolDefinition) -> String {
        Self::runtime_name_for(&self.name, &tool.name)
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(crate) struct TurnSettings {
    pub max_iterations: Option<u32>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(crate) struct ContextSettings {
    pub compaction: CompactionPolicy,
    #[serde(default, skip_serializing_if = "ImageDeliverySettings::is_native")]
    pub image_delivery: ImageDeliverySettings,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum ImageDeliveryMode {
    #[default]
    Native,
    ResourceLink,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(crate) struct ImageDeliverySettings {
    #[serde(default)]
    pub agent: ImageDeliveryMode,
    #[serde(default)]
    pub compaction: ImageDeliveryMode,
}

impl ImageDeliverySettings {
    fn is_native(&self) -> bool {
        self.agent == ImageDeliveryMode::Native && self.compaction == ImageDeliveryMode::Native
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(crate) struct ToolSettings {
    pub programmatic: programmatic_tool_calling::Settings,
    pub large_output: large_output::Policy,
    pub subagents: subagents::Mode,
    pub background_processes: background_processes::Mode,
    pub command_environment: CommandEnvironment,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(crate) struct HarnessSettings {
    pub turn: TurnSettings,
    pub context: ContextSettings,
    pub tools: ToolSettings,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub(crate) struct HarnessConfig {
    pub task_id: String,
    #[serde(default)]
    pub system_instructions: String,
    pub settings: HarnessSettings,
    #[serde(default)]
    pub capabilities: HarnessCapabilitySet,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub skill_catalog_fingerprint: Option<String>,
    #[serde(default)]
    pub plugins: Vec<PluginContextDefinition>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub(crate) struct HarnessConfigUpdate {
    #[serde(default)]
    pub system_instructions: Option<String>,
    #[serde(default)]
    pub settings: Option<HarnessSettings>,
    #[serde(default)]
    pub capabilities: Option<HarnessCapabilitySet>,
    #[serde(default)]
    pub skill_catalog_fingerprint: Option<String>,
    #[serde(default)]
    pub plugins: Option<Vec<PluginContextDefinition>>,
}

pub(crate) fn validate_settings(settings: &HarnessSettings) -> Result<(), CoreError> {
    if settings.turn.max_iterations == Some(0) {
        return Err(CoreError::invalid_configuration(
            "settings.turn.max_iterations",
            "settings.turn.max_iterations must be greater than zero",
        ));
    }
    settings.context.compaction.validate()?;
    settings.tools.programmatic.validate()?;
    settings.tools.large_output.validate()?;
    if settings.tools.background_processes.is_enabled()
        && !settings.tools.command_environment.is_enabled()
    {
        return Err(CoreError::invalid_configuration(
            "settings.tools.background_processes",
            "background processes require an enabled command environment",
        ));
    }
    Ok(())
}

pub(crate) fn validate_skill_catalog_fingerprint(
    skill_catalog_fingerprint: Option<&str>,
) -> Result<(), CoreError> {
    let Some(fingerprint) = skill_catalog_fingerprint else {
        return Ok(());
    };
    if fingerprint.len() != 64
        || !fingerprint
            .bytes()
            .all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
    {
        return Err(CoreError::invalid_configuration(
            "skill_catalog_fingerprint",
            "skill catalog fingerprint must be a 64-character hexadecimal SHA-256 digest",
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::testing::*;

    #[test]
    fn nested_settings_reject_unknown_fields() {
        let mut value = serde_json::to_value(config()).unwrap();
        value["settings"]["tools"]["unexpected"] = json!(true);

        let error = serde_json::from_value::<HarnessConfig>(value).unwrap_err();
        assert!(error.to_string().contains("unknown field `unexpected`"));
    }

    #[test]
    fn omitted_command_environment_is_rejected() {
        // Prepare
        let mut value = serde_json::to_value(config()).unwrap();
        value["settings"]["tools"]
            .as_object_mut()
            .unwrap()
            .remove("command_environment");

        // Do
        let error = serde_json::from_value::<HarnessConfig>(value).unwrap_err();

        // Assert
        assert!(
            error
                .to_string()
                .contains("missing field `command_environment`")
        );
    }

    #[test]
    fn max_iterations_may_be_null_or_omitted() {
        // Prepare
        let mut unlimited = serde_json::to_value(config()).unwrap();
        unlimited["settings"]["turn"]["max_iterations"] = Value::Null;
        let mut omitted = unlimited.clone();
        omitted["settings"]["turn"]
            .as_object_mut()
            .unwrap()
            .remove("max_iterations");

        // Do
        let parsed = serde_json::from_value::<HarnessConfig>(unlimited).unwrap();
        let parsed_omitted = serde_json::from_value::<HarnessConfig>(omitted).unwrap();

        // Assert
        assert_eq!(parsed.settings.turn.max_iterations, None);
        assert_eq!(parsed_omitted.settings.turn.max_iterations, None);
    }

    #[test]
    fn background_processes_require_a_command_environment() {
        // Prepare
        let mut config = config();
        config.settings.tools.command_environment = CommandEnvironment::Disabled;
        config.settings.tools.background_processes = background_processes::Mode::Enabled;

        // Do
        let error = initial_state(config).unwrap_err();

        // Assert
        assert_eq!(
            error.detail(),
            "background processes require an enabled command environment"
        );
    }

    #[test]
    fn automatic_compaction_requires_a_positive_threshold() {
        let mut config = config();
        config.settings.context.compaction = CompactionPolicy::Automatic { token_threshold: 0 };

        assert_eq!(
            initial_state(config).unwrap_err().detail(),
            "automatic compaction token_threshold must be greater than zero"
        );
    }

    #[test]
    fn skill_catalog_fingerprint_must_be_a_lowercase_sha256_digest() {
        let mut valid_config = config();
        valid_config.skill_catalog_fingerprint = Some("a".repeat(64));
        assert!(initial_state(valid_config).is_ok());

        let mut invalid_config = config();
        invalid_config.skill_catalog_fingerprint = Some("A".repeat(64));
        assert_eq!(
            initial_state(invalid_config).unwrap_err().detail(),
            "skill catalog fingerprint must be a 64-character hexadecimal SHA-256 digest"
        );

        let mut non_hex_config = config();
        non_hex_config.skill_catalog_fingerprint = Some("z".repeat(64));
        assert_eq!(
            initial_state(non_hex_config).unwrap_err().detail(),
            "skill catalog fingerprint must be a 64-character hexadecimal SHA-256 digest"
        );
    }

    #[test]
    fn duplicate_or_reserved_direct_tool_names_are_rejected() {
        let direct_tool = |name: &str| ProvidedToolDefinition {
            name: name.to_string(),
            description: String::new(),
            input_schema: json!({"type": "object"}),
            output_schema: None,
            exposure: ProvidedToolExposure::Direct,
        };
        let mut duplicate_config = config();
        duplicate_config.capabilities.tool_groups = vec![
            ToolGroupDefinition {
                name: "first".to_string(),
                description: String::new(),
                metadata: None,
                icon_url: None,
                tools: vec![direct_tool("choose")],
            },
            ToolGroupDefinition {
                name: "second".to_string(),
                description: String::new(),
                metadata: None,
                icon_url: None,
                tools: vec![direct_tool("choose")],
            },
        ];
        assert_eq!(
            initial_state(duplicate_config).unwrap_err().detail(),
            "ambiguous duplicate direct tool name \"choose\""
        );

        let mut config = config();
        config.capabilities.tool_groups = vec![ToolGroupDefinition {
            name: "client".to_string(),
            description: String::new(),
            metadata: None,
            icon_url: None,
            tools: vec![direct_tool("bash")],
        }];
        assert_eq!(
            initial_state(config).unwrap_err().detail(),
            "direct tool name \"bash\" conflicts with a built-in tool"
        );
    }
}
