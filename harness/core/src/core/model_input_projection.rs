use std::borrow::Cow;

use crate::core::config::ImageDeliveryMode;
use crate::core::wire::content::ContentBlock;
use crate::core::wire::message::{AssistantPart, Message, StoredMessage};

const FILE_IMAGE_RESOURCE_LINK_META_KEY: &str = "mistralai.vibe.harness/file-image-resource-link";
/// What a model without image input receives for an image that has no file link.
const IMAGE_PLACEHOLDER: &str = "[image]";

pub(crate) fn project_model_input_messages(
    messages: &[StoredMessage],
    image_delivery: ImageDeliveryMode,
) -> Vec<Message> {
    messages
        .iter()
        .map(|stored| project_model_input_message(&stored.message, image_delivery))
        .collect()
}

/// Returns the block the model receives in place of `block`. Under
/// `ResourceLink` delivery no image block reaches the model: an image becomes
/// its file link, or the `[image]` text placeholder when it has none. The
/// result borrows `block` unless image delivery replaces it.
pub(crate) fn project_model_input_content(
    block: &ContentBlock,
    image_delivery: ImageDeliveryMode,
) -> Cow<'_, ContentBlock> {
    match (image_delivery, block) {
        (ImageDeliveryMode::ResourceLink, ContentBlock::Image(_)) => Cow::Owned(
            file_image_resource_link(block)
                .unwrap_or_else(|| ContentBlock::text(IMAGE_PLACEHOLDER)),
        ),
        _ => Cow::Borrowed(block),
    }
}

fn project_model_input_message(message: &Message, image_delivery: ImageDeliveryMode) -> Message {
    let mut message = message.clone();
    match &mut message {
        Message::System { content } | Message::User { content } => {
            project_content(content, image_delivery);
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
        Message::Tool { content, .. } => {
            project_content(content, image_delivery);
        }
    }
    message
}

fn project_content(content: &mut [ContentBlock], image_delivery: ImageDeliveryMode) {
    for block in content {
        if let Cow::Owned(projected) = project_model_input_content(block, image_delivery) {
            *block = projected;
        }
    }
}

fn file_image_resource_link(block: &ContentBlock) -> Option<ContentBlock> {
    let ContentBlock::Image(image) = block else {
        return None;
    };
    let value = image
        .meta
        .as_ref()?
        .0
        .get(FILE_IMAGE_RESOURCE_LINK_META_KEY)?
        .clone();
    let resource_link = serde_json::from_value::<ContentBlock>(value).ok()?;
    match &resource_link {
        ContentBlock::ResourceLink(resource) if resource.uri.starts_with("file:") => {
            Some(resource_link)
        }
        _ => None,
    }
}
