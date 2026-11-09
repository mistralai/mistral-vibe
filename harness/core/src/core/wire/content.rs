use crate::core::error::CoreError;

#[cfg(test)]
pub(crate) use rmcp::model::{
    Annotations, AudioContent, EmbeddedResource, Icon, IconTheme, ImageContent, MetaObject,
    Resource, TextContent,
};
pub(crate) use rmcp::model::{ContentBlock, ResourceContents, Role};

pub(crate) fn text_content(text: impl Into<String>) -> Vec<ContentBlock> {
    vec![ContentBlock::text(text)]
}

pub(crate) fn validate_non_empty_content(
    content: &[ContentBlock],
    label: &str,
) -> Result<(), CoreError> {
    if content.is_empty() {
        return Err(CoreError::invalid_command(format!(
            "{label} must contain at least one content block"
        )));
    }
    Ok(())
}

pub(crate) fn content_blocks_to_text(
    content: &[ContentBlock],
    label: &str,
) -> Result<String, CoreError> {
    validate_non_empty_content(content, label)?;
    let text = content
        .iter()
        .map(|block| match block {
            ContentBlock::Text(content) => content.text.clone(),
            block => serde_json::to_string(block).unwrap_or_else(|_| format!("{block:?}")),
        })
        .collect::<Vec<_>>()
        .join("\n");
    if text.trim().is_empty() {
        return Err(CoreError::invalid_command(format!(
            "{label} must contain non-empty text"
        )));
    }
    Ok(text)
}

pub(crate) fn strip_model_hidden_content_fields(block: &mut ContentBlock) {
    let (annotations, meta) = match block {
        ContentBlock::Text(content) => (&mut content.annotations, &mut content.meta),
        ContentBlock::Image(content) => (&mut content.annotations, &mut content.meta),
        ContentBlock::Audio(content) => (&mut content.annotations, &mut content.meta),
        ContentBlock::ResourceLink(resource) => (&mut resource.annotations, &mut resource.meta),
        ContentBlock::Resource(content) => (&mut content.annotations, &mut content.meta),
        _ => return,
    };
    *annotations = None;
    *meta = None;
    if let ContentBlock::Resource(content) = block {
        match &mut content.resource {
            ResourceContents::TextResourceContents { meta, .. }
            | ResourceContents::BlobResourceContents { meta, .. } => *meta = None,
            _ => {}
        }
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn content_blocks_use_mcp_field_names_and_variants() {
        let blocks = serde_json::from_value::<Vec<ContentBlock>>(json!([
            {
                "type": "audio",
                "data": "YXVkaW8=",
                "mimeType": "audio/wav",
                "annotations": {"audience": ["assistant"], "lastModified": "2026-08-07"}
            },
            {
                "type": "resource_link",
                "uri": "resource://report",
                "name": "report",
                "mimeType": "application/pdf",
                "icons": [{"src": "resource://icon", "mimeType": "image/png"}]
            },
            {
                "type": "resource",
                "resource": {
                    "uri": "resource://text",
                    "mimeType": "text/plain",
                    "text": "hello"
                }
            }
        ]))
        .unwrap();

        assert!(matches!(
            &blocks[0],
            ContentBlock::Audio(AudioContent {
                mime_type,
                annotations: Some(Annotations { last_modified, .. }),
                ..
            }) if mime_type == "audio/wav" && last_modified.as_deref() == Some("2026-08-07")
        ));
        assert!(matches!(
            &blocks[1],
            ContentBlock::ResourceLink(Resource {
                mime_type,
                icons: Some(icons),
                ..
            }) if mime_type.as_deref() == Some("application/pdf")
                && icons[0].mime_type.as_deref() == Some("image/png")
        ));
        assert!(matches!(
            &blocks[2],
            ContentBlock::Resource(EmbeddedResource {
                resource: ResourceContents::TextResourceContents { mime_type, .. },
                ..
            }) if mime_type.as_deref() == Some("text/plain")
        ));
    }
}
