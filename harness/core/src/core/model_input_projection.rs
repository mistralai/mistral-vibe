use crate::core::config::ImageDeliveryMode;
use crate::core::wire::content::ContentBlock;
use crate::core::wire::message::{AssistantPart, Message, StoredMessage};

const FILE_IMAGE_RESOURCE_LINK_META_KEY: &str = "mistralai.vibe.harness/file-image-resource-link";

pub(crate) fn project_model_input_messages(
    messages: &[StoredMessage],
    image_delivery: ImageDeliveryMode,
) -> Vec<Message> {
    messages
        .iter()
        .map(|stored| project_model_input_message(&stored.message, image_delivery))
        .collect()
}

pub(crate) fn project_model_input_content(
    block: &ContentBlock,
    image_delivery: ImageDeliveryMode,
) -> ContentBlock {
    match image_delivery {
        ImageDeliveryMode::Native => block.clone(),
        ImageDeliveryMode::ResourceLink => {
            file_image_resource_link(block).unwrap_or_else(|| block.clone())
        }
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
                if let AssistantPart::Content(block) = part {
                    *block = project_model_input_content(block, image_delivery);
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
        *block = project_model_input_content(block, image_delivery);
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
