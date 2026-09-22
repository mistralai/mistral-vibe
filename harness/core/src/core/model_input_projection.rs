use rmcp::model::MetaObject;
use serde_json::Value;

use crate::core::config::ImageDeliveryMode;
use crate::core::wire::content::{ContentBlock, strip_model_hidden_content_fields};
use crate::core::wire::message::{AssistantPart, Message, StoredMessage};

const FILE_IMAGE_RESOURCE_LINK_META_KEY: &str = "mistralai.vibe.harness/file-image-resource-link";
const REMOTE_IMAGE_URL_META_KEY: &str = "mistralai.vibe.harness/remote-image-url";

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
    let mut block = if image_delivery == ImageDeliveryMode::ResourceLink {
        file_image_resource_link(block).unwrap_or_else(|| block.clone())
    } else {
        block.clone()
    };
    let materialization_meta = provider_materialization_meta(&block);
    strip_model_hidden_content_fields(&mut block);
    if let ContentBlock::ResourceLink(resource) = &mut block {
        resource.meta = materialization_meta;
    }
    block
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
        Message::Tool { content, meta, .. } => {
            project_content(content, image_delivery);
            *meta = None;
        }
    }
    message
}

fn project_content(content: &mut [ContentBlock], image_delivery: ImageDeliveryMode) {
    for block in content {
        *block = project_model_input_content(block, image_delivery);
    }
}

fn provider_materialization_meta(block: &ContentBlock) -> Option<MetaObject> {
    let ContentBlock::ResourceLink(resource) = block else {
        return None;
    };
    if resource.meta.as_ref()?.0.get(REMOTE_IMAGE_URL_META_KEY) != Some(&Value::Bool(true)) {
        return None;
    }
    let mut meta = MetaObject::new();
    meta.0
        .insert(REMOTE_IMAGE_URL_META_KEY.to_owned(), Value::Bool(true));
    Some(meta)
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

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn projection_preserves_assistant_continuation_metadata() {
        let message = serde_json::from_value::<Message>(json!({
            "role": "assistant",
            "content": [
                {
                    "type": "reasoning",
                    "content": [{"type": "text", "text": "thinking"}],
                    "_meta": {"signature": "reasoning-signature"}
                },
                {
                    "type": "tool_call",
                    "id": "call-1",
                    "name": "lookup",
                    "arguments": {"type": "json", "raw": "{}", "value": {}},
                    "_meta": {"provider_call_id": "provider-call-1"}
                }
            ]
        }))
        .unwrap();

        let projected = project_model_input_message(&message, ImageDeliveryMode::Native);

        assert_eq!(projected, message);
    }

    #[test]
    fn projection_keeps_only_remote_image_resource_link_metadata() {
        let message = serde_json::from_value::<Message>(json!({
            "role": "user",
            "content": [
                {
                    "type": "resource_link",
                    "uri": "https://example.test/image.png",
                    "name": "image.png",
                    "annotations": {"audience": ["assistant"]},
                    "_meta": {
                        "mistralai.vibe.harness/remote-image-url": true,
                        "private": "remove"
                    }
                },
                {
                    "type": "resource_link",
                    "uri": "https://example.test/report.pdf",
                    "name": "report.pdf",
                    "_meta": {"private": "remove"}
                }
            ]
        }))
        .unwrap();

        let projected = project_model_input_message(&message, ImageDeliveryMode::Native);

        let Message::User { content } = projected else {
            panic!("projection changed the message role");
        };
        let ContentBlock::ResourceLink(image) = &content[0] else {
            panic!("projection changed the remote image block");
        };
        assert_eq!(image.annotations, None);
        assert_eq!(
            image.meta.as_ref().map(|meta| &meta.0),
            Some(&serde_json::Map::from_iter([(
                REMOTE_IMAGE_URL_META_KEY.to_owned(),
                Value::Bool(true),
            )]))
        );
        let ContentBlock::ResourceLink(resource) = &content[1] else {
            panic!("projection changed the resource-link block");
        };
        assert_eq!(resource.meta, None);
    }
}
