use serde::Serialize;

use crate::core::config::{ImageCostRule, ImageDeliveryMode};
use crate::core::error::CoreError;
use crate::core::model_input_projection::project_model_input_messages;
use crate::core::step_protocol::ToolDefinition;
use crate::core::wire::content::{ContentBlock, strip_model_hidden_content_fields};
use crate::core::wire::message::{AssistantPart, AssistantSemanticPart, Message, StoredMessage};

pub(crate) const APPROX_BYTES_PER_TOKEN: usize = 4;

pub(crate) fn estimate_model_input_tokens(
    messages: &[StoredMessage],
    tools: &[ToolDefinition],
    image_delivery: ImageDeliveryMode,
    image_cost: ImageCostRule,
) -> Result<u64, CoreError> {
    let mut messages = project_model_input_messages(messages, image_delivery);
    let mut image_tokens = 0u64;
    for message in &mut messages {
        image_tokens = image_tokens.saturating_add(budget_message(message, image_cost));
    }
    let estimate = estimate_serialized_tokens(&(messages, tools), "model input")?;
    Ok(estimate.saturating_add(image_tokens))
}

/// Prepares one content block for estimation. A native image returns the token
/// cost its image cost rule gives and must be dropped from the budget copy, so
/// the image file size cannot influence the estimate. Any other block has its
/// model-hidden fields stripped and returns `None`.
fn budget_block(block: &mut ContentBlock, image_cost: ImageCostRule) -> Option<u64> {
    if matches!(block, ContentBlock::Image(_)) {
        return Some(image_cost.image_tokens());
    }
    strip_model_hidden_content_fields(block);
    None
}

/// Prepares a message budget copy and returns the token cost of the native
/// images it removed.
fn budget_message(message: &mut Message, image_cost: ImageCostRule) -> u64 {
    let mut image_tokens = 0u64;
    match message {
        Message::System { content } | Message::User { content } => {
            budget_contents(content, image_cost, &mut image_tokens);
        }
        Message::Assistant { content } => content.retain_mut(|part| match part {
            AssistantPart::Content(block) => {
                keep_budgeted_block(block, image_cost, &mut image_tokens)
            }
            AssistantPart::Semantic(
                AssistantSemanticPart::Reasoning { meta, .. }
                | AssistantSemanticPart::ToolCall { meta, .. },
            ) => {
                *meta = None;
                true
            }
        }),
        Message::Tool { content, meta, .. } => {
            budget_contents(content, image_cost, &mut image_tokens);
            *meta = None;
        }
    }
    image_tokens
}

fn budget_contents(
    content: &mut Vec<ContentBlock>,
    image_cost: ImageCostRule,
    image_tokens: &mut u64,
) {
    content.retain_mut(|block| keep_budgeted_block(block, image_cost, image_tokens));
}

/// Returns whether the block stays in the budget copy, adding the cost of a
/// removed image to `image_tokens`.
fn keep_budgeted_block(
    block: &mut ContentBlock,
    image_cost: ImageCostRule,
    image_tokens: &mut u64,
) -> bool {
    let Some(tokens) = budget_block(block, image_cost) else {
        return true;
    };
    *image_tokens = image_tokens.saturating_add(tokens);
    false
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

    const TEST_IMAGE_COST: ImageCostRule = ImageCostRule::Fixed { tokens: 8_192 };

    #[test]
    fn excludes_assistant_and_tool_message_metadata() {
        let metadata = "x".repeat(10_000);
        let with_metadata = messages_with_metadata(Some(&metadata));
        let without_metadata = messages_with_metadata(None);

        assert_ne!(with_metadata, without_metadata);

        assert_eq!(
            estimate_model_input_tokens(
                &with_metadata,
                &[],
                ImageDeliveryMode::Native,
                TEST_IMAGE_COST
            )
            .expect("model input with metadata serializes"),
            estimate_model_input_tokens(
                &without_metadata,
                &[],
                ImageDeliveryMode::Native,
                TEST_IMAGE_COST
            )
            .expect("model input without metadata serializes")
        );
    }

    #[test]
    fn fixed_image_cost_is_independent_of_image_size() {
        let small = user_message_with_image(&"a".repeat(1_000));
        let large = user_message_with_image(&"a".repeat(5_000_000));

        let small_estimate =
            estimate_model_input_tokens(&[small], &[], ImageDeliveryMode::Native, TEST_IMAGE_COST)
                .expect("small image model input serializes");
        let large_estimate =
            estimate_model_input_tokens(&[large], &[], ImageDeliveryMode::Native, TEST_IMAGE_COST)
                .expect("large image model input serializes");

        assert_eq!(small_estimate, large_estimate);
    }

    #[test]
    fn an_embedded_image_costs_its_image_cost_not_its_base64_length() {
        let embedded = |blob_len: usize| -> ContentBlock {
            serde_json::from_value(json!({
                "type": "resource",
                "resource": {
                    "uri": "file:///workspace/shot.png",
                    "mimeType": "image/png",
                    "blob": "a".repeat(blob_len),
                },
            }))
            .expect("embedded image is valid")
        };

        let estimate = |blob_len: usize| {
            let message = StoredMessage::visible(Message::user(vec![embedded(blob_len)]));
            estimate_model_input_tokens(&[message], &[], ImageDeliveryMode::Native, TEST_IMAGE_COST)
                .expect("embedded image is measured")
        };

        let small = estimate(1_000);
        assert_eq!(small, estimate(5_000_000));
        assert!(small >= TEST_IMAGE_COST.image_tokens());
        assert!(small < TEST_IMAGE_COST.image_tokens() + 100, "{small}");
    }

    #[test]
    fn fixed_image_cost_charges_the_constant_per_native_image() {
        let without_images = user_message_with_image_data(0);
        let one_image = user_message_with_image_data(1);
        let two_images = user_message_with_image_data(2);

        let without_estimate = estimate_model_input_tokens(
            &[without_images],
            &[],
            ImageDeliveryMode::Native,
            TEST_IMAGE_COST,
        )
        .expect("image-free model input serializes");
        let one_estimate = estimate_model_input_tokens(
            &[one_image],
            &[],
            ImageDeliveryMode::Native,
            TEST_IMAGE_COST,
        )
        .expect("single-image model input serializes");
        let two_estimate = estimate_model_input_tokens(
            &[two_images],
            &[],
            ImageDeliveryMode::Native,
            TEST_IMAGE_COST,
        )
        .expect("two-image model input serializes");

        assert_eq!(
            one_estimate - without_estimate,
            TEST_IMAGE_COST.image_tokens()
        );
        assert_eq!(two_estimate - one_estimate, TEST_IMAGE_COST.image_tokens());
    }

    #[test]
    fn each_native_image_costs_what_the_rule_says() {
        let one_image = user_message_with_image_data(1);
        let estimate = |tokens| {
            estimate_model_input_tokens(
                std::slice::from_ref(&one_image),
                &[],
                ImageDeliveryMode::Native,
                ImageCostRule::Fixed { tokens },
            )
            .expect("single-image model input serializes")
        };

        assert_eq!(estimate(9_000) - estimate(1_000), 8_000);
    }

    fn user_message_with_image(data: &str) -> StoredMessage {
        user_message_with_images(data, 1)
    }

    fn user_message_with_image_data(images: usize) -> StoredMessage {
        user_message_with_images(&"a".repeat(1_000), images)
    }

    fn user_message_with_images(data: &str, images: usize) -> StoredMessage {
        let content = (0..images)
            .map(|_| {
                json!({
                    "type": "image",
                    "data": data,
                    "mimeType": "image/png",
                })
            })
            .chain(std::iter::once(
                json!({"type": "text", "text": "look at this"}),
            ))
            .collect::<Vec<_>>();
        StoredMessage::visible(
            serde_json::from_value(json!({
                "role": "user",
                "content": content,
            }))
            .expect("user message is valid"),
        )
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
