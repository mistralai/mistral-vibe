use serde::Deserialize;
use serde::Serialize;
use serde_json::Value;

use crate::core::wire::content::ContentBlock;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum ToolKind {
    External,
    Internal,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub(crate) struct ToolFunction {
    pub name: String,
    pub arguments: Value,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum ToolState {
    #[serde(rename = "pending_tool")]
    Pending {
        kind: ToolKind,
        id: String,
        function: ToolFunction,
    },
    #[serde(rename = "resolved_tool")]
    Resolved {
        kind: ToolKind,
        id: String,
        function: ToolFunction,
        result: Value,
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        content: Vec<ContentBlock>,
    },
    #[serde(rename = "rejected_tool")]
    Rejected {
        kind: ToolKind,
        id: String,
        function: ToolFunction,
        error: Value,
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        content: Vec<ContentBlock>,
    },
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub(crate) struct PartialEvaluation {
    pub code: String,
    #[serde(default)]
    pub input: Value,
    #[serde(default)]
    pub tool_state: Vec<ToolState>,
}
