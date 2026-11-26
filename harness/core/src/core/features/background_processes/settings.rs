use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(tag = "mode", rename_all = "snake_case", deny_unknown_fields)]
pub(crate) enum Mode {
    Disabled,
    Enabled,
}

impl Mode {
    pub(crate) fn is_enabled(self) -> bool {
        matches!(self, Self::Enabled)
    }

    pub(crate) fn changes_system_prompt(self, next: Self) -> bool {
        self != next
    }
}
