use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use crate::core::wire::content::ContentBlock;
use crate::core::wire::message::{
    AssistantPart, AssistantRole, AssistantSemanticPart, CandidateMessage, Message,
    ReasoningContent, ToolArguments, ToolOutcome,
};

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub(in crate::core::checkpoint::v1) enum CheckpointToolArguments {
    Json { raw: String, value: Value },
    InvalidJson { raw: String, error: String },
}

impl CheckpointToolArguments {
    fn capture(arguments: &ToolArguments) -> Self {
        match arguments {
            ToolArguments::Json { raw, value } => Self::Json {
                raw: raw.clone(),
                value: value.clone(),
            },
            ToolArguments::InvalidJson { raw, error } => Self::InvalidJson {
                raw: raw.clone(),
                error: error.clone(),
            },
        }
    }

    fn restore(self) -> ToolArguments {
        match self {
            Self::Json { raw, value } => ToolArguments::Json { raw, value },
            Self::InvalidJson { raw, error } => ToolArguments::InvalidJson { raw, error },
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub(in crate::core::checkpoint::v1) enum CheckpointReasoningContent {
    Text { text: String },
    Summary { text: String },
    Redacted { data: String },
}

impl CheckpointReasoningContent {
    fn capture(content: &ReasoningContent) -> Self {
        match content {
            ReasoningContent::Text { text } => Self::Text { text: text.clone() },
            ReasoningContent::Summary { text } => Self::Summary { text: text.clone() },
            ReasoningContent::Redacted { data } => Self::Redacted { data: data.clone() },
        }
    }

    fn restore(self) -> ReasoningContent {
        match self {
            Self::Text { text } => ReasoningContent::Text { text },
            Self::Summary { text } => ReasoningContent::Summary { text },
            Self::Redacted { data } => ReasoningContent::Redacted { data },
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub(in crate::core::checkpoint::v1) enum CheckpointAssistantSemanticPart {
    Reasoning {
        content: Vec<CheckpointReasoningContent>,
        #[serde(default, rename = "_meta", skip_serializing_if = "Option::is_none")]
        meta: Option<Map<String, Value>>,
    },
    ToolCall {
        id: String,
        name: String,
        arguments: CheckpointToolArguments,
        #[serde(default, rename = "_meta", skip_serializing_if = "Option::is_none")]
        meta: Option<Map<String, Value>>,
    },
}

impl CheckpointAssistantSemanticPart {
    fn capture(part: &AssistantSemanticPart) -> Self {
        match part {
            AssistantSemanticPart::Reasoning { content, meta } => Self::Reasoning {
                content: content
                    .iter()
                    .map(CheckpointReasoningContent::capture)
                    .collect(),
                meta: meta.clone(),
            },
            AssistantSemanticPart::ToolCall {
                id,
                name,
                arguments,
                meta,
            } => Self::ToolCall {
                id: id.clone(),
                name: name.clone(),
                arguments: CheckpointToolArguments::capture(arguments),
                meta: meta.clone(),
            },
        }
    }

    fn restore(self) -> AssistantSemanticPart {
        match self {
            Self::Reasoning { content, meta } => AssistantSemanticPart::Reasoning {
                content: content
                    .into_iter()
                    .map(CheckpointReasoningContent::restore)
                    .collect(),
                meta,
            },
            Self::ToolCall {
                id,
                name,
                arguments,
                meta,
            } => AssistantSemanticPart::ToolCall {
                id,
                name,
                arguments: arguments.restore(),
                meta,
            },
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(untagged)]
pub(in crate::core::checkpoint::v1) enum CheckpointAssistantPart {
    Content(ContentBlock),
    Semantic(CheckpointAssistantSemanticPart),
}

impl CheckpointAssistantPart {
    fn capture(part: &AssistantPart) -> Self {
        match part {
            AssistantPart::Content(block) => Self::Content(block.clone()),
            AssistantPart::Semantic(part) => {
                Self::Semantic(CheckpointAssistantSemanticPart::capture(part))
            }
        }
    }

    fn restore(self) -> AssistantPart {
        match self {
            Self::Content(block) => AssistantPart::Content(block),
            Self::Semantic(part) => AssistantPart::Semantic(part.restore()),
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
enum CheckpointAssistantRole {
    Assistant,
}

impl CheckpointAssistantRole {
    fn capture(role: &AssistantRole) -> Self {
        match role {
            AssistantRole::Assistant => Self::Assistant,
        }
    }

    fn restore(self) -> AssistantRole {
        match self {
            Self::Assistant => AssistantRole::Assistant,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub(in crate::core::checkpoint::v1) struct CheckpointCandidateMessage {
    role: CheckpointAssistantRole,
    content: Vec<CheckpointAssistantPart>,
}

impl CheckpointCandidateMessage {
    pub(in crate::core::checkpoint::v1) fn capture(message: &CandidateMessage) -> Self {
        Self {
            role: CheckpointAssistantRole::capture(&message.role),
            content: message
                .content
                .iter()
                .map(CheckpointAssistantPart::capture)
                .collect(),
        }
    }

    pub(in crate::core::checkpoint::v1) fn restore(self) -> CandidateMessage {
        CandidateMessage {
            role: self.role.restore(),
            content: self
                .content
                .into_iter()
                .map(CheckpointAssistantPart::restore)
                .collect(),
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub(in crate::core::checkpoint::v1) enum CheckpointToolOutcome {
    Success,
    Failure,
}

impl CheckpointToolOutcome {
    fn capture(outcome: &ToolOutcome) -> Self {
        match outcome {
            ToolOutcome::Success => Self::Success,
            ToolOutcome::Failure => Self::Failure,
        }
    }

    fn restore(self) -> ToolOutcome {
        match self {
            Self::Success => ToolOutcome::Success,
            Self::Failure => ToolOutcome::Failure,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "role", rename_all = "snake_case", deny_unknown_fields)]
pub(in crate::core::checkpoint::v1) enum CheckpointMessage {
    System {
        content: Vec<ContentBlock>,
    },
    User {
        content: Vec<ContentBlock>,
    },
    Assistant {
        content: Vec<CheckpointAssistantPart>,
    },
    Tool {
        tool_call_id: String,
        name: String,
        outcome: CheckpointToolOutcome,
        content: Vec<ContentBlock>,
        #[serde(default, rename = "_meta", skip_serializing_if = "Option::is_none")]
        meta: Option<Map<String, Value>>,
    },
}

impl CheckpointMessage {
    pub(in crate::core::checkpoint::v1) fn capture(message: &Message) -> Self {
        match message {
            Message::System { content } => Self::System {
                content: content.clone(),
            },
            Message::User { content } => Self::User {
                content: content.clone(),
            },
            Message::Assistant { content } => Self::Assistant {
                content: content
                    .iter()
                    .map(CheckpointAssistantPart::capture)
                    .collect(),
            },
            Message::Tool {
                tool_call_id,
                name,
                outcome,
                content,
                meta,
            } => Self::Tool {
                tool_call_id: tool_call_id.clone(),
                name: name.clone(),
                outcome: CheckpointToolOutcome::capture(outcome),
                content: content.clone(),
                meta: meta.clone(),
            },
        }
    }

    pub(in crate::core::checkpoint::v1) fn restore(self) -> Message {
        match self {
            Self::System { content } => Message::System { content },
            Self::User { content } => Message::User { content },
            Self::Assistant { content } => Message::Assistant {
                content: content
                    .into_iter()
                    .map(CheckpointAssistantPart::restore)
                    .collect(),
            },
            Self::Tool {
                tool_call_id,
                name,
                outcome,
                content,
                meta,
            } => Message::Tool {
                tool_call_id,
                name,
                outcome: outcome.restore(),
                content,
                meta,
            },
        }
    }
}
