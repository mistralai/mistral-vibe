use super::tools::ToolName;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(tag = "mode", rename_all = "snake_case", deny_unknown_fields)]
pub(crate) enum Mode {
    Disabled,
    Enabled {
        #[serde(default = "default_operations")]
        operations: Vec<ToolName>,
    },
}

fn default_operations() -> Vec<ToolName> {
    ToolName::ALL.to_vec()
}

impl Mode {
    pub(crate) fn enabled() -> Self {
        Self::Enabled {
            operations: default_operations(),
        }
    }
    pub(crate) fn is_enabled(&self) -> bool {
        matches!(self, Self::Enabled { .. })
    }
    pub(crate) fn supports(&self, operation: ToolName) -> bool {
        matches!(self, Self::Enabled { operations } if operations.contains(&operation))
    }
    pub(crate) fn changes_system_prompt(&self, next: &Self) -> bool {
        self != next
    }
}

pub(crate) fn validate_reconfiguration(current: &Mode, next: &Mode) -> Result<(), &'static str> {
    if current != next {
        return Err("settings.tools.subagents cannot be reconfigured after session creation");
    }
    Ok(())
}
