use serde::Serialize;

use crate::core::config::ImageDeliveryMode;
use crate::core::error::CoreError;
use crate::core::model_input_projection::{
    project_model_input_content, project_model_input_messages,
};
use crate::core::step_protocol::ToolDefinition;
use crate::core::wire::content::{ContentBlock, strip_model_hidden_content_fields};
use crate::core::wire::message::{AssistantPart, AssistantSemanticPart, Message, StoredMessage};

pub(crate) const APPROX_BYTES_PER_TOKEN: usize = 4;

pub(crate) fn estimate_model_input_tokens(
    messages: &[StoredMessage],
    tools: &[ToolDefinition],
    image_delivery: ImageDeliveryMode,
) -> Result<u64, CoreError> {
    let mut messages = project_model_input_messages(messages, image_delivery);
    for message in &mut messages {
        strip_budget_message_fields(message);
    }
    estimate_serialized_tokens(&(messages, tools), "model input")
}

pub(crate) fn estimate_model_content_tokens(
    block: &ContentBlock,
    label: &str,
    image_delivery: ImageDeliveryMode,
) -> Result<u64, CoreError> {
    let mut block = project_model_input_content(block, image_delivery);
    strip_model_hidden_content_fields(&mut block);
    estimate_serialized_tokens(&block, label)
}

fn strip_budget_message_fields(message: &mut Message) {
    match message {
        Message::System { content } | Message::User { content } => strip_content_fields(content),
        Message::Assistant { content } => {
            for part in content {
                match part {
                    AssistantPart::Content(block) => strip_model_hidden_content_fields(block),
                    AssistantPart::Semantic(
                        AssistantSemanticPart::Reasoning { meta, .. }
                        | AssistantSemanticPart::ToolCall { meta, .. },
                    ) => *meta = None,
                }
            }
        }
        Message::Tool { content, meta, .. } => {
            strip_content_fields(content);
            *meta = None;
        }
    }
}

fn strip_content_fields(content: &mut [ContentBlock]) {
    for block in content {
        strip_model_hidden_content_fields(block);
    }
}

pub(crate) fn estimate_serialized_tokens(
    value: &impl Serialize,
    label: &str,
) -> Result<u64, CoreError> {
    let bytes = serde_json::to_vec(value)
        .map_err(|error| CoreError::invariant(format!("{label} cannot be measured: {error}")))?
        .len();
    Ok(u64::try_from(bytes.div_ceil(APPROX_BYTES_PER_TOKEN)).unwrap_or(u64::MAX))
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn excludes_assistant_and_tool_message_metadata() {
        let metadata = "x".repeat(10_000);
        let with_metadata = messages_with_metadata(Some(&metadata));
        let without_metadata = messages_with_metadata(None);

        assert_ne!(with_metadata, without_metadata);

        assert_eq!(
            estimate_model_input_tokens(&with_metadata, &[], ImageDeliveryMode::Native)
                .expect("model input with metadata serializes"),
            estimate_model_input_tokens(&without_metadata, &[], ImageDeliveryMode::Native)
                .expect("model input without metadata serializes")
        );
    }

    fn messages_with_metadata(metadata: Option<&str>) -> Vec<StoredMessage> {
        let metadata = metadata.map(|value| json!({"provider.example/value": value}));

        vec![
            StoredMessage::visible(
                serde_json::from_value(json!({
                    "role": "assistant",
                    "content": [
                        {
                            "type": "reasoning",
                            "content": [{"type": "text", "text": "thinking"}],
                            "_meta": metadata,
                        },
                        {
                            "type": "tool_call",
                            "id": "call-1",
                            "name": "lookup",
                            "arguments": {"type": "json", "raw": "{}", "value": {}},
                            "_meta": metadata,
                        },
                    ],
                }))
                .expect("assistant message is valid"),
            ),
            StoredMessage::visible(
                serde_json::from_value(json!({
                    "role": "tool",
                    "tool_call_id": "call-1",
                    "name": "lookup",
                    "outcome": "success",
                    "content": [{"type": "text", "text": "result"}],
                    "_meta": metadata,
                }))
                .expect("tool message is valid"),
            ),
        ]
    }
}
