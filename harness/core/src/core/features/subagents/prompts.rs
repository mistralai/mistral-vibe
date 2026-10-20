use serde::{Deserialize, Serialize};

use crate::core::error::CoreError;

/// Agent tool text. Core's defaults live in `default_prompts.json`.
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(crate) struct Prompts {
    pub guidance: String,
    pub search_description: String,
    pub list_configs: String,
    pub list: String,
    pub spawn: String,
    pub spawn_with_conversation: String,
    pub send_message: String,
    pub interrupt: String,
}

impl Prompts {
    pub(crate) fn validate(&self) -> Result<(), CoreError> {
        if [
            &self.guidance,
            &self.search_description,
            &self.list_configs,
            &self.list,
            &self.spawn,
            &self.spawn_with_conversation,
            &self.send_message,
            &self.interrupt,
        ]
        .iter()
        .any(|value| value.trim().is_empty())
        {
            return Err(CoreError::invalid_configuration(
                "settings.prompts.agent_tools",
                "agent prompts must not be empty",
            ));
        }
        Ok(())
    }
}
