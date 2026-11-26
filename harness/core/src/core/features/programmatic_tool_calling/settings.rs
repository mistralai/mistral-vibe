use serde::{Deserialize, Serialize};

use crate::core::error::CoreError;

/// An on/off switch whose omission means enabled, so settings recorded before
/// the switch existed keep their tools.
#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum Switch {
    #[default]
    Enabled,
    Disabled,
}

impl Switch {
    pub(crate) fn is_enabled(&self) -> bool {
        matches!(self, Self::Enabled)
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(crate) struct Settings {
    /// Whether the Session exposes `run_typescript`, `search_tool_functions`,
    /// and the `tools.<group>.<function>` catalog.
    #[serde(default, skip_serializing_if = "Switch::is_enabled")]
    pub mode: Switch,
    /// Whether the programmatic catalog includes `tools.self.sleep`.
    #[serde(default, skip_serializing_if = "Switch::is_enabled")]
    pub sleep: Switch,
    pub max_effects: u32,
    pub max_operations: u32,
    /// Time budget of one program: wall-clock time, paused while an
    /// operation waits for an approval or a hook.
    pub max_elapsed_ms: u64,
    pub max_sleep_ms: u64,
}

impl Settings {
    pub(crate) fn is_enabled(&self) -> bool {
        self.mode.is_enabled()
    }

    pub(crate) fn offers_sleep(&self) -> bool {
        self.mode.is_enabled() && self.sleep.is_enabled()
    }

    pub(crate) fn validate(&self) -> Result<(), CoreError> {
        if self.max_sleep_ms == 0 {
            return Err(CoreError::invalid_configuration(
                "settings.tools.programmatic.max_sleep_ms",
                "settings.tools.programmatic.max_sleep_ms must be greater than zero",
            ));
        }
        if self.max_elapsed_ms == 0 {
            return Err(CoreError::invalid_configuration(
                "settings.tools.programmatic.max_elapsed_ms",
                "settings.tools.programmatic.max_elapsed_ms must be greater than zero",
            ));
        }
        if self.max_effects == 0 {
            return Err(CoreError::invalid_configuration(
                "settings.tools.programmatic.max_effects",
                "settings.tools.programmatic.max_effects must be greater than zero",
            ));
        }
        if self.max_operations == 0 {
            return Err(CoreError::invalid_configuration(
                "settings.tools.programmatic.max_operations",
                "settings.tools.programmatic.max_operations must be greater than zero",
            ));
        }
        if self.max_operations < self.max_effects {
            return Err(CoreError::invalid_configuration(
                "settings.tools.programmatic.max_operations",
                "settings.tools.programmatic.max_operations must be greater than or equal to settings.tools.programmatic.max_effects",
            ));
        }
        Ok(())
    }
}
