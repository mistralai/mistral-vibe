use serde::Deserialize;
use serde::Deserializer;
use serde::Serialize;
use serde::Serializer;
use serde_json::Value;

use crate::core::wire::content::ContentBlock;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub(crate) struct ToolCall {
    pub id: String,
    pub name: String,
    #[serde(default)]
    pub arguments: Value,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub argument_error: Option<String>,
}

/// Longest tool name, in characters, that Core repeats back to the model in an
/// error or feedback message. A model that invents a very long name sees a
/// short echo of it, not its own output again.
pub(crate) const ECHOED_TOOL_NAME_CHARS: usize = 64;

/// `name` as Core repeats it to the model: cut to [`ECHOED_TOOL_NAME_CHARS`]
/// characters, with `...` marking a cut.
pub(crate) fn echoed_tool_name(name: &str) -> String {
    match name.char_indices().nth(ECHOED_TOOL_NAME_CHARS) {
        None => name.to_string(),
        Some((end, _)) => format!("{}...", &name[..end]),
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub(crate) struct ProtocolError {
    pub code: String,
    pub message: String,
    pub retryable: bool,
    #[serde(default)]
    pub details: Value,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub(crate) enum StructuredContent {
    #[default]
    Absent,
    Present(Value),
}

impl StructuredContent {
    pub(crate) fn present(value: Value) -> Self {
        Self::Present(value)
    }

    pub(crate) fn as_value(&self) -> Option<&Value> {
        match self {
            Self::Absent => None,
            Self::Present(value) => Some(value),
        }
    }

    pub(crate) fn into_value(self) -> Option<Value> {
        match self {
            Self::Absent => None,
            Self::Present(value) => Some(value),
        }
    }

    fn is_absent(&self) -> bool {
        matches!(self, Self::Absent)
    }
}

impl Serialize for StructuredContent {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        match self {
            Self::Absent => serializer.serialize_none(),
            Self::Present(value) => value.serialize(serializer),
        }
    }
}

impl<'de> Deserialize<'de> for StructuredContent {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        Value::deserialize(deserializer).map(Self::Present)
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum ToolResult {
    Success {
        #[serde(default)]
        content: Vec<ContentBlock>,
        #[serde(default, skip_serializing_if = "StructuredContent::is_absent")]
        structured_content: StructuredContent,
        #[serde(default, rename = "_meta", skip_serializing_if = "Option::is_none")]
        meta: Option<serde_json::Map<String, Value>>,
    },
    Failure {
        #[serde(default)]
        content: Vec<ContentBlock>,
        #[serde(default, skip_serializing_if = "StructuredContent::is_absent")]
        structured_content: StructuredContent,
        #[serde(default, rename = "_meta", skip_serializing_if = "Option::is_none")]
        meta: Option<serde_json::Map<String, Value>>,
        error: ProtocolError,
    },
}

impl ToolResult {
    pub(crate) fn structured_content(&self) -> Option<&Value> {
        match self {
            Self::Success {
                structured_content, ..
            }
            | Self::Failure {
                structured_content, ..
            } => structured_content.as_value(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::testing::*;

    #[test]
    fn echoed_tool_names_are_cut_to_a_bounded_length() {
        assert_eq!(echoed_tool_name("read_file"), "read_file");
        let exact = "a".repeat(ECHOED_TOOL_NAME_CHARS);
        assert_eq!(echoed_tool_name(&exact), exact);
        let long = format!("{}{}", "\u{e9}".repeat(ECHOED_TOOL_NAME_CHARS), "tail");
        assert_eq!(
            echoed_tool_name(&long),
            format!("{}...", "\u{e9}".repeat(ECHOED_TOOL_NAME_CHARS))
        );
    }

    #[test]
    fn tool_result_wire_shape_distinguishes_absent_and_null_structured_content() {
        let absent: ToolResult = serde_json::from_value(json!({
            "type": "success",
            "content": [{"type": "text", "text": "fallback"}]
        }))
        .unwrap();
        let explicit_null: ToolResult = serde_json::from_value(json!({
            "type": "success",
            "content": [{"type": "text", "text": "fallback"}],
            "structured_content": null,
            "_meta": {"request": "mcp-1"}
        }))
        .unwrap();

        assert_eq!(absent.structured_content(), None);
        assert_eq!(explicit_null.structured_content(), Some(&Value::Null));
        assert!(
            serde_json::to_value(absent)
                .unwrap()
                .get("structured_content")
                .is_none()
        );
        assert_eq!(
            serde_json::to_value(explicit_null).unwrap()["structured_content"],
            Value::Null
        );
    }
}
