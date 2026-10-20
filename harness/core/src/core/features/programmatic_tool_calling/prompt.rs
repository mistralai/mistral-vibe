use crate::core::error::CoreError;
use crate::core::helpers::{render_template, validate_template};
use serde::{Deserialize, Serialize};
use std::collections::HashSet;

/// Program tool text. Core's defaults live in `default_prompts.json`.
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(crate) struct Prompts {
    pub description: String,
    /// Stable usage guidance rendered before product instructions.
    pub tool_use: String,
    /// Connector inventory rendered after product instructions; requires `{groups}`.
    pub tool_groups: String,
    pub current_time: String,
}

impl Prompts {
    pub(crate) fn validate(&self) -> Result<(), CoreError> {
        if [&self.description, &self.tool_use, &self.current_time]
            .iter()
            .any(|value| value.trim().is_empty())
        {
            return Err(CoreError::invalid_configuration(
                "settings.prompts.programs",
                "program prompts must not be empty",
            ));
        }
        validate_template(
            &self.tool_groups,
            &["groups"],
            "settings.prompts.programs.tool_groups",
        )
    }
}

pub(crate) fn tool_group_inventory_prompt<'a>(
    groups: impl IntoIterator<Item = &'a str>,
    template: &str,
) -> String {
    let mut seen = HashSet::new();
    let groups = groups
        .into_iter()
        .filter(|name| seen.insert(*name))
        .map(|name| format!("- {name}"))
        .collect::<Vec<_>>()
        .join("\n");
    render_template(template, &[("groups", &groups)]).expect("validated tool groups prompt")
}
