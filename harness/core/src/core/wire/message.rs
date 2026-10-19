use crate::core::error::CoreError;
use serde::Deserialize;
use serde::Serialize;
use serde_json::Value;
use serde_json::json;

use crate::core::wire::content::ContentBlock;
use crate::core::wire::content::text_content;
use crate::core::wire::content::validate_non_empty_content;
use crate::core::wire::tool::ToolCall;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum ToolArguments {
    Json { raw: String, value: Value },
    InvalidJson { raw: String, error: String },
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum ReasoningContent {
    Text { text: String },
    Summary { text: String },
    Redacted { data: String },
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum AssistantSemanticPart {
    Reasoning {
        content: Vec<ReasoningContent>,
        #[serde(default, rename = "_meta", skip_serializing_if = "Option::is_none")]
        meta: Option<serde_json::Map<String, Value>>,
    },
    ToolCall {
        id: String,
        name: String,
        arguments: ToolArguments,
        #[serde(default, rename = "_meta", skip_serializing_if = "Option::is_none")]
        meta: Option<serde_json::Map<String, Value>>,
    },
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(untagged)]
pub(crate) enum AssistantPart {
    Content(ContentBlock),
    Semantic(AssistantSemanticPart),
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum AssistantRole {
    Assistant,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub(crate) struct CandidateMessage {
    pub role: AssistantRole,
    pub content: Vec<AssistantPart>,
}

impl CandidateMessage {
    pub(crate) fn content_blocks(&self) -> Vec<ContentBlock> {
        self.content
            .iter()
            .filter_map(|part| match part {
                AssistantPart::Content(block) => Some(block.clone()),
                AssistantPart::Semantic(_) => None,
            })
            .collect()
    }

    pub(crate) fn tool_calls(&self) -> Vec<ToolCall> {
        tool_calls_from_parts(&self.content)
    }

    pub(crate) fn replace_content(&mut self, replacement: Vec<ContentBlock>) {
        let insertion_index = self
            .content
            .iter()
            .take_while(|part| !matches!(part, AssistantPart::Content(_)))
            .filter(|part| matches!(part, AssistantPart::Semantic(_)))
            .count();
        self.content
            .retain(|part| !matches!(part, AssistantPart::Content(_)));
        self.content.splice(
            insertion_index..insertion_index,
            replacement.into_iter().map(AssistantPart::Content),
        );
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum ToolOutcome {
    Success,
    Failure,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "role", rename_all = "snake_case")]
pub(crate) enum Message {
    System {
        content: Vec<ContentBlock>,
    },
    User {
        content: Vec<ContentBlock>,
    },
    Assistant {
        content: Vec<AssistantPart>,
    },
    Tool {
        tool_call_id: String,
        name: String,
        outcome: ToolOutcome,
        content: Vec<ContentBlock>,
        #[serde(default, rename = "_meta", skip_serializing_if = "Option::is_none")]
        meta: Option<serde_json::Map<String, Value>>,
    },
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum StoredMessageSource {
    GeneratedSystem,
    History,
    Injection,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub(crate) struct StoredMessage {
    pub message: Message,
    pub source: StoredMessageSource,
}

impl Message {
    pub(crate) fn system_text(content: impl Into<String>) -> Self {
        Self::System {
            content: vec![ContentBlock::text(content)],
        }
    }

    pub(crate) fn user(content: Vec<ContentBlock>) -> Self {
        Self::User { content }
    }

    pub(crate) fn user_text(content: impl Into<String>) -> Self {
        Self::user(text_content(content))
    }

    pub(crate) fn assistant(content: Vec<AssistantPart>) -> Self {
        Self::Assistant { content }
    }

    pub(crate) fn tool(
        call_id: String,
        name: String,
        outcome: ToolOutcome,
        content: Vec<ContentBlock>,
        meta: Option<serde_json::Map<String, Value>>,
    ) -> Self {
        Self::Tool {
            tool_call_id: call_id,
            name,
            outcome,
            content,
            meta,
        }
    }

    #[cfg(test)]
    pub(crate) fn tool_success(call_id: String, name: String, content: Vec<ContentBlock>) -> Self {
        Self::tool(call_id, name, ToolOutcome::Success, content, None)
    }

    pub(crate) fn tool_failure(call_id: String, name: String, content: Vec<ContentBlock>) -> Self {
        Self::tool(call_id, name, ToolOutcome::Failure, content, None)
    }

    #[cfg(test)]
    pub(crate) fn tool_success_text(
        call_id: String,
        name: String,
        content: impl Into<String>,
    ) -> Self {
        Self::tool_success(call_id, name, text_content(content))
    }

    pub(crate) fn tool_failure_text(
        call_id: String,
        name: String,
        content: impl Into<String>,
    ) -> Self {
        Self::tool_failure(call_id, name, text_content(content))
    }

    #[cfg(test)]
    pub(crate) fn tool_calls(&self) -> Vec<ToolCall> {
        let Self::Assistant { content } = self else {
            return Vec::new();
        };
        tool_calls_from_parts(content)
    }

    pub(crate) fn validate(&self) -> Result<(), CoreError> {
        match self {
            Self::System { content } => {
                validate_non_empty_content(content, "system message content")?;
                if content
                    .iter()
                    .any(|block| !matches!(block, ContentBlock::Text(_)))
                {
                    return Err(CoreError::invalid_command(
                        "system messages may contain only text blocks",
                    ));
                }
            }
            Self::User { content } => {
                validate_non_empty_content(content, "user message content")?;
            }
            Self::Assistant { content } => {
                if content.is_empty() {
                    return Err(CoreError::invalid_command(
                        "assistant message must contain at least one assistant part",
                    ));
                }
                for part in content {
                    if let AssistantPart::Semantic(AssistantSemanticPart::Reasoning {
                        content, ..
                    }) = part
                        && content.is_empty()
                    {
                        return Err(CoreError::invalid_command(
                            "reasoning part must contain at least one reasoning item",
                        ));
                    }
                }
            }
            Self::Tool { .. } => {}
        }
        Ok(())
    }
}

pub(crate) fn tool_calls_from_parts(parts: &[AssistantPart]) -> Vec<ToolCall> {
    parts
        .iter()
        .filter_map(|part| match part {
            AssistantPart::Semantic(AssistantSemanticPart::ToolCall {
                id,
                name,
                arguments,
                ..
            }) => Some(match arguments {
                ToolArguments::Json { value, .. } => ToolCall {
                    id: id.clone(),
                    name: name.clone(),
                    arguments: value.clone(),
                    argument_error: None,
                },
                ToolArguments::InvalidJson { error, .. } => ToolCall {
                    id: id.clone(),
                    name: name.clone(),
                    arguments: json!({}),
                    argument_error: Some(error.clone()),
                },
            }),
            AssistantPart::Content(_)
            | AssistantPart::Semantic(AssistantSemanticPart::Reasoning { .. }) => None,
        })
        .collect()
}

impl StoredMessage {
    pub(crate) fn visible(message: Message) -> Self {
        Self {
            message,
            source: StoredMessageSource::History,
        }
    }

    pub(crate) fn injected(message: Message) -> Self {
        Self {
            message,
            source: StoredMessageSource::Injection,
        }
    }

    pub(crate) fn generated_system(message: Message) -> Self {
        Self {
            message,
            source: StoredMessageSource::GeneratedSystem,
        }
    }

    pub(crate) fn is_injected(&self) -> bool {
        self.source == StoredMessageSource::Injection
    }

    pub(crate) fn is_generated_system(&self) -> bool {
        self.source == StoredMessageSource::GeneratedSystem
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::testing::*;
    use crate::core::wire::content::{
        Annotations, AudioContent, EmbeddedResource, Icon, IconTheme, ImageContent, MetaObject,
        Resource, ResourceContents, Role as ContentRole, TextContent,
    };

    #[test]
    fn message_content_round_trips_all_block_variants() {
        assert!(
            serde_json::from_value::<Message>(json!({
                "role": "user",
                "content": "legacy text"
            }))
            .is_err()
        );

        let content = vec![
            ContentBlock::Text(
                TextContent::new("text")
                    .with_annotations(
                        serde_json::from_value::<Annotations>(json!({
                            "audience": [ContentRole::Assistant],
                            "priority": 0.9,
                            "lastModified": "2026-07-27T12:00:00Z"
                        }))
                        .expect("annotation fixture is valid MCP"),
                    )
                    .with_meta(MetaObject(serde_json::Map::from_iter([(
                        "block".to_string(),
                        json!(true),
                    )]))),
            ),
            ContentBlock::Image(ImageContent::new("aW1hZ2U=", "image/png")),
            ContentBlock::Audio(AudioContent::new("YXVkaW8=", "audio/wav")),
            ContentBlock::resource_link(
                Resource::new("https://example.com/reference.pdf", "reference.pdf")
                    .with_mime_type("application/pdf")
                    .with_size(42)
                    .with_icons(vec![
                        Icon::new("https://example.com/icon.png")
                            .with_mime_type("image/png")
                            .with_sizes(vec!["48x48".to_string()])
                            .with_theme(IconTheme::Dark),
                    ]),
            ),
            ContentBlock::Resource(EmbeddedResource::new(
                ResourceContents::TextResourceContents {
                    uri: "file:///workspace/notes.txt".to_string(),
                    mime_type: Some("text/plain".to_string()),
                    text: "resource text".to_string(),
                    meta: None,
                },
            )),
            ContentBlock::Resource(EmbeddedResource::new(
                ResourceContents::BlobResourceContents {
                    uri: "file:///workspace/archive.bin".to_string(),
                    mime_type: Some("application/octet-stream".to_string()),
                    blob: "YmxvYg==".to_string(),
                    meta: None,
                },
            )),
        ];
        let message =
            Message::tool_success("tool-1".to_string(), "inspect".to_string(), content.clone());

        let restored: Message =
            serde_json::from_value(serde_json::to_value(message).unwrap()).unwrap();

        assert_eq!(message_content(&restored), content);
    }
}
