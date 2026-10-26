use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::core::tools::external::RuntimeBuiltinToolName;

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
    /// The Runtime built-in this function calls. Its arguments are checked as
    /// for a direct call (`tools::arguments::validate_arguments`). `None` for
    /// app tools.
    #[serde(skip)]
    pub runtime_builtin: Option<RuntimeBuiltinToolName>,
}
