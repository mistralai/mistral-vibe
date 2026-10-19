use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub(crate) struct ProgrammaticName {
    pub namespace: String,
    pub name: String,
}

impl ProgrammaticName {
    pub(crate) fn display_name(&self) -> String {
        format!("tools.{}.{}", self.namespace, self.name)
    }
}

/// A removed function may replay saved results, but cannot request another effect.
#[derive(Clone, Debug, Serialize)]
pub(crate) struct ReplayOnlyTool {
    pub name: String,
    pub programmatic_name: ProgrammaticName,
    pub rejection_message: String,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub(crate) struct TypeScriptTool {
    pub name: String,
    pub programmatic_name: ProgrammaticName,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub input_schema: Value,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub output_schema: Option<Value>,
}
