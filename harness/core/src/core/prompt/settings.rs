//! Prompt text Core sends to the model, and the internal override a Runtime may pass.
//!
//! Core owns the default text in `default_prompts.json`. `settings.prompts` is internal and
//! unstable: the Harness team uses it for prompt research, and products do not set it. An
//! override is a partial document merged over the defaults, so it names only what it changes.

use std::sync::LazyLock;

use serde::{Deserialize, Deserializer, Serialize};
use serde_json::Value;

use crate::core::error::CoreError;
use crate::core::features::{compaction, programmatic_tool_calling, subagents};
use crate::core::helpers::AgentMessageSettings;

static DEFAULT_DOCUMENT: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("default_prompts.json"))
        .expect("the embedded default prompts are JSON")
});

static DEFAULTS: LazyLock<PromptSettings> = LazyLock::new(|| {
    let defaults = PromptSettings::deserialize(DEFAULT_DOCUMENT.clone())
        .expect("the embedded default prompts are complete");
    defaults
        .validate()
        .expect("the embedded default prompts are valid");
    defaults
});

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(crate) struct PromptSettings {
    pub guidance: GuidanceSettings,
    pub agent_messages: AgentMessageSettings,
    pub agent_tools: subagents::Prompts,
    pub programs: programmatic_tool_calling::Prompts,
    pub compaction: compaction::Prompts,
    pub wait_description: String,
    pub discovery_description: String,
}

/// Generic guidance at the start of the system prompt. Empty text omits that section.
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(crate) struct GuidanceSettings {
    pub intro: String,
    pub autonomy: String,
}

impl Default for PromptSettings {
    fn default() -> Self {
        DEFAULTS.clone()
    }
}

impl PromptSettings {
    pub(crate) fn is_default(&self) -> bool {
        self == &*DEFAULTS
    }

    /// Whether a change from `self` to `next` changes a tool definition or tool catalog text.
    pub(crate) fn tool_text_changed(&self, next: &Self) -> bool {
        self.programs != next.programs
            || self.agent_tools != next.agent_tools
            || self.wait_description != next.wait_description
            || self.discovery_description != next.discovery_description
    }

    pub(crate) fn validate(&self) -> Result<(), CoreError> {
        self.agent_messages.validate()?;
        self.agent_tools.validate()?;
        self.programs.validate()?;
        self.compaction.validate()?;
        for (text, path) in [
            (&self.wait_description, "settings.prompts.wait_description"),
            (
                &self.discovery_description,
                "settings.prompts.discovery_description",
            ),
        ] {
            if text.trim().is_empty() {
                return Err(CoreError::invalid_configuration(
                    path,
                    "prompt must not be empty",
                ));
            }
        }
        Ok(())
    }
}

/// Reads a partial override and merges it over the defaults. Objects merge by key; any other
/// value, including `null`, replaces the default and must then have the field's type.
pub(crate) fn deserialize_override<'de, D>(deserializer: D) -> Result<PromptSettings, D::Error>
where
    D: Deserializer<'de>,
{
    let mut document = DEFAULT_DOCUMENT.clone();
    merge(&mut document, Value::deserialize(deserializer)?);
    PromptSettings::deserialize(document).map_err(serde::de::Error::custom)
}

fn merge(target: &mut Value, patch: Value) {
    match (target, patch) {
        (Value::Object(target), Value::Object(patch)) => {
            for (key, value) in patch {
                match target.get_mut(&key) {
                    Some(existing) => merge(existing, value),
                    None => {
                        target.insert(key, value);
                    }
                }
            }
        }
        (target, patch) => *target = patch,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn read(value: Value) -> Result<PromptSettings, serde_json::Error> {
        deserialize_override(value)
    }

    #[test]
    fn an_override_changes_only_the_text_it_names() {
        let settings = read(json!({"agent_tools": {"spawn": "Spawn."}})).unwrap();

        assert_eq!(settings.agent_tools.spawn, "Spawn.");
        let mut expected = PromptSettings::default();
        expected.agent_tools.spawn = "Spawn.".into();
        assert_eq!(settings, expected);
        assert!(!settings.is_default());
        assert!(read(json!({})).unwrap().is_default());
    }

    #[test]
    fn an_override_rejects_unknown_names_and_null() {
        assert!(read(json!({"agent_tools": {"spawm": "Spawn."}})).is_err());
        assert!(read(json!({"wait_description": null})).is_err());
        assert!(read(json!(null)).is_err());
    }
}
