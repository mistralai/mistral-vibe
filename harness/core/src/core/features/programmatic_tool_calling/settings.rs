use serde::{Deserialize, Serialize};

use crate::core::error::CoreError;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(crate) struct Settings {
    pub max_effects: u32,
    pub max_operations: u32,
}

impl Settings {
    pub(crate) fn validate(&self) -> Result<(), CoreError> {
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
