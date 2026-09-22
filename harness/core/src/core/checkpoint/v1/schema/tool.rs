use serde::{Deserialize, Deserializer, Serialize, Serializer};
use serde_json::{Map, Value};

use crate::core::wire::content::ContentBlock;
use crate::core::wire::tool::{ProtocolError, StructuredContent, ToolResult};

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub(in crate::core::checkpoint::v1) struct CheckpointProtocolError {
    code: String,
    message: String,
    retryable: bool,
    #[serde(default)]
    details: Value,
}

impl CheckpointProtocolError {
    pub(in crate::core::checkpoint::v1) fn capture(error: &ProtocolError) -> Self {
        Self {
            code: error.code.clone(),
            message: error.message.clone(),
            retryable: error.retryable,
            details: error.details.clone(),
        }
    }

    pub(in crate::core::checkpoint::v1) fn restore(self) -> ProtocolError {
        ProtocolError {
            code: self.code,
            message: self.message,
            retryable: self.retryable,
            details: self.details,
        }
    }
}

#[derive(Clone, Debug, Default, PartialEq)]
pub(in crate::core::checkpoint::v1) enum CheckpointStructuredContent {
    #[default]
    Absent,
    Present(Value),
}

impl CheckpointStructuredContent {
    fn capture(content: &StructuredContent) -> Self {
        match content {
            StructuredContent::Absent => Self::Absent,
            StructuredContent::Present(value) => Self::Present(value.clone()),
        }
    }

    fn restore(self) -> StructuredContent {
        match self {
            Self::Absent => StructuredContent::Absent,
            Self::Present(value) => StructuredContent::Present(value),
        }
    }

    fn is_absent(&self) -> bool {
        matches!(self, Self::Absent)
    }
}

impl Serialize for CheckpointStructuredContent {
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

impl<'de> Deserialize<'de> for CheckpointStructuredContent {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        Value::deserialize(deserializer).map(Self::Present)
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub(in crate::core::checkpoint::v1) enum CheckpointToolResult {
    Success {
        #[serde(default)]
        content: Vec<ContentBlock>,
        #[serde(
            default,
            skip_serializing_if = "CheckpointStructuredContent::is_absent"
        )]
        structured_content: CheckpointStructuredContent,
        #[serde(default, rename = "_meta", skip_serializing_if = "Option::is_none")]
        meta: Option<Map<String, Value>>,
    },
    Failure {
        #[serde(default)]
        content: Vec<ContentBlock>,
        #[serde(
            default,
            skip_serializing_if = "CheckpointStructuredContent::is_absent"
        )]
        structured_content: CheckpointStructuredContent,
        #[serde(default, rename = "_meta", skip_serializing_if = "Option::is_none")]
        meta: Option<Map<String, Value>>,
        error: CheckpointProtocolError,
    },
}

impl CheckpointToolResult {
    pub(in crate::core::checkpoint::v1) fn capture(result: &ToolResult) -> Self {
        match result {
            ToolResult::Success {
                content,
                structured_content,
                meta,
            } => Self::Success {
                content: content.clone(),
                structured_content: CheckpointStructuredContent::capture(structured_content),
                meta: meta.clone(),
            },
            ToolResult::Failure {
                content,
                structured_content,
                meta,
                error,
            } => Self::Failure {
                content: content.clone(),
                structured_content: CheckpointStructuredContent::capture(structured_content),
                meta: meta.clone(),
                error: CheckpointProtocolError::capture(error),
            },
        }
    }

    pub(in crate::core::checkpoint::v1) fn restore(self) -> ToolResult {
        match self {
            Self::Success {
                content,
                structured_content,
                meta,
            } => ToolResult::Success {
                content,
                structured_content: structured_content.restore(),
                meta,
            },
            Self::Failure {
                content,
                structured_content,
                meta,
                error,
            } => ToolResult::Failure {
                content,
                structured_content: structured_content.restore(),
                meta,
                error: error.restore(),
            },
        }
    }
}
