use crate::core::error::CoreError;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::core::capabilities::HarnessCapabilitySet;
use crate::core::capabilities::PluginContextDefinition;
use crate::core::features::background_processes;
use crate::core::features::compaction::CompactionPolicy;
use crate::core::features::file_system;
use crate::core::features::large_output;
pub(crate) use crate::core::features::notifications::Settings as NotificationSettings;
pub(crate) use crate::core::features::output_limit_recovery::Settings as OutputLimitRecovery;
use crate::core::features::programmatic_tool_calling;
use crate::core::features::skills;
use crate::core::features::subagents;
use crate::core::prompt::PromptSettings;
use crate::core::tools::command_environment::CommandEnvironment;

use crate::core::features::permissions::PermissionPolicy;

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
    #[serde(default, skip_serializing_if = "OutputLimitRecovery::is_disabled")]
    pub output_limit_recovery: OutputLimitRecovery,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(crate) struct ContextSettings {
    pub compaction: CompactionPolicy,
    pub notifications: NotificationSettings,
    pub model_facts: ModelFacts,
    /// The kinds of input the model that writes compaction summaries reads,
    /// when the host uses a separate model for them. Absent means the agent
    /// model's `model_facts.input_modalities`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub compaction_model_input_modalities: Option<Vec<InputModality>>,
}

impl ContextSettings {
    /// How each completion purpose receives images, from the input modalities
    /// of the model that serves it.
    pub(crate) fn effective_image_delivery(&self) -> ImageDeliverySettings {
        let compaction_modalities = self
            .compaction_model_input_modalities
            .as_deref()
            .unwrap_or(&self.model_facts.input_modalities);
        ImageDeliverySettings {
            agent: ImageDeliveryMode::for_modalities(&self.model_facts.input_modalities),
            compaction: ImageDeliveryMode::for_modalities(compaction_modalities),
        }
    }

    fn validate(&self) -> Result<(), CoreError> {
        self.model_facts.validate()?;
        if let Some(modalities) = &self.compaction_model_input_modalities {
            validate_input_modalities(
                "settings.context.compaction_model_input_modalities",
                modalities,
            )?;
        }
        Ok(())
    }

    /// The model input size that triggers automatic compaction, or `None` when
    /// compaction is disabled.
    pub(crate) fn compaction_threshold(&self) -> Option<u64> {
        match self.compaction {
            CompactionPolicy::Disabled => None,
            CompactionPolicy::Automatic => Some(self.model_facts.compaction_threshold),
        }
    }
}

/// What the host knows about the model, compiled from its completion config.
/// Core has no defaults for any of it: the host fills every field. Only facts
/// Core decides with are here; what only a provider adapter reads, such as the
/// model's maximum output, stays with the host.
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(crate) struct ModelFacts {
    pub context_window: u64,
    pub compaction_threshold: u64,
    /// The largest threshold the window allows, which is also the budget of a
    /// summary request.
    pub compaction_threshold_max: u64,
    /// The kinds of input the model reads. Text is always one of them.
    pub input_modalities: Vec<InputModality>,
    pub image_cost: ImageCostRule,
}

/// A kind of input a model reads.
#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum InputModality {
    Text,
    Image,
}

impl ModelFacts {
    fn validate(&self) -> Result<(), CoreError> {
        validate_input_modalities(
            "settings.context.model_facts.input_modalities",
            &self.input_modalities,
        )?;
        if self.context_window == 0 || self.compaction_threshold == 0 {
            return Err(CoreError::invalid_configuration(
                "settings.context.model_facts",
                "context_window and compaction_threshold must be greater than zero",
            ));
        }
        if self.compaction_threshold > self.compaction_threshold_max
            || self.compaction_threshold_max >= self.context_window
        {
            return Err(CoreError::invalid_configuration(
                "settings.context.model_facts",
                "compaction_threshold <= compaction_threshold_max < context_window must hold",
            ));
        }
        self.image_cost.validate()
    }
}

fn validate_input_modalities(
    field: &'static str,
    modalities: &[InputModality],
) -> Result<(), CoreError> {
    if !modalities.contains(&InputModality::Text) {
        return Err(CoreError::invalid_configuration(
            field,
            "input_modalities must include text",
        ));
    }
    for (index, modality) in modalities.iter().enumerate() {
        if modalities[..index].contains(modality) {
            return Err(CoreError::invalid_configuration(
                field,
                "input_modalities must not repeat a modality",
            ));
        }
    }
    Ok(())
}

/// How a delivered native image counts toward a model input estimate.
#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub(crate) enum ImageCostRule {
    /// Every image counts as the same number of tokens, whatever its size.
    Fixed { tokens: u64 },
}

impl ImageCostRule {
    /// The tokens one delivered native image adds to an estimate.
    pub(crate) fn image_tokens(self) -> u64 {
        match self {
            Self::Fixed { tokens } => tokens,
        }
    }

    fn validate(self) -> Result<(), CoreError> {
        if self.image_tokens() == 0 {
            return Err(CoreError::invalid_configuration(
                "settings.context.model_facts.image_cost",
                "an image must cost more than zero tokens",
            ));
        }
        Ok(())
    }
}

/// How one completion request receives images, decided from the input
/// modalities of the model that serves it. History is never rewritten: Core
/// projects each request (`model_input_projection`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum ImageDeliveryMode {
    /// The model reads images: an image stays an image, and an embedded
    /// resource that holds one is sent as an image.
    Native,
    /// The model takes no image input: an image becomes the `[Image]`
    /// placeholder, and an embedded image becomes `[Image]` followed by its
    /// link text. A resource link stays as is.
    WithoutImageInput,
}

impl ImageDeliveryMode {
    pub(crate) fn for_modalities(modalities: &[InputModality]) -> Self {
        if modalities.contains(&InputModality::Image) {
            Self::Native
        } else {
            Self::WithoutImageInput
        }
    }
}

/// The image delivery of each completion purpose.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct ImageDeliverySettings {
    pub agent: ImageDeliveryMode,
    pub compaction: ImageDeliveryMode,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(crate) struct ToolSettings {
    #[serde(default)]
    pub model_wait: crate::core::features::model_wait::Mode,
    #[serde(default = "crate::core::features::model_wait::default_max_wait_ms")]
    pub max_wait_ms: u64,
    pub programmatic: programmatic_tool_calling::Settings,
    pub large_output: large_output::Policy,
    pub subagents: subagents::Mode,
    pub background_processes: background_processes::Mode,
    #[serde(default, skip_serializing_if = "file_system::Mode::is_enabled")]
    pub file_system: file_system::Mode,
    pub command_environment: CommandEnvironment,
    #[serde(default, skip_serializing_if = "skills::Mode::is_when_configured")]
    pub skills: skills::Mode,
    /// The Host's bash timeouts, which Core states to the model and fills into a call
    /// without its own; omitted, the schema states none and Core fills nothing.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bash_timeout: Option<file_system::BashTimeoutSettings>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub permissions: Option<PermissionPolicy>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(crate) struct HarnessSettings {
    pub turn: TurnSettings,
    pub context: ContextSettings,
    pub tools: ToolSettings,
    /// Internal and unstable. Omitted means Core's default prompts.
    #[serde(
        default,
        deserialize_with = "crate::core::prompt::deserialize_prompt_override",
        skip_serializing_if = "PromptSettings::is_default"
    )]
    pub prompts: PromptSettings,
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
    settings.turn.output_limit_recovery.validate()?;
    settings.context.validate()?;
    settings.context.notifications.validate()?;
    settings.tools.programmatic.validate()?;
    settings.prompts.validate()?;
    if settings.tools.max_wait_ms == 0 {
        return Err(CoreError::invalid_configuration(
            "settings.tools.max_wait_ms",
            "maximum wait must be positive",
        ));
    }
    settings.tools.large_output.validate()?;
    if let Some(bash_timeout) = &settings.tools.bash_timeout {
        bash_timeout.validate()?;
    }
    if settings.tools.background_processes.is_enabled()
        && !settings.tools.command_environment.is_enabled()
    {
        return Err(CoreError::invalid_configuration(
            "settings.tools.background_processes",
            "background processes require an enabled command environment",
        ));
    }
    validate_file_system(&settings.tools)?;
    validate_programmatic_dependents(&settings.tools)
}

fn validate_file_system(tools: &ToolSettings) -> Result<(), CoreError> {
    if tools.file_system.is_enabled() {
        return Ok(());
    }
    if tools.command_environment.is_enabled() {
        return Err(CoreError::invalid_configuration(
            "settings.tools.file_system",
            "an enabled command environment requires the file system tools",
        ));
    }
    if matches!(tools.large_output, large_output::Policy::Filesystem { .. }) {
        return Err(CoreError::invalid_configuration(
            "settings.tools.file_system",
            "the filesystem large-output policy requires the file system tools",
        ));
    }
    Ok(())
}

/// Background processes and subagents are only callable from `run_typescript`.
fn validate_programmatic_dependents(tools: &ToolSettings) -> Result<(), CoreError> {
    if tools.programmatic.is_enabled() {
        return Ok(());
    }
    if tools.subagents.is_enabled() {
        return Err(CoreError::invalid_configuration(
            "settings.tools.programmatic.mode",
            "subagents require programmatic tool calling",
        ));
    }
    if tools.background_processes.is_enabled() {
        return Err(CoreError::invalid_configuration(
            "settings.tools.programmatic.mode",
            "background processes require programmatic tool calling",
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
    fn model_facts_reject_impossible_values() {
        type BreakFacts = fn(&mut ModelFacts);
        let cases: [(&str, BreakFacts); 8] = [
            (
                "context_window and compaction_threshold must be greater than zero",
                |facts| {
                    facts.compaction_threshold = 0;
                },
            ),
            (
                "compaction_threshold <= compaction_threshold_max < context_window must hold",
                |facts| {
                    facts.compaction_threshold = facts.compaction_threshold_max + 1;
                },
            ),
            (
                "compaction_threshold <= compaction_threshold_max < context_window must hold",
                |facts| {
                    facts.compaction_threshold_max = facts.context_window;
                },
            ),
            ("input_modalities must include text", |facts| {
                facts.input_modalities = vec![InputModality::Image];
            }),
            ("input_modalities must include text", |facts| {
                facts.input_modalities = Vec::new();
            }),
            ("input_modalities must not repeat a modality", |facts| {
                facts.input_modalities = vec![
                    InputModality::Text,
                    InputModality::Image,
                    InputModality::Image,
                ];
            }),
            ("an image must cost more than zero tokens", |facts| {
                facts.image_cost = ImageCostRule::Fixed { tokens: 0 };
            }),
            (
                "context_window and compaction_threshold must be greater than zero",
                |facts| {
                    facts.context_window = 0;
                },
            ),
        ];
        for (detail, break_facts) in cases {
            let mut config = config();
            break_facts(&mut config.settings.context.model_facts);

            assert_eq!(initial_state(config).unwrap_err().detail(), detail);
        }
    }

    #[test]
    fn compaction_model_input_modalities_are_validated_like_the_agent_model() {
        for modalities in [
            vec![InputModality::Image],
            vec![InputModality::Text, InputModality::Text],
        ] {
            let mut config = config();
            config.settings.context.compaction_model_input_modalities = Some(modalities);

            assert!(matches!(
                initial_state(config).unwrap_err(),
                CoreError::InvalidConfiguration {
                    field: "settings.context.compaction_model_input_modalities",
                    ..
                }
            ));
        }
    }

    #[test]
    fn compaction_model_input_modalities_are_optional_on_the_wire() {
        let mut config = config();
        let value = serde_json::to_value(&config).unwrap();
        assert!(
            value["settings"]["context"]
                .get("compaction_model_input_modalities")
                .is_none()
        );

        config.settings.context.compaction_model_input_modalities = Some(vec![InputModality::Text]);
        let value = serde_json::to_value(&config).unwrap();
        assert_eq!(
            value["settings"]["context"]["compaction_model_input_modalities"],
            json!(["text"])
        );
        let parsed: HarnessConfig = serde_json::from_value(value).unwrap();
        assert_eq!(parsed, config);
    }

    #[test]
    fn model_facts_round_trip_and_are_required() {
        let config = config();
        let value = serde_json::to_value(&config).unwrap();
        assert_eq!(
            value["settings"]["context"]["model_facts"],
            json!({
                "context_window": 262_144,
                "compaction_threshold": 202_144,
                "compaction_threshold_max": 238_144,
                "input_modalities": ["text", "image"],
                "image_cost": { "kind": "fixed", "tokens": 8_192 },
            })
        );
        assert_eq!(
            serde_json::from_value::<HarnessConfig>(value.clone()).unwrap(),
            config
        );

        for adapter_fact in ["max_output_tokens", "reports_cutoff"] {
            let mut extra = value.clone();
            extra["settings"]["context"]["model_facts"][adapter_fact] = json!(true);
            let error = serde_json::from_value::<HarnessConfig>(extra).unwrap_err();
            assert!(error.to_string().contains("unknown field"), "{error}");
        }

        let mut omitted = value;
        omitted["settings"]["context"]
            .as_object_mut()
            .unwrap()
            .remove("model_facts");
        let error = serde_json::from_value::<HarnessConfig>(omitted).unwrap_err();
        assert!(error.to_string().contains("missing field `model_facts`"));
    }

    #[test]
    fn compaction_threshold_comes_from_the_model_facts() {
        let mut config = config();
        assert_eq!(config.settings.context.compaction_threshold(), None);

        config.settings.context.compaction = CompactionPolicy::Automatic;
        config.settings.context.model_facts.compaction_threshold = 150_000;

        assert_eq!(
            config.settings.context.compaction_threshold(),
            Some(150_000)
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

        let skill_tool_config = |skills: skills::Mode| {
            let mut skill_config = crate::core::testing::config();
            skill_config.settings.tools.skills = skills;
            skill_config.capabilities.tool_groups = vec![ToolGroupDefinition {
                name: "client".to_string(),
                description: String::new(),
                metadata: None,
                icon_url: None,
                tools: vec![direct_tool("skill")],
            }];
            skill_config
        };
        for reserved in [skills::Mode::Enabled, skills::Mode::WhenConfigured] {
            assert_eq!(
                initial_state(skill_tool_config(reserved))
                    .unwrap_err()
                    .detail(),
                "direct tool name \"skill\" conflicts with a built-in tool"
            );
        }
        assert!(initial_state(skill_tool_config(skills::Mode::Disabled)).is_ok());
    }

    #[test]
    fn preserves_permission_policy_when_harness_config_round_trips() {
        let mut value = serde_json::to_value(config()).unwrap();
        let permissions = json!({
            "rules": [{
                "tools": ["file_system.bash"],
                "decision": "ask",
                "allowlist": ["*git status*"],
                "denylist": ["*production*"],
                "sensitive": ["*sudo*"],
            }],
            "default": "deny",
        });
        value["settings"]["tools"]["permissions"] = permissions.clone();

        let config: HarnessConfig =
            serde_json::from_value(value).expect("permission policy deserializes");
        let serialized = serde_json::to_value(config).expect("permission policy serializes");

        assert_eq!(serialized["settings"]["tools"]["permissions"], permissions);
    }

    #[test]
    fn rejects_harness_config_with_invalid_permission_pattern() {
        let mut value = serde_json::to_value(config()).unwrap();
        value["settings"]["tools"]["permissions"] = json!({
            "rules": [{
                "tools": ["["],
                "decision": "deny",
                "allowlist": [],
                "denylist": [],
                "sensitive": [],
            }],
            "default": "allow",
        });

        assert!(serde_json::from_value::<HarnessConfig>(value).is_err());
    }

    #[test]
    fn rejects_unknown_permission_policy_field() {
        let mut value = serde_json::to_value(config()).unwrap();
        value["settings"]["tools"]["permissions"] = json!({
            "rules": [],
            "default": "allow",
            "unexpected": true,
        });

        assert!(serde_json::from_value::<HarnessConfig>(value).is_err());
    }

    #[test]
    fn rejects_unknown_permission_rule_field() {
        let mut value = serde_json::to_value(config()).unwrap();
        value["settings"]["tools"]["permissions"] = json!({
            "rules": [{
                "tools": ["file_system.read_file"],
                "decision": "deny",
                "allowlist": [],
                "denylist": [],
                "sensitive": [],
                "unexpected": true,
            }],
            "default": "allow",
        });

        assert!(serde_json::from_value::<HarnessConfig>(value).is_err());
    }
}
