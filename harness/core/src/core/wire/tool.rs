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
