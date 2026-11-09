use std::borrow::Cow;

use rmcp::model::ImageContent;

use crate::core::config::ImageDeliveryMode;
use crate::core::wire::content::{ContentBlock, ResourceContents};
use crate::core::wire::message::{AssistantPart, Message, StoredMessage};

/// What a model that takes no image input receives for an image.
pub(crate) const IMAGE_PLACEHOLDER: &str = "[Image]";

/// Returns the messages one completion request sends, decided from the image
/// input of the model that serves it. The stored history is never changed.
pub(crate) fn project_model_input_messages(
    messages: &[StoredMessage],
    image_delivery: ImageDeliveryMode,
) -> Vec<Message> {
    messages
        .iter()
        .map(|stored| project_model_input_message(&stored.message, image_delivery))
        .collect()
}

/// Returns the block a model receives in place of `block`. The same rule
/// holds wherever the block sits, in a message or a tool result:
///
/// - A model that reads images gets an image as is, and an embedded resource
///   that holds an image as that image.
/// - A model without image input gets `[Image]` for an image, and `[Image]`
///   followed by its link text for an embedded image, so it can still open
///   the resource with a tool.
/// - A resource link stays a link for every model, a web image's included.
///
/// The result borrows `block` unless the rule replaces it.
pub(crate) fn project_model_input_content(
    block: &ContentBlock,
    image_delivery: ImageDeliveryMode,
) -> Cow<'_, ContentBlock> {
    match (image_delivery, block) {
        (ImageDeliveryMode::Native, ContentBlock::Resource(resource)) => {
            match embedded_image(&resource.resource) {
                Some((_, blob, mime_type)) => {
                    let mut image = ImageContent::new(blob, mime_type.to_ascii_lowercase());
                    image.annotations = resource.annotations.clone();
                    if let ResourceContents::BlobResourceContents { meta, .. } = &resource.resource
                    {
                        image.meta = meta.clone();
                    }
                    if let Some(outer_meta) = &resource.meta {
                        image
                            .meta
                            .get_or_insert_default()
                            .0
                            .extend(outer_meta.0.clone());
                    }
                    Cow::Owned(ContentBlock::Image(image))
                }
                None => Cow::Borrowed(block),
            }
        }
        (ImageDeliveryMode::WithoutImageInput, ContentBlock::Image(_)) => {
            Cow::Owned(ContentBlock::text(IMAGE_PLACEHOLDER))
        }
        (ImageDeliveryMode::WithoutImageInput, ContentBlock::Resource(resource)) => {
            match embedded_image(&resource.resource) {
                Some((uri, _, _)) => Cow::Owned(ContentBlock::text(format!(
                    "{IMAGE_PLACEHOLDER} [resource: {uri}]"
                ))),
                None => Cow::Borrowed(block),
            }
        }
        _ => Cow::Borrowed(block),
    }
}

/// Whether `block` is an image the model would read as one: an image block, or
/// an embedded resource whose content is an image.
pub(crate) fn is_image_content(block: &ContentBlock) -> bool {
    match block {
        ContentBlock::Image(_) => true,
        ContentBlock::Resource(resource) => embedded_image(&resource.resource).is_some(),
        _ => false,
    }
}

/// The uri, base64 data and media type of an embedded resource that holds an
/// image.
fn embedded_image(resource: &ResourceContents) -> Option<(&str, &str, &str)> {
    match resource {
        ResourceContents::BlobResourceContents {
            uri,
            blob,
            mime_type: Some(mime_type),
            ..
        } if mime_type
            .get(..6)
            .is_some_and(|prefix| prefix.eq_ignore_ascii_case("image/")) =>
        {
            Some((uri, blob, mime_type))
        }
        _ => None,
    }
}

fn project_model_input_message(message: &Message, image_delivery: ImageDeliveryMode) -> Message {
    let mut message = message.clone();
    match &mut message {
        Message::System { content } | Message::User { content } | Message::Tool { content, .. } => {
            for block in content {
                if let Cow::Owned(projected) = project_model_input_content(block, image_delivery) {
                    *block = projected;
                }
            }
        }
        Message::Assistant { content } => {
            for part in content {
                if let AssistantPart::Content(block) = part
                    && let Cow::Owned(projected) =
                        project_model_input_content(block, image_delivery)
                {
                    *block = projected;
                }
            }
        }
    }
    message
}
