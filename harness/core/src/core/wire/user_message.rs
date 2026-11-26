use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum UserMessageMode {
    Queue,
    Steer,
}

/// The JSON Schema one Turn's answer must match: an ask on that Turn, not a
/// model setting.
///
/// Core keeps it on the Turn the message starts and puts it on each agent
/// completion Action of that Turn only. The Runtime turns it into its
/// provider's structured-output request.
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub(crate) struct OutputType {
    /// The schema's name, as providers ask for one.
    pub name: String,
    pub schema: Value,
}

impl OutputType {
    pub(crate) fn validate(&self) -> Result<(), String> {
        if self.name.trim().is_empty() {
            return Err("output_type name must not be empty".to_string());
        }
        if !self.schema.is_object() {
            return Err("output_type schema must be a JSON object".to_string());
        }
        Ok(())
    }
}
